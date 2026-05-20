import fitz  # PyMuPDF
import json
import re
import shutil
from pathlib import Path

# 現在の構成に合わせた出力先
RAW_DIR = Path("raw_data")
PUBLIC_DIR = Path("public")
PDF_OUT_DIR = PUBLIC_DIR / "pdfs"
DATA_JSON = PUBLIC_DIR / "data.json"

# Cloudflare Pages は Build output directory の直下に index.html が必要
ROOT_INDEX_HTML = Path("index.html")
PUBLIC_INDEX_HTML = PUBLIC_DIR / "index.html"

NR_RE = re.compile(r"^\d{3}/\d{2}$")
DATE_RE = re.compile(r"(\d{8})")


# -----------------------------
# 共通ユーティリティ
# -----------------------------
def normalize_text(text: str) -> str:
    """改行や連続空白をならして、判定や表示を安定させる。"""
    return re.sub(r"\s+", " ", text).strip()


def clean_title_line(line: str) -> str:
    """PDFから抽出したタイトル行を表示しやすい形に整える。"""
    line = line.replace("\u3000", " ")
    line = re.sub(r"\s+", " ", line).strip()
    return line


def is_ascii_title_char(char: str) -> bool:
    """和文タイトル内の英数字語句を自然につなぐための簡易判定。"""
    return bool(re.match(r"[A-Za-z0-9)\]％%°./-]", char))


def smart_join_ja_title(lines: list[str]) -> str:
    """
    日本語タイトルの改行を自然につなぐ。

    PDFでは「について」が「につい / て」のように分割されることがあるため、
    日本語同士は空白なしで連結する。
    一方で "Target / Start" のような英数字語句の改行は空白を入れる。
    """
    result = ""

    for line in lines:
        line = clean_title_line(line)
        if not line:
            continue

        if not result:
            result = line
            continue

        prev = result[-1]
        first = line[0]

        if is_ascii_title_char(prev) and is_ascii_title_char(first):
            result += " " + line
        else:
            result += line

    return normalize_text(result)


def join_title_lines(lines: list[str], side: str = "ja") -> str:
    """複数行タイトルを1行にまとめる。"""
    cleaned = [clean_title_line(line) for line in lines]
    cleaned = [line for line in cleaned if line]

    if side == "ja":
        return smart_join_ja_title(cleaned)

    return normalize_text(" ".join(cleaned))


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


# -----------------------------
# PDF検出・抽出処理
# -----------------------------
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


def extract_title_from_block(page: fitz.Page, nr: str, side: str) -> str:
    """
    タイトルブロックからタイトルを抽出する。

    side='ja' は左側の日本語タイトル、side='en' は右側の英語タイトルを対象にする。
    本文中の NR や取消対象NRを拾わないよう、NRを含むタイトルブロックだけを見る。
    """
    if side == "ja":
        x_min, x_max = 45, 315
        # 和文タイトルは長い場合でも y=190pt 付近までに収まることが多い
        y_min, y_max = 120, 205
    else:
        x_min, x_max = 305, 570
        y_min, y_max = 120, 210

    candidates: list[tuple[float, str]] = []

    for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
        # 対象側・タイトル付近のブロックに限定
        if not (x0 >= x_min and x0 <= x_max and y0 <= y_max and y1 >= y_min):
            continue

        raw_lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in raw_lines if line.strip()]

        # このブロックに対象NRが無い場合はタイトルブロックではない
        if nr not in lines and nr not in text:
            continue

        title_lines: list[str] = []
        nr_seen = False

        for line in lines:
            clean = clean_title_line(line)

            # 単独の NR 行を見つけた後の行をタイトルとして扱う
            if NR_RE.fullmatch(clean):
                if clean == nr:
                    nr_seen = True
                    continue

            if nr_seen:
                # 念のため、次の番号・日付らしき行が来たら止める
                if NR_RE.fullmatch(clean):
                    break
                if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", clean):
                    break
                title_lines.append(clean)

        title = join_title_lines(title_lines, side)
        if title:
            # タイトルブロックとして自然な位置のものを優先
            score = 100 - abs(y0 - 135)
            if side == "ja" and x0 < 120:
                score += 20
            if side == "en" and 310 <= x0 <= 330:
                score += 20
            candidates.append((score, title))

    if not candidates:
        return ""

    candidates.sort(reverse=True)
    return candidates[0][1]


def extract_title_from_words(page: fitz.Page, nr: str, side: str) -> str:
    """
    ブロック抽出でタイトルが取れない場合の予備処理。
    タイトル付近の単語を行ごとにまとめる。
    """
    if side == "ja":
        x_min, x_max = 45, 315
    else:
        x_min, x_max = 305, 570

    words_by_line: dict[float, list[tuple[float, str]]] = {}
    nr_line_y: float | None = None

    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        if not (x_min <= x0 <= x_max and 130 <= y0 <= 190):
            continue

        # y座標を丸めて同じ行として扱う
        line_y = round(y0 / 2) * 2

        if word == nr:
            nr_line_y = line_y
            continue

        words_by_line.setdefault(line_y, []).append((x0, word))

    if nr_line_y is None:
        return ""

    title_lines: list[str] = []
    for line_y in sorted(words_by_line.keys()):
        # NR行より下、本文開始より上の行だけをタイトル候補にする
        if line_y <= nr_line_y:
            continue
        if line_y > 180:
            continue

        words = [word for _, word in sorted(words_by_line[line_y])]
        line = clean_title_line(" ".join(words))
        if line and not NR_RE.fullmatch(line):
            title_lines.append(line)

    return join_title_lines(title_lines, side)


def extract_titles(page: fitz.Page, nr: str) -> dict[str, str]:
    """文書1ページ目から日本語タイトル・英語タイトルを抽出する。"""
    title_ja = extract_title_from_block(page, nr, "ja")
    if not title_ja:
        title_ja = extract_title_from_words(page, nr, "ja")

    title_en = extract_title_from_block(page, nr, "en")
    if not title_en:
        title_en = extract_title_from_words(page, nr, "en")

    # 表示用の代表タイトル。日本語を優先し、取れない場合は英語にする。
    title = title_ja or title_en

    return {
        "title": title,
        "title_ja": title_ja,
        "title_en": title_en,
    }


def find_sections(doc: fitz.Document, category: str) -> list[dict]:
    """
    結合PDF内の各文書の開始・終了ページを検出する。
    ページ番号は PyMuPDF に合わせて 0 始まり。
    """
    sections: list[dict] = []
    current_nr: str | None = None
    current_titles: dict[str, str] = {"title": "", "title_ja": "", "title_en": ""}
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
                "title": current_titles.get("title", ""),
                "title_ja": current_titles.get("title_ja", ""),
                "title_en": current_titles.get("title_en", ""),
                "start": start_page,
                "end": page_index - 1,
            })

        current_nr = nr
        current_titles = extract_titles(page, nr)
        start_page = page_index

    if current_nr is not None and start_page is not None:
        sections.append({
            "nr": current_nr,
            "title": current_titles.get("title", ""),
            "title_ja": current_titles.get("title_ja", ""),
            "title_en": current_titles.get("title_en", ""),
            "start": start_page,
            "end": len(doc) - 1,
        })

    return sections


# -----------------------------
# 出力処理
# -----------------------------
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

            item = {
                "nr": nr,
                "title": section.get("title", ""),
                "title_ja": section.get("title_ja", ""),
                "title_en": section.get("title_en", ""),
                "path": f"pdfs/{file_name}",
                # 確認用。不要なら削除してもOK。
                "pages": f"{start + 1}-{end + 1}",
            }
            web_data[category][formatted_date].append(item)

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


def write_data_json(web_data: dict) -> None:
    """public/data.json を書き出す。"""
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)

    with DATA_JSON.open("w", encoding="utf-8") as f:
        json.dump(sort_web_data(web_data), f, ensure_ascii=False, indent=2)

    print(f"DONE: {DATA_JSON}")


# -----------------------------
# Cloudflare Pages 用 index.html
# -----------------------------
def build_fallback_index_html() -> str:
    """
    index.html がリポジトリ直下にも public 内にも無い場合の最低限のトップページ。
    通常はリポジトリ直下の index.html を public/index.html にコピーして使う。
    """
    return """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aviation Information Hub</title>
  <style>
    body {
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 900px;
      margin: 0 auto;
      padding: 24px;
      color: #222;
      background: #fff;
    }
    h1 {
      font-size: 24px;
      font-weight: 600;
      margin: 0 0 16px;
      color: #075a9c;
      border-bottom: 2px solid #075a9c;
      padding-bottom: 12px;
    }
    .section {
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      padding: 16px;
      margin: 16px 0;
    }
    .date {
      font-weight: 700;
      margin-top: 16px;
    }
    .item {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      padding: 10px 0;
      border-bottom: 1px solid #eee;
    }
    .item:last-child {
      border-bottom: 0;
    }
    .nr-title {
      display: flex;
      gap: 10px;
      align-items: baseline;
      min-width: 0;
    }
    .nr {
      flex: 0 0 auto;
      font-weight: 700;
    }
    .title {
      color: #555;
      line-height: 1.5;
    }
    a {
      color: #075a9c;
      text-decoration: none;
      font-weight: 600;
      white-space: nowrap;
    }
    @media (max-width: 560px) {
      .item {
        align-items: flex-start;
        flex-direction: column;
      }
      .nr-title {
        display: block;
      }
      .title {
        margin-top: 4px;
      }
    }
  </style>
</head>
<body>
  <h1>航空情報 AIC / AIP SUP</h1>
  <div id="app">読み込み中...</div>

  <script>
    function escapeHTML(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    async function main() {
      const app = document.getElementById("app");

      try {
        const res = await fetch("data.json", { cache: "no-store" });
        const data = await res.json();

        if (!Object.keys(data).length) {
          app.textContent = "表示できるPDFデータがありません。";
          return;
        }

        app.innerHTML = "";

        for (const [category, dates] of Object.entries(data)) {
          const section = document.createElement("section");
          section.className = "section";
          section.innerHTML = `<h2>${escapeHTML(category)}</h2>`;

          for (const [date, items] of Object.entries(dates)) {
            const dateEl = document.createElement("div");
            dateEl.className = "date";
            dateEl.textContent = date;
            section.appendChild(dateEl);

            for (const item of items) {
              const row = document.createElement("div");
              row.className = "item";

              const title = item.title_ja || item.title || item.title_en || "";

              row.innerHTML = `
                <span class="nr-title">
                  <span class="nr">NR ${escapeHTML(item.nr)}</span>
                  <span class="title">${escapeHTML(title)}</span>
                </span>
                <a href="${escapeHTML(item.path)}" target="_blank" rel="noopener">PDFを開く</a>
              `;
              section.appendChild(row);
            }
          }

          app.appendChild(section);
        }
      } catch (error) {
        app.textContent = "data.json の読み込みに失敗しました。";
      }
    }

    main();
  </script>
</body>
</html>
"""


def ensure_public_index_html() -> None:
    """
    Cloudflare Pages のトップページ 404 対策。
    Build output directory が public の場合、public/index.html が無いと / が 404 になる。
    """
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

    if ROOT_INDEX_HTML.exists():
        shutil.copy2(ROOT_INDEX_HTML, PUBLIC_INDEX_HTML)
        print(f"OK: copied {ROOT_INDEX_HTML} -> {PUBLIC_INDEX_HTML}")
        return

    if PUBLIC_INDEX_HTML.exists():
        print(f"OK: {PUBLIC_INDEX_HTML} already exists")
        return

    PUBLIC_INDEX_HTML.write_text(build_fallback_index_html(), encoding="utf-8")
    print(f"WARNING: {ROOT_INDEX_HTML} was not found. Created fallback {PUBLIC_INDEX_HTML}")


def main() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    PDF_OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 先に index.html を用意することで、PDF処理側に問題があってもトップページの404を避ける
    ensure_public_index_html()

    web_data: dict = {}

    if not RAW_DIR.exists():
        print(f"ERROR: raw_data folder was not found: {RAW_DIR}")
        write_data_json(web_data)
        return

    pdf_files = sorted([p for p in RAW_DIR.iterdir() if p.suffix.lower() == ".pdf"])

    if not pdf_files:
        print("WARNING: no PDF files found in raw_data")
        write_data_json(web_data)
        return

    for pdf_path in pdf_files:
        category = detect_category(pdf_path.name)
        process_pdf(category, pdf_path, PDF_OUT_DIR, web_data)

    write_data_json(web_data)


if __name__ == "__main__":
    main()
