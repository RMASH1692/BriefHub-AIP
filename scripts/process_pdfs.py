import fitz  # PyMuPDF
import os
import json
import re

# タイトルに含めないキーワード
SKIP_KEYWORDS = [
    "また、新情報", "及び変更部", "表示される", "JAPAN", "MINISTRY OF", 
    "CIVIL AVIATION", "AIP SUP", "NR", "AERONAUTICAL", "Changes are", 
    "This AIP", "Tel:", "Fax:", "E-mail", "helpdesk", "AFTN:", "ページ",
    "取り消す", "再発行", "斜体文字", "太い縦線", "ATTACHMENT", "参照"
]

def is_body_text(text):
    """句点（。）が含まれる、または特定の動詞で終わる場合は『本文』とみなす"""
    text = text.strip()
    if "。" in text: return True
    if text.endswith("される") or text.endswith("実施される"): return True
    # 箇条書きや英語の開始も本文とみなす
    if text.startswith("・") or re.match(r'^[a-zA-Z]{2,}', text): return True
    return False

def is_valid_title_part(text):
    """行がタイトルの一部として適切か判定"""
    text = text.strip()
    if not text or len(text) < 2: return False
    if any(k in text for k in SKIP_KEYWORDS): return False
    # 完全に英語・数字・記号だけの行は、日本語タイトルの終わりとみなす
    if re.fullmatch(r'[a-zA-Z\s\(\)\d\.,\-/!&]+', text): return False
    return True

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
        
        # ヘッダー領域にあるNRを特定 (y座標が250以下のもの)
        found_nr_on_page = None
        for b in blocks:
            x0, y0, x1, y1, text, block_no, block_type = b
            # NR 000/00 の形式を厳格にチェック
            nr_search = re.search(r'^NR\s*(\d{3}/\d{2})$|^\s*(\d{3}/\d{2})\s*$', text.strip())
            if nr_search and y1 < 250:
                found_nr_on_page = nr_search.group(1) or nr_search.group(2)
                break
        
        if found_nr_on_page:
            new_nr = found_nr_on_page
            if current_nr and new_nr != current_nr:
                save_split_pdf(doc, start_page, page_num - 1, category, date_str, current_nr, output_dir)
                web_data[category][formatted_date].append({
                    "nr": current_nr, "title": current_title.strip(),
                    "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
                })

            current_nr = new_nr
            start_page = page_num
            current_title = ""

            # --- タイトル抽出ロジック（本文巻き込み防止） ---
            found_title_lines = []
            # y座標がNR(y0)より下、かつ ページ中央(y=500)より上のブロックを調査
            for b in sorted(blocks, key=lambda x: x[1]): # 上から順に
                if b[1] < 100: continue # NRより上の連絡先などは無視
                if b[1] > 500: break    # ページ下部は無視
                
                txt_block = b[4].strip()
                if current_nr in txt_block or any(k in txt_block for k in SKIP_KEYWORDS):
                    continue
                
                # ブロック内の各行をチェック
                stop_collecting = False
                for line in txt_block.split('\n'):
                    line = line.strip()
                    if is_body_text(line):
                        # 本文（句点など）が出てきたら、その行の「。」の前までをタイトルにするか、そこで打ち切る
                        if "。" in line:
                            potential_end = line.split("。")[0]
                            if is_valid_title_part(potential_end) and len(potential_end) > 2:
                                found_title_lines.append(potential_end)
                        stop_collecting = True
                        break
                    
                    if is_valid_title_part(line):
                        found_title_lines.append(line)
                
                if stop_collecting or (found_title_lines and b[1] > 400):
                    break
            
            current_title = " ".join(found_title_lines) if found_title_lines else "No Title Detected"

    if current_nr:
        save_split_pdf(doc, start_page, len(doc) - 1, category, date_str, current_nr, output_dir)
        web_data[category][formatted_date].append({
            "nr": current_nr, "title": current_title.strip(),
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

# 実行
os.makedirs("public/pdfs", exist_ok=True)
web_data = {}
if os.path.exists("raw_data"):
    for f in os.listdir("raw_data"):
        if f.lower().endswith(".pdf"):
            cat = "AIP SUP" if "SUP" in f.upper() else "AIC"
            process_pdf(cat, os.path.join("raw_data", f), "public/pdfs", web_data)

with open("public/data.json", "w", encoding="utf-8") as j:
    json.dump(web_data, j, ensure_ascii=False, indent=2)
