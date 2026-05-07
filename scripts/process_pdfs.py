import fitz  # PyMuPDF
import os
import json
import re

# タイトルとして抽出しないキーワード
SKIP_KEYWORDS = [
    "また、新情報", "及び変更部", "表示される", "JAPAN", "MINISTRY OF", 
    "CIVIL AVIATION", "AIP SUP", "NR", "AERONAUTICAL", "Changes are", 
    "This AIP", "Tel:", "Fax:", "E-mail", "helpdesk", "AFTN:", "ページ",
    "取り消す", "再発行", "斜体文字", "太い縦線", "期間:", "Period:", "位置:", "Position:"
]

def is_valid_title_part(text):
    """行がタイトルの一部として適切か（日本語を含み、禁止語句でないか）を判定"""
    text = text.strip()
    if not text or len(text) < 2: return False
    # 禁止キーワードが含まれていたらNG
    if any(k in text for k in SKIP_KEYWORDS): return False
    # 記号や数字だけの行、または完全に英語だけの行（英語タイトル）は日本語タイトルの終わりとみなす
    if re.fullmatch(r'[a-zA-Z\s\(\)\d\.,\-/!&]+', text): return False
    return True

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
    current_title = ""
    start_page = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        # ページ上部（ヘッダーエリア）のみを対象にNRを探す（y=300以下程度）
        # 座標取得のために get_text("blocks") を使用
        blocks = page.get_text("blocks")
        
        # ページ内のテキストをリスト化
        all_lines = [b[4].strip() for b in blocks if b[4].strip()]
        
        # ヘッダー領域にあるNRを特定
        found_nr_on_page = None
        for b in blocks:
            x0, y0, x1, y1, text, block_no, block_type = b
            # y1 < 250 (上部約1/4) の位置にある NR XXX/XX を探す
            nr_search = re.search(r'(?:NR\s?)?(\d{3}/\d{2})', text)
            if nr_search and y1 < 250:
                found_nr_on_page = nr_search.group(1)
                break
        
        if found_nr_on_page:
            new_nr = found_nr_on_page
            
            # 既にNRを処理中の場合、新しいNRが出たら前のを保存
            if current_nr and new_nr != current_nr:
                save_split_pdf(doc, start_page, page_num - 1, category, date_str, current_nr, output_dir)
                web_data[category][formatted_date].append({
                    "nr": current_nr, "title": current_title,
                    "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
                })

            # 新しいセクションの開始
            current_nr = new_nr
            start_page = page_num
            current_title = ""

            # --- タイトル抽出（複数行対応） ---
            # NRが見つかった箇所以降のブロックをスキャン
            found_first_line = False
            for b in blocks:
                # NRが見つかった行、あるいはその後の上部エリアのテキストをタイトル候補にする
                txt = b[4].strip()
                # y座標がNRと同じか少し下、かつ本文(y>500)より上
                if b[1] > 100 and b[3] < 500:
                    lines_in_block = [l.strip() for l in txt.split('\n') if l.strip()]
                    for line in lines_in_block:
                        # NRそのものや、明らかにタイトルでない行はスキップ
                        if current_nr in line or any(k in line for k in SKIP_KEYWORDS):
                            continue
                        
                        if is_valid_title_part(line):
                            if not current_title:
                                current_title = line
                            else:
                                current_title += " " + line
                            found_first_line = True
                        elif found_first_line:
                            # 一度タイトルを拾い始めた後、無効な行（英語等）が出たら終了
                            break
                    if found_first_line and not is_valid_title_part(txt.split('\n')[-1]):
                        break

    # 最後のセクションを保存
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
