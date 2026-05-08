import fitz  # PyMuPDF
import os
import json
import re

def process_pdf(category, input_path, output_dir, web_data):
    try:
        doc = fitz.open(input_path)
    except Exception as e:
        print(f"Error opening {input_path}: {e}")
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
    start_page = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("blocks")
        
        found_nr_on_page = None
        for b in blocks:
            x0, y0, x1, y1, text, block_no, block_type = b
            # y1 < 300 (ページ上部) で "数字3桁/数字2桁" を探す
            nr_match = re.search(r'(\d{3}/\d{2})', text)
            if nr_match and y1 < 300:
                found_nr_on_page = nr_match.group(1)
                break
        
        if found_nr_on_page:
            new_nr = found_nr_on_page
            # 新しいNRが見つかったら前のセクションを保存
            if current_nr and new_nr != current_nr:
                save_split_pdf(doc, start_page, page_num - 1, category, date_str, current_nr, output_dir)
                web_data[category][formatted_date].append({
                    "nr": current_nr,
                    "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
                })

            current_nr = new_nr
            start_page = page_num

    # 最後のNRセクションを保存
    if current_nr:
        save_split_pdf(doc, start_page, len(doc) - 1, category, date_str, current_nr, output_dir)
        web_data[category][formatted_date].append({
            "nr": current_nr,
            "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
        })

def save_split_pdf(doc, start, end, cat, date, nr, out_dir):
    new_doc = fitz.open()
    s, e = max(0, start), min(len(doc)-1, end)
    if s <= e:
        new_doc.insert_pdf(doc, from_page=s, to_page=e)
        filename = f"{cat.replace(' ', '_')}_{date}_{nr.replace('/', '-')}.pdf"
        new_doc.save(os.path.join(out_dir, filename))
    new_doc.close()

# メイン処理
os.makedirs("public/pdfs", exist_ok=True)
web_data = {}
if os.path.exists("raw_data"):
    for f in os.listdir("raw_data"):
        if f.lower().endswith(".pdf"):
            cat = "AIP SUP" if "SUP" in f.upper() else "AIC"
            process_pdf(cat, os.path.join("raw_data", f), "public/pdfs", web_data)

with open("public/data.json", "w", encoding="utf-8") as j:
    json.dump(web_data, j, ensure_ascii=False, indent=2)
