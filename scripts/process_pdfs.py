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

    # ファイル名から日付を取得 (例: SUP_20260514.pdf -> 2026/05/14)
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
        # NR番号の検索
        nr_match = re.search(r'NR\s?(\d{3}/\d{2})', text)
        
        if nr_match:
            new_nr = nr_match.group(1)
            
            # 新しいNRが見つかったら前のセクションを保存
            if current_nr and new_nr != current_nr:
                save_split_pdf(doc, start_page, page_num - 1, category, date_str, current_nr, output_dir)
                web_data[category][formatted_date].append({
                    "nr": current_nr,
                    "title": current_title,
                    "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
                })

            current_nr = new_nr
            start_page = page_num
            
            # タイトルの抽出ロジック（NR番号の後の最初の意味のある行を探す）
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            for i, line in enumerate(lines):
                if current_nr in line:
                    # 次の行が英語や日付でなければタイトルとみなす
                    if i + 1 < len(lines):
                        current_title = lines[i+1]
                    break

    # 最後のセクションを保存
    if current_nr:
        save_split_pdf(doc, start_page, len(doc) - 1, category, date_str, current_nr, output_dir)
        web_data[category][formatted_date].append({
            "nr": current_nr,
            "title": current_title,
            "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
        })

def save_split_pdf(doc, start, end, cat, date, nr, out_dir):
    new_doc = fitz.open()
    new_doc.insert_pdf(doc, from_page=start, to_page=end)
    safe_cat = cat.replace(' ', '_')
    safe_nr = nr.replace('/', '-')
    filename = f"{safe_cat}_{date}_{safe_nr}.pdf"
    new_doc.save(os.path.join(out_dir, filename))
    new_doc.close()

# メイン処理
os.makedirs("public/pdfs", exist_ok=True)
web_data = {}
raw_dir = "raw_data"

if os.path.exists(raw_dir):
    for f in os.listdir(raw_dir):
        if f.lower().endswith(".pdf"):
            cat = "AIP SUP" if "SUP" in f.upper() else "AIC"
            process_pdf(cat, os.path.join(raw_dir, f), "public/pdfs", web_data)

with open("public/data.json", "w", encoding="utf-8") as j:
    json.dump(web_data, j, ensure_ascii=False, indent=2)
