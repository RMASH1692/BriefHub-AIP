import fitz  # PyMuPDF
import os
import json
import re

# タイトルとして抽出しないキーワード（定型文や連絡先など）
SKIP_KEYWORDS = [
    "また、新情報", "及び変更部", "表示される", "JAPAN", "MINISTRY OF", 
    "CIVIL AVIATION", "AIP SUP", "NR", "AERONAUTICAL", "Changes are", 
    "This AIP", "Tel:", "Fax:", "E-mail", " helpdesk", "AFTN:", "ページ",
    "取り消す", "再発行", "斜体文字", "太い縦線"
]

def is_valid_title(text):
    """行がタイトルとして適切かどうかを判定する"""
    text = text.strip()
    if not text or len(text) < 3: return False
    # あまりに長い行（本文）は除外
    if len(text) > 100: return False
    # 除外キーワードが含まれていないかチェック
    if any(k in text for k in SKIP_KEYWORDS): return False
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
        lines = [l.strip() for l in page.get_text().split('\n') if l.strip()]
        
        for i, line in enumerate(lines):
            # NR番号のパターン（例: 013/19 または NR 013/19）
            nr_match = re.search(r'(?:NR\s?)?(\d{3}/\d{2})(.*)', line)
            
            if nr_match:
                new_nr = nr_match.group(1)
                same_line_text = nr_match.group(2).strip()
                
                # 新しいNRセクションの開始
                if current_nr and new_nr != current_nr:
                    save_split_pdf(doc, start_page, page_num - 1, category, date_str, current_nr, output_dir)
                    web_data[category][formatted_date].append({
                        "nr": current_nr, "title": current_title,
                        "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
                    })

                current_nr = new_nr
                start_page = page_num
                
                # --- タイトル抽出ロジックの改善 ---
                # 1. 同じ行にテキストがあればそれを優先
                if is_valid_title(same_line_text):
                    current_title = same_line_text
                else:
                    # 2. 次の数行を探して、最初に見つかった有効な日本語（っぽい）行をタイトルにする
                    current_title = "No Title Detected"
                    for j in range(i + 1, min(i + 6, len(lines))):
                        candidate = lines[j]
                        if is_valid_title(candidate):
                            # 日本語が含まれているか、またはアルファベットのみでないものを優先
                            if re.search(r'[^\x00-\x7F]+', candidate):
                                current_title = candidate
                                break
                            elif current_title == "No Title Detected":
                                current_title = candidate
                break # 1ページ内に複数のNRがあるケースは稀だが、最初の検知でそのページの処理を抜ける

    # 最後のセクションを保存
    if current_nr:
        save_split_pdf(doc, start_page, len(doc) - 1, category, date_str, current_nr, output_dir)
        web_data[category][formatted_date].append({
            "nr": current_nr, "title": current_title,
            "path": f"pdfs/{category.replace(' ', '_')}_{date_str}_{current_nr.replace('/', '-')}.pdf"
        })

def save_split_pdf(doc, start, end, cat, date, nr, out_dir):
    new_doc = fitz.open()
    # startがendより大きくなるエラーを防止
    actual_start = max(0, start)
    actual_end = min(len(doc)-1, end)
    if actual_start <= actual_end:
        new_doc.insert_pdf(doc, from_page=actual_start, to_page=actual_end)
        filename = f"{cat.replace(' ', '_')}_{date}_{nr.replace('/', '-')}.pdf"
        new_doc.save(os.path.join(out_dir, filename))
    new_doc.close()

# 実行環境のセットアップ
os.makedirs("public/pdfs", exist_ok=True)
web_data = {}
if os.path.exists("raw_data"):
    for f in os.listdir("raw_data"):
        if f.lower().endswith(".pdf"):
            cat = "AIP SUP" if "SUP" in f.upper() else "AIC"
            process_pdf(cat, os.path.join("raw_data", f), "public/pdfs", web_data)

with open("public/data.json", "w", encoding="utf-8") as j:
    json.dump(web_data, j, ensure_ascii=False, indent=2)
