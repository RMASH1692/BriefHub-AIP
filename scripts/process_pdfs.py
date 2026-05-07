import fitz  # PyMuPDF
import os
import json
import re

# タイトルとして抽出しない除外キーワード
SKIP_KEYWORDS = [
    "また、新情報", "及び変更部", "表示される", "JAPAN", "MINISTRY OF", 
    "CIVIL AVIATION", "AIP SUP", "NR", "18 JUN", "AERONAUTICAL",
    "Changes are", "This AIP", "Tel:", "Fax:", "E-mail"
]

def process_pdf(category, input_path, output_dir, web_data):
    try:
        doc = fitz.open(input_path)
    except Exception as e:
        print(f"Error: {e}")
        return

    file_name = os.path.basename(input_path)
    date_match = re.search(r'\d{8}', file_name)
    date_str = date_match.group(0) if date_match else "00000000"
    formatted_date = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"

    if category not in web_data:
        web_data[category] = {}
    if formatted_date not in web_data[category]:
        web_data[category][formatted_date] = []

    current_nr = None
    current_title = ""
    start_page = 0

    for page_num in range(len(doc)):
        text = doc[page_num].get_text()
        nr_match = re.search(r'NR\s?(\d{3}/\d{2})', text)
        
        if nr_match:
            new_nr = nr_match.group(1)
            if current_nr and new_nr != current_nr:
                save_split_pdf(doc, start_page, page_num - 1, category, date_str, current_nr, output_dir)
                web_data[category][formatted_date].append({
                    "nr": current_nr, "title": current_title,
                    "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
                })

            current_nr = new_nr
            start_page = page_num
            
            # --- 改良されたタイトル抽出ロジック ---
            lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 2]
            found_title = False
            for i, line in enumerate(lines):
                if current_nr in line:
                    # NRの後の数行をチェックし、除外キーワードを含まない最初の行をタイトルにする
                    for j in range(i + 1, min(i + 8, len(lines))):
                        potential = lines[j]
                        if not any(k in potential for k in SKIP_KEYWORDS):
                            current_title = potential
                            found_title = True
                            break
                if found_title: break
            if not found_title: current_title = "No Title Detected"

    if current_nr:
        save_split_pdf(doc, start_page, len(doc) - 1, category, date_str, current_nr, output_dir)
        web_data[category][formatted_date].append({
            "nr": current_nr, "title": current_title,
            "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
        })

def save_split_pdf(doc, start, end, cat, date, nr, out_dir):
    new_doc = fitz.open()
    new_doc.insert_pdf(doc, from_page=start, to_page=end)
    filename = f"{cat.replace(' ', '_')}_{date}_{nr.replace('/', '-')}.pdf"
    new_doc.save(os.path.join(out_dir, filename))
    new_doc.close()

os.makedirs("public/pdfs", exist_ok=True)
web_data = {}
if os.path.exists("raw_data"):
    for f in os.listdir("raw_data"):
        if f.lower().endswith(".pdf"):
            cat = "AIP SUP" if "SUP" in f.upper() else "AIC"
            process_pdf(cat, os.path.join("raw_data", f), "public/pdfs", web_data)

with open("public/data.json", "w", encoding="utf-8") as j:
    json.dump(web_data, j, ensure_ascii=False, indent=2)
