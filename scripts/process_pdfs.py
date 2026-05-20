import fitz  # PyMuPDF
import os
import json
import re
from pathlib import Path

# 現在の構成に合わせた出力先
RAW_DIR = Path("raw_data")
PUBLIC_DIR = Path("public")
PDF_OUT_DIR = PUBLIC_DIR / "pdfs"
DATA_JSON = PUBLIC_DIR / "data.json"

NR_RE = re.compile(r"^\d{3}/\d{2}$")
DATE_RE = re.compile(r"(\d{8})")


def normalize_text(text: str) -> str:
    """改行や連続空白をならして、ヘッダー判定を安定させる。"""
    return re.sub(r"\s+", " ", text).strip()


def get_issue_date_from_filename(input_path: Path) -> tuple[str, str]:
    """
    ファイル名から YYYYMMDD を取り出し、data.json 用の日付も作る。
    例: SUP_20260514.pdf -> ("20260514", "2026/05/14")
    """
    m = DATE_RE.search(input_path.name)
    if not m:
        return "00000000", "0000/00/00"

    date_str = m.group(1)
    formatted_date = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"
    return date_str, formatted_date


def detect_category(file_name: str) -> str:
    """ファイル名からカテゴリを判定する。"""
    upper = file_name.upper()
    if "SUP" in upper:
        return "AIP SUP"
    return "AIC"


def has_document_header(page: fitz.Page, category: str) -> bool:
    """
    そのページが各PDF内の「新しい文書の1ページ目」かを判定する。

    改修ポイント:
    - 単に 000/00 形式を探すのではなく、上部ヘッダーに
      'AERONAUTICAL INFORMATION SERVICE CENTER' と 'AIP SUP' / 'AIC' があるかを見る。
    - これにより、本文中の 'NR049/23' や NOTAM の '000/007' などを誤検出しにくくする。
    """
    top_text_parts = []

    for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
        # 1ページ目の共通ヘッダーは上部 130pt 程度に収まる
        if y1 <= 130:
            top_text_parts.append(text)

    top_text = normalize_text("\n".join(top_text_parts))

    has_center_header = "AERONAUTICAL INFORMATION SERVICE CENTER" in top_text

    if category == "AIP SUP":
        has_type_header = "AIP SUP" in top_text
    else:
        has_type_header = bool(re.search(r"\bAIC\b", top_text))

    return has_center_header and has_type_header


def extract_nr_from_title_area(page: fitz.Page) -> str | None:
    """
    1ページ目のタイトル付近から NR を取得する。

    AIC / AIP SUP は、本文上部の左右どちらにも 000/00 形式の番号が出るため、
    y=115〜185pt 付近の単独の 000/00 だけを候補にする。
    本文中の 'NR049/23' や NOTAM の Q-line などはここでは拾わない。
    """
    candidates: list[tuple[float, str]] = []

    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        if not NR_RE.fullmatch(word):
            continue

        # タイトル番号が置かれる範囲に限定
        if not (115 <= y0 <= 185):
            continue

        score = 0.0

        # 左側の和文タイトル番号を最優先
        if 60 <= x0 <= 130:
            score += 100

        # 右側の英文タイトル番号も候補にする
        if 300 <= x0 <= 370:
            score += 50

        # 一般的なタイトル番号の縦位置に近いほど優先
        score -= abs(y0 - 135)

        candidates.append((score, word))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def find_sections(doc: fitz.Document, category: str) -> list[dict]:
    """
    結合PDF内の各文書の開始・終了ページを検出する。
    ページ番号は PyMuPDF に合わせて 0 始まり。
    """
    sections: list[dict] = []
    current_nr: str | None = None
    start_page: int | None = None

    for page_index in range(len(doc)):
        page = doc[page_index]

        if not has_document_header(page, category):
            continue

        nr = extract_nr_from_title_area(page)
        if not nr:
            print(f"WARNING: header found but NR was not detected. page={page_index + 1}")
            continue

        if current_nr is not None and start_page is not None:
            sections.append({
                "nr": current_nr,
                "start": start_page,
                "end": page_index - 1,
            })

        current_nr = nr
        start_page = page_index

    if current_nr is not None and start_page is not None:
        sections.append({
            "nr": current_nr,
            "start": start_page,
            "end": len(doc) - 1,
        })

    return sections


def save_split_pdf(doc: fitz.Document, start: int, end: int, output_path: Path) -> None:
    """指定ページ範囲を別PDFとして保存する。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    new_doc = fitz.open()
    try:
        new_doc.insert_pdf(doc, from_page=start, to_page=end)
        new_doc.save(output_path, deflate=True, garbage=4)
    finally:
        new_doc.close()


def build_pdf_filename(category: str, date_str: str, nr: str) -> str:
    """既存HTMLと互換性のあるPDFファイル名を作る。"""
    safe_category = category.replace(" ", "_")
    safe_nr = nr.replace("/", "-")
    return f"{safe_category}_{date_str}_{safe_nr}.pdf"


def process_pdf(category: str, input_path: Path, output_dir: Path, web_data: dict) -> None:
    """1つの結合PDFを NR ごとに切り分け、data.json 用データを追加する。"""
    try:
        doc = fitz.open(input_path)
    except Exception as e:
        print(f"ERROR: opening {input_path}: {e}")
        return

    try:
        date_str, formatted_date = get_issue_date_from_filename(input_path)

        web_data.setdefault(category, {})
        web_data[category].setdefault(formatted_date, [])

        sections = find_sections(doc, category)

        if not sections:
            print(f"WARNING: no sections detected: {input_path}")
            return

        seen_nr: set[str] = set()

        for section in sections:
            nr = section["nr"]
            start = section["start"]
            end = section["end"]

            if nr in seen_nr:
                print(f"WARNING: duplicate NR detected in {input_path.name}: {nr}")
            seen_nr.add(nr)

            file_name = build_pdf_filename(category, date_str, nr)
            output_path = output_dir / file_name

            save_split_pdf(doc, start, end, output_path)

            web_data[category][formatted_date].append({
                "nr": nr,
                "path": f"pdfs/{file_name}",
                # 確認用。不要なら削除してもOK。
                "pages": f"{start + 1}-{end + 1}",
            })

        print(f"OK: {input_path.name} -> {len(sections)} files")

    finally:
        doc.close()


def sort_web_data(web_data: dict) -> dict:
    """data.json の表示順を安定させる。"""
    sorted_data = {}

    for category in sorted(web_data.keys()):
        sorted_data[category] = {}
        for date in sorted(web_data[category].keys(), reverse=True):
            sorted_data[category][date] = web_data[category][date]

    return sorted_data


def main() -> None:
    PDF_OUT_DIR.mkdir(parents=True, exist_ok=True)
    web_data: dict = {}

    if not RAW_DIR.exists():
        print(f"ERROR: raw_data folder was not found: {RAW_DIR}")
        return

    pdf_files = sorted([p for p in RAW_DIR.iterdir() if p.suffix.lower() == ".pdf"])

    if not pdf_files:
        print("WARNING: no PDF files found in raw_data")
        return

    for pdf_path in pdf_files:
        category = detect_category(pdf_path.name)
        process_pdf(category, pdf_path, PDF_OUT_DIR, web_data)

    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    with DATA_JSON.open("w", encoding="utf-8") as f:
        json.dump(sort_web_data(web_data), f, ensure_ascii=False, indent=2)

    print(f"DONE: {DATA_JSON}")


if __name__ == "__main__":
    main()
