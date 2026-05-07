import fitz  # PyMuPDF
import os
import json
import re

# ヘッダーとして無視する文字列
IGNORE_TEXTS = ["JAPAN", "AIP SUP", "AIC", "NR", "TEL:", "FAX:", "E-MAIL"]

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
        page = doc[page_num]
        blocks = page.get_text("blocks")
        # y座標でソート（上から順に処理）
        blocks.sort(key=lambda b: b[1])
        
        found_nr_on_page = None
        for b in blocks:
            x0, y0, x1, y1, text, block_no, block_type = b
            text = text.strip()
            
            # ページ上部(y < 300)で "数字3桁/数字2桁" を探す（一番最初に見つかったものを採用）
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
                    "nr": current_nr, "title": current_title,
                    "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
                })

            current_nr = new_nr
            start_page = page_num
            
            # --- タイトル抽出（シンプル版） ---
            title_parts = []
            for b in blocks:
                txt = b[4].strip().replace('\n', ' ')
                # y座標がNRブロックより下で、かつ本文エリア(y > 500)より上のもの
                if b[1] > 100 and b[3] < 500:
                    # 無視リストに入っている単語、またはNR番号自体はスキップ
                    if any(ig in txt.upper() for ig in IGNORE_TEXTS) or current_nr in txt:
                        continue
                    
                    # 句点（。）や、特定のキーワードが出たら本文とみなして終了
                    if "。" in txt or "1." in txt or "期間" in txt or "Period" in txt:
                        # 句点がある場合、その手前までをタイトルに加える
                        if "。" in txt:
                            title_parts.append(txt.split("。")[0])
                        break
                    
                    # 日本語が含まれている場合のみタイトルとして採用
                    if re.search(r'[^\x00-\x7F]+', txt):
                        title_parts.append(txt)
                
                # タイトルが2つ以上のブロックに渡ることは稀なので、2つ拾ったら安全のために終了
                if len(title_parts) >= 2:
                    break
            
            current_title = " ".join(title_parts) if title_parts else "No Title"

    # 最後のNRセクションを保存
    if current_nr:
        save_split_pdf(doc, start_page, len(doc) - 1, category, date_str, current_nr, output_dir)
        web_data[category][formatted_date].append({
            "nr": current_nr, "title": current_title,
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
