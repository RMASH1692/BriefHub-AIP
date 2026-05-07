import fitz  # PyMuPDF
import os
import json
import re

def process_pdf(category, input_path, output_dir, web_data):
    doc = fitz.open(input_path)
    # ファイル名から日付を取得 (例: SUP_20260514.pdf -> 2026/05/14)
    file_date = re.search(r'\d{8}', input_path)
    date_str = file_date.group(0) if file_date else "Unknown"
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
        # NR番号の検索 (例: 098/20 または NR098/20)
        nr_match = re.search(r'(?:NR)?(\d{3}/\d{2})', text)
        
        if nr_match:
            new_nr = nr_match.group(1)
            # 新しいNRが見つかったら前のページまでを保存
            if current_nr and new_nr != current_nr:
                save_split_pdf(doc, start_page, page_num - 1, category, date_str, current_nr, output_dir)
                web_data[category][formatted_date].append({
                    "nr": current_nr,
                    "title": current_title,
                    "path": f"pdfs/{category}_{date_str}_{current_nr.replace('/', '-')}.pdf"
                })

            # 新しいセクションの初期化
            current_nr = new_nr
            start_page = page_num
            # タイトルの簡易抽出 (NRの後の数行を取得)
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            for i, line in enumerate(lines):
                if current_nr in line:
                    # NRの次の行付近をタイトルと仮定（日本語を優先）
                    current_title = lines[i+1] if i+1 < len(lines) else "No Title"
                    break

    # 最後のセクションを保存
    if current_nr:
        save_split_pdf(doc, start_page, len(doc) - 1, category, date_str, current_nr, output_dir)
        web_data[category][formatted_date].append({
            "nr": current_nr,
            "title": current_title,
            "path": f"pdfs/{category}_{date_str}_{current_nr.replace('/', '-')}.pdf"
        })

def save_split_pdf(doc, start, end, cat, date, nr, out_dir):
    new_doc = fitz.open()
    new_doc.insert_pdf(doc, from_page=start, to_page=end)
    safe_nr = nr.replace('/', '-')
    filename = f"{cat}_{date}_{safe_nr}.pdf"
    new_doc.save(os.path.join(out_dir, filename))
    new_doc.close()

# 実行
os.makedirs("public/pdfs", exist_ok=True)
data = {}
for f in os.listdir("raw_data"):
    if f.endswith(".pdf"):
        cat = "AIP SUP" if "SUP" in f.upper() else "AIC"
        process_pdf(cat, os.path.join("raw_data", f), "public/pdfs", data)

with open("public/data.json", "w", encoding="utf-8") as j:
    json.dump(data, j, ensure_ascii=False, indent=2)
