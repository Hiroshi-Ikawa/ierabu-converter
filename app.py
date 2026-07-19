from __future__ import annotations

import io
import re
import csv
import json
import unicodedata
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_file

try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    import pykakasi as _pykakasi
    _kks = _pykakasi.kakasi()
    KAKASI_AVAILABLE = True
except ImportError:
    KAKASI_AVAILABLE = False

try:
    import easyocr as _easyocr_test  # noqa: F401
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB


# ---------------------------------------------------------------------------
# フォーマット定義レジストリ
# ---------------------------------------------------------------------------
FORMAT_REGISTRY = [
    {
        "id": "ierabu_passthrough",
        "name": "いえらぶ形式CSV（パススルー）",
        "match": lambda name: False,  # 内容ベースのみで判定
        "type": "csv",
    },
    {
        "id": "fb",
        "name": "FB引落データ（エポス・ジェイリース等）",
        "match": lambda name: re.match(r"^FB_\d{8}_", name) is not None,
        "type": "csv",
    },
    {
        "id": "safety_csv",
        "name": "日本セーフティー保証引落CSV",
        "match": lambda name: bool(re.match(r"^(いえらふ|いえらぶ|イエラフ|イエラブ)_+", name)),
        "type": "csv",
    },
    {
        "id": "rakuten",
        "name": "楽天保証収納 支店・営業所単位CSV",
        "match": lambda name: re.match(r"^\d{6}_", name) is not None and name.lower().endswith(".csv"),
        "type": "csv",
    },
    {
        "id": "safety_pdf",
        "name": "日本セーフティー送金明細PDF",
        "match": lambda name: "セーフティ" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "rakuten_bank",
        "name": "楽天銀行 取引明細（全銀形式）",
        "match": lambda name: "zengin" in name.lower() and name.lower().endswith(".csv"),
        "type": "csv",
    },
    # ---- 以下、新規追加フォーマット ----
    {
        "id": "elzs_pdf",
        "name": "集金代行/LACTii送金明細PDF（エルズ系）",
        "match": lambda name: (
            "集金代行送金明細" in name or "LACTii送金明細" in name
        ) and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "nap_pdf",
        "name": "ナップ収納代行送金明細PDF",
        "match": lambda name: "ナップ" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "premialife_pdf",
        "name": "プレミアライフ家賃送金明細PDF",
        "match": lambda name: "プレミアライフ" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "arc_pdf",
        "name": "アーク賃貸保証 定時送金PDF",
        "match": lambda name: "アーク" in name and "定時送金" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "jid_pdf",
        "name": "日本賃貸保証(JID) 送金予定明細PDF",
        "match": lambda name: "SoukinMeisai" in name and "JID" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "jrag_pdf",
        "name": "日本賃貸住宅保証機構 送金明細PDF",
        "match": lambda name: "日本賃貸住宅保証機構" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "epos_pdf",
        "name": "エポスカード 家賃精算額一覧PDF",
        "match": lambda name: "エポス" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "fair_pdf",
        "name": "フェア信用保証 収納代行送金PDF（スキャン）",
        "match": lambda name: "フェア" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "orico_pdf",
        "name": "オリコフォレントインシュア PDF（スキャン）",
        "match": lambda name: "オリコ" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "capco_pdf",
        "name": "CAPCOエージェンシー お支払明細書PDF（スキャン）",
        "match": lambda name: re.match(r'^\d{14}\.pdf$', name) is not None,
        "type": "pdf",
    },
    {
        "id": "orico_csv",
        "name": "オリコフォレントインシュア 家賃明細CSV",
        "match": lambda name: "オリコ" in name and name.lower().endswith(".csv"),
        "type": "csv",
    },
    {
        "id": "zenhoren_pdf",
        "name": "全保連 振替精算書PDF",
        "match": lambda name: "reportpdf_exchangeadjust" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "fourseasons_pdf",
        "name": "フォーシーズ 集金代行PDF",
        "match": lambda name: "フォーシーズ" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
    {
        "id": "casa_pdf",
        "name": "カーサ/リコーリース 送金明細PDF",
        "match": lambda name: "カーサ" in name and name.lower().endswith(".pdf"),
        "type": "pdf",
    },
]


# ---------------------------------------------------------------------------
# 日付変換ヘルパー
# ---------------------------------------------------------------------------
def to_yyyymmdd(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        return value
    m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", value)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    m = re.fullmatch(r"(\d{6})", value)
    if m:
        return value + "01"
    return ""


def has_kanji(text: str) -> bool:
    return bool(re.search(r'[一-鿿㐀-䶿]', text))


def kanji_to_katakana(text: str) -> str:
    if not text or not has_kanji(text):
        return text
    if not KAKASI_AVAILABLE:
        return text
    result = _kks.convert(text)
    return "".join(item["kana"] for item in result)


def clean_amount(value: str) -> int | None:
    cleaned = re.sub(r"[,，\s¥￥]", "", unicodedata.normalize("NFKC", str(value).strip()))
    if "－" in cleaned or cleaned in ("", "-"):
        return None
    try:
        amount = int(float(cleaned))
        return amount if amount > 0 else None
    except (ValueError, TypeError):
        return None


def is_life_advance(text: str) -> bool:
    """ライフアドバンス宛（全角・半角どちらでも）かどうか判定する。"""
    return "ライフアドバンス" in text or "ﾗｲﾌｱﾄﾞﾊﾞﾝｽ" in text


def decode_csv_bytes(raw: bytes) -> str:
    for enc in ("shift_jis", "cp932", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("shift_jis", errors="replace")


def extract_pdf_text(raw: bytes) -> str:
    """PDF全ページのテキストを結合して返す。"""
    if not PDF_AVAILABLE:
        return ""
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


# ---------------------------------------------------------------------------
# OCR ヘルパー（スキャンPDF用、遅延ロード）
# ---------------------------------------------------------------------------
_ocr_reader = None


def get_ocr_reader():
    if not OCR_AVAILABLE:
        raise RuntimeError(
            "このフォーマットはOCR機能が必要ですが、クラウド版では利用できません。"
            "スキャンPDFの代わりにCSVをお使いください。"
        )
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        _ocr_reader = easyocr.Reader(['ja', 'en'], gpu=False, verbose=False)
    return _ocr_reader


def ocr_pdf_pages(raw: bytes) -> list[list[tuple]]:
    """スキャンPDFを各ページOCRし、[(x, y, text, conf), ...] のリストで返す。"""
    import numpy as np
    reader = get_ocr_reader()
    all_pages = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            img = page.to_image(resolution=200)
            arr = np.array(img.original)
            results = reader.readtext(arr, detail=1)
            words = [
                (float(bbox[0][0]), float(bbox[0][1]), text, float(conf))
                for bbox, text, conf in results
                if conf > 0.3
            ]
            all_pages.append(words)
    return all_pages


def group_ocr_by_row(words: list[tuple], y_tol: int = 18) -> list[list[tuple]]:
    """y座標でワードをグループ化してテキスト行を再構成する。"""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w[1])
    rows: list[list[tuple]] = []
    current_row = [sorted_words[0]]
    current_y = sorted_words[0][1]
    for word in sorted_words[1:]:
        if abs(word[1] - current_y) <= y_tol:
            current_row.append(word)
        else:
            rows.append(sorted(current_row, key=lambda w: w[0]))
            current_row = [word]
            current_y = word[1]
    rows.append(sorted(current_row, key=lambda w: w[0]))
    return rows


# ---------------------------------------------------------------------------
# フォーマット別変換関数
# ---------------------------------------------------------------------------

def check_columns(fieldnames, required: list[str], filename: str) -> None:
    if fieldnames is None:
        raise ValueError("CSVのヘッダー行が読み取れませんでした。文字コードを確認してください。")
    missing = [c for c in required if c not in fieldnames]
    if missing:
        found = "、".join(str(f) for f in fieldnames)
        raise ValueError(
            f"列名が一致しません。\n"
            f"  不足列: {' / '.join(missing)}\n"
            f"  実際の列: {found}"
        )


def convert_fb(raw: bytes, filename: str) -> list[dict]:
    text = decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text))
    check_columns(reader.fieldnames, ["振替日", "請求額", "引落口座名義"], filename)
    rows = []
    for row in reader:
        date = to_yyyymmdd(row.get("振替日", "").strip())
        amount = clean_amount(row.get("請求額", ""))
        if amount is None:
            continue
        kana = row.get("引落口座名義", "").strip()
        rows.append({
            "勘定日": date,
            "金額": amount,
            "振込依頼人コード": row.get("顧客番号", "").strip(),
            "振込依頼人カナ": kanji_to_katakana(kana),
        })
    return rows


def convert_safety_csv(raw: bytes, filename: str) -> list[dict]:
    text = decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text))
    check_columns(reader.fieldnames, ["振替日", "振替内訳（賃料等）", "保証番号", "契約者名カナ", "送金先名"], filename)
    rows = []
    for row in reader:
        destination = row.get("送金先名", "").replace("　", "").replace(" ", "")
        if not is_life_advance(destination):
            continue
        raw_date = row.get("振替日", "").strip()
        date = "" if raw_date == "振替前" else to_yyyymmdd(raw_date)
        amount = clean_amount(row.get("振替内訳（賃料等）", ""))
        if amount is None:
            continue
        kana = row.get("契約者名カナ", "").strip()
        rows.append({
            "勘定日": date,
            "金額": amount,
            "振込依頼人コード": row.get("保証番号", "").strip(),
            "振込依頼人カナ": kanji_to_katakana(kana),
        })
    return rows


def convert_rakuten(raw: bytes, filename: str) -> list[dict]:
    text = decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text))
    check_columns(reader.fieldnames, ["請求年月", "賃料等", "契約者名カナ（半）"], filename)
    rows = []
    for row in reader:
        date = to_yyyymmdd(row.get("請求年月", "").strip())
        amount = clean_amount(row.get("賃料等", ""))
        if amount is None:
            continue
        kana = row.get("契約者名カナ（半）", "").strip()
        rows.append({
            "勘定日": date,
            "金額": amount,
            "振込依頼人コード": row.get("企業コード", "").strip(),
            "振込依頼人カナ": kanji_to_katakana(kana),
        })
    return rows


def reiwa_yymmdd_to_yyyymmdd(yymmdd: str) -> str:
    yymmdd = yymmdd.strip()
    if len(yymmdd) != 6:
        return ""
    reiwa_year = int(yymmdd[0:2])
    western_year = reiwa_year + 2018
    return f"{western_year}{yymmdd[2:]}"


def convert_rakuten_bank(raw: bytes, filename: str) -> list[dict]:
    text = decode_csv_bytes(raw)
    reader = csv.reader(io.StringIO(text))
    rows = []
    for cols in reader:
        if len(cols) < 15:
            continue
        if cols[0].strip() != "2":
            continue
        if cols[4].strip() != "1":
            continue
        date = reiwa_yymmdd_to_yyyymmdd(cols[2])
        amount = clean_amount(cols[6])
        if amount is None:
            continue
        kana = cols[14].strip()
        rows.append({
            "勘定日": date,
            "金額": amount,
            "振込依頼人コード": "",
            "振込依頼人カナ": kana,
        })
    return rows


def convert_safety_pdf(raw: bytes, filename: str) -> list[dict]:
    """日本セーフティー送金明細PDF（テキスト版 → OCR fallback）"""
    if not PDF_AVAILABLE:
        raise RuntimeError("pdfplumber がインストールされていません")

    rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if not (is_life_advance(full_text) or "7678903" in full_text):
            return rows
        date_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", full_text)
        send_date = ""
        if date_match:
            send_date = f"{date_match.group(1)}{int(date_match.group(2)):02d}{int(date_match.group(3)):02d}"

        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue
                header_str = " ".join(str(c) for c in table[0] if c)
                if "物件名称" not in header_str and "号室" not in header_str:
                    continue
                pending = None
                for row in table[1:]:
                    if not row:
                        continue
                    if pending is not None and (row[0] is None or str(row[0]).strip() == ""):
                        contractor = str(row[1]).strip() if row[1] else ""
                        rows.append({
                            "勘定日": pending["date"],
                            "金額": pending["amount"],
                            "振込依頼人コード": pending["code"],
                            "振込依頼人カナ": kanji_to_katakana(contractor),
                        })
                        pending = None
                        continue
                    code = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                    amount_raw = str(row[4]).strip() if len(row) > 4 and row[4] else ""
                    amount = clean_amount(amount_raw)
                    if amount is None:
                        pending = None
                        continue
                    pending = {"date": send_date, "amount": amount, "code": code}

    if rows:
        return rows

    # テキスト抽出できなかった場合はOCR fallback
    return _convert_safety_pdf_ocr(raw, send_date or "")


def _extract_date_from_ocr(page_words: list[tuple]) -> str:
    """OCRワードリストから送金日を抽出（y順にスキャンして最初の有効日付を返す）"""
    sorted_words = sorted(page_words, key=lambda w: w[1])
    all_text = " ".join(w[2] for w in sorted_words)

    # 年/月/日パターン (YYYY年MM月DD日 or YYYY/MM/DD)
    # (\d{2}) で日を2桁固定にしてOCR誤読("28H"→"8"など)を防ぐ
    for pattern in [
        r'(\d{4})[年/](\d{1,2})[月/](\d{1,2})',
        r'(\d{4}).{0,2}(\d{1,2}).{0,2}月.{0,1}(\d{2})',
    ]:
        for m in re.finditer(pattern, all_text):
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2020 <= y <= 2040 and 1 <= mo <= 12 and 1 <= d <= 31:
                    return f"{y}{mo:02d}{d:02d}"
            except Exception:
                pass
    return ""


def _convert_safety_pdf_ocr(raw: bytes, send_date: str) -> list[dict]:
    """スキャン版セーフティPDFのOCR抽出（精度注意）"""
    pages = ocr_pdf_pages(raw)
    rows = []

    for page_words in pages:
        # 送金日を探す
        if not send_date:
            send_date = _extract_date_from_ocr(page_words)

        # y座標でグループ化してテーブル行を再構成
        row_groups = group_ocr_by_row(page_words, y_tol=20)
        for row_words in row_groups:
            row_text = " ".join(w[2] for w in row_words)
            # 証明番号パターン（ハイフン区切り数字）を含む行を対象とする
            code_m = re.search(r'(\d{5,12}-\d{5,9}|\d{12,14})', row_text)
            if not code_m:
                continue
            code = code_m.group(1)
            # 右端（x > 900）の金額を抽出
            amount_words = [w for w in row_words if w[0] > 900 and clean_amount(w[2]) is not None]
            if not amount_words:
                continue
            amount = clean_amount(sorted(amount_words, key=lambda w: w[0])[-1][2])
            if amount is None or amount < 1000:
                continue
            rows.append({
                "勘定日": send_date,
                "金額": amount,
                "振込依頼人コード": code,
                "振込依頼人カナ": "",
            })
    return rows


# ---- 集金代行/LACTii PDF (エルズ系) ----
def convert_elzs_pdf(raw: bytes, filename: str) -> list[dict]:
    """集金代行送金明細 / LACTii送金明細 PDF（エルズサポート系）"""
    full_text = extract_pdf_text(raw)

    # 送金日
    m_date = re.search(r'送金年月日[：:]\s*(\d{4}/\d{1,2}/\d{1,2})', full_text)
    send_date = to_yyyymmdd(m_date.group(1)) if m_date else ""

    rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                # ヘッダー行があるかチェック
                first_row = table[0]
                has_header = any(str(c).strip() == "保証番号" for c in first_row if c)
                data_rows = table[1:] if has_header else table

                for row in data_rows:
                    if not row or len(row) < 3:
                        continue
                    code = str(row[0]).strip() if row[0] else ""
                    # 送金予定額は col[2]
                    amount = clean_amount(str(row[2]).strip() if len(row) > 2 and row[2] else "")
                    if amount is None:
                        continue
                    # 送金先列（col[3] or col[4]）でライフアドバンスを確認
                    destination = ""
                    for i in [3, 4]:
                        if len(row) > i and row[i]:
                            destination += str(row[i])
                    if not is_life_advance(destination):
                        continue
                    rows.append({
                        "勘定日": send_date,
                        "金額": amount,
                        "振込依頼人コード": code,
                        "振込依頼人カナ": "",
                    })
    return rows


# ---- ナップ 収納代行 PDF ----
def convert_nap_pdf(raw: bytes, filename: str) -> list[dict]:
    """ナップ 収納代行送金明細PDF"""
    full_text = extract_pdf_text(raw)
    rows = []

    for line in full_text.split('\n'):
        if not is_life_advance(line):
            continue
        # 日付
        dm = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', line)
        if not dm:
            continue
        date = f"{dm.group(1)}{int(dm.group(2)):02d}{int(dm.group(3)):02d}"

        # 金額（楽天 より前の5-7桁数字）
        am = re.search(r'(\d{5,7})\s+楽天', line)
        if not am:
            continue
        amount = clean_amount(am.group(1))
        if amount is None:
            continue

        # 名前（コレクター識別子の後から金額直前まで。末尾1トークンは部屋番号）
        collector_m = re.search(r'(?:インサイト|ナップ)\S+\s+', line)
        name = ""
        if collector_m:
            name_section = line[collector_m.end():am.start()].strip()
            tokens = name_section.split()
            if len(tokens) > 1:
                name = " ".join(tokens[:-1])  # 末尾トークン（部屋番号）を除く
            elif tokens:
                name = tokens[0]

        rows.append({
            "勘定日": date,
            "金額": amount,
            "振込依頼人コード": "",
            "振込依頼人カナ": kanji_to_katakana(name),
        })
    return rows


# ---- プレミアライフ PDF ----
def convert_epos_pdf(raw: bytes, filename: str) -> list[dict]:
    """エポスカード 家賃精算額一覧PDF（x座標列ベース抽出）"""
    full_text = extract_pdf_text(raw)
    if not (is_life_advance(full_text) or "7678903" in full_text):
        return []

    # 振込予定日
    m_date = re.search(r'振込予定日\s+(\d{4})年(\d{2})月(\d{2})日', full_text)
    send_date = f"{m_date.group(1)}{m_date.group(2)}{m_date.group(3)}" if m_date else ""

    rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            # y座標でグループ化
            row_map: dict[int, list] = {}
            for w in words:
                y = round(w['top'] / 10) * 10
                row_map.setdefault(y, []).append(w)

            for y in sorted(row_map):
                row_words = sorted(row_map[y], key=lambda w: w['x0'])

                # 契約番号 (XXXX-XXXXXX-XXX) が x≈355 にある行のみ
                code_words = [w for w in row_words
                              if re.match(r'^\d{4}-\d{6}-\d{3}$', w['text'])]
                if not code_words:
                    continue
                code = code_words[0]['text']

                # 契約者名: x=275〜354
                name_parts = [w['text'] for w in row_words if 270 <= w['x0'] < 355]
                name = " ".join(name_parts)

                # 金額: x≥490、数字とカンマのみ
                amt_words = [w for w in row_words
                             if w['x0'] >= 490 and re.match(r'^[\d,]+$', w['text'])]
                if not amt_words:
                    continue
                amount = clean_amount(amt_words[0]['text'])
                if not amount:
                    continue

                rows.append({
                    "勘定日": send_date,
                    "金額": amount,
                    "振込依頼人コード": code,
                    "振込依頼人カナ": kanji_to_katakana(name),
                })
    return rows


def convert_premialife_pdf(raw: bytes, filename: str) -> list[dict]:
    """プレミアライフ 家賃送金明細PDF"""
    full_text = extract_pdf_text(raw)
    if not (is_life_advance(full_text) or "7678903" in full_text):
        return []

    # 送金日
    m_date = re.search(r'送金日\s+(\d{4})年(\d{1,2})月(\d{1,2})日', full_text)
    send_date = ""
    if m_date:
        send_date = f"{m_date.group(1)}{int(m_date.group(2)):02d}{int(m_date.group(3)):02d}"

    rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in (table or []):
                    if not row or len(row) < 13:
                        continue
                    # データ行: row[0]が通し番号(数字)、row[1]が10桁の契約ID
                    if not (row[0] and str(row[0]).strip().isdigit()):
                        continue
                    contract_id = str(row[1]).strip() if row[1] else ""
                    if not re.match(r'\d{10}', contract_id):
                        continue
                    contractor = str(row[2]).strip() if row[2] else ""
                    amount = clean_amount(str(row[12]).strip() if row[12] else "")
                    if amount is None:
                        continue
                    rows.append({
                        "勘定日": send_date,
                        "金額": amount,
                        "振込依頼人コード": contract_id,
                        "振込依頼人カナ": kanji_to_katakana(contractor),
                    })
    return rows


# ---- アーク賃貸保証 PDF ----
def convert_arc_pdf(raw: bytes, filename: str) -> list[dict]:
    """アーク賃貸保証 マイガードプレミアム 定時送金PDF"""
    full_text = extract_pdf_text(raw)
    if not (is_life_advance(full_text) or "7678903" in full_text):
        return []

    # 振込実行日
    m_date = re.search(r'振込実行日[）)]\s*(\d{4})年(\d{2})月(\d{2})日', full_text)
    send_date = ""
    if m_date:
        send_date = f"{m_date.group(1)}{m_date.group(2)}{m_date.group(3)}"

    rows = []
    lines = [l.strip() for l in full_text.split('\n') if l.strip()]
    pending = None

    for line in lines:
        # データ行1: 9桁契約番号で始まる
        m1 = re.match(
            r'^(\d{9})\s+(.+?)\s+([\d,]+)\s+[\d,]+\s+[\d,]+\s+[\d,]+\s+[\d,]+\s+([\d,]+)\s*$',
            line
        )
        if m1:
            pending = {"code": m1.group(1), "name": m1.group(2)}
            continue

        # データ行2: 直前にデータ行1があった場合、最後の数字が差引送金額
        if pending is not None:
            if re.match(r'^【', line):
                pending = None
                continue
            nums = re.findall(r'[\d,]+', line)
            if nums:
                amount = clean_amount(nums[-1])
                if amount and amount > 0:
                    rows.append({
                        "勘定日": send_date,
                        "金額": amount,
                        "振込依頼人コード": pending["code"],
                        "振込依頼人カナ": kanji_to_katakana(pending["name"]),
                    })
            pending = None

    return rows


# ---- JID 日本賃貸保証 PDF ----
def convert_jid_pdf(raw: bytes, filename: str) -> list[dict]:
    """日本賃貸保証(JID) 送金予定明細表PDF"""
    full_text = extract_pdf_text(raw)
    if not (is_life_advance(full_text) or "7678903" in full_text):
        return []

    # 送金日（文字が分解されている場合あり）
    m_date = re.search(r'送\s*金\s*日\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', full_text)
    send_date = ""
    if m_date:
        send_date = f"{m_date.group(1)}{int(m_date.group(2)):02d}{int(m_date.group(3)):02d}"

    rows = []
    for line in full_text.split('\n'):
        # データ行: ﾄﾘｵ系商品区分で始まる
        if not re.match(r'^ﾄﾘｵ', line):
            continue

        # 保証番号（[A-Z]{2}\d+）
        code_m = re.search(r'\b([A-Z]{1,2}\d+)\b', line)
        code = code_m.group(1) if code_m else ""

        # 保証番号直後の2トークンを契約者名とする
        name = ""
        if code_m:
            after = line[code_m.end():].strip()
            tokens = after.split()
            if len(tokens) >= 2:
                name = f"{tokens[0]} {tokens[1]}"
            elif tokens:
                name = tokens[0]

        # 最後の5桁以上の数字が送金金額
        normalized = unicodedata.normalize('NFKC', line)
        nums = re.findall(r'(?<![A-Za-z])(\d{4,9})(?![A-Za-z])', normalized)
        if not nums:
            continue
        amount = clean_amount(nums[-1])
        if amount is None or amount < 1000:
            continue

        rows.append({
            "勘定日": send_date,
            "金額": amount,
            "振込依頼人コード": code,
            "振込依頼人カナ": kanji_to_katakana(name),
        })
    return rows


# ---- 日本賃貸住宅保証機構 PDF ----
def convert_jrag_pdf(raw: bytes, filename: str) -> list[dict]:
    """日本賃貸住宅保証機構 家賃送金明細PDF"""
    full_text = extract_pdf_text(raw)
    if not (is_life_advance(full_text) or "7678903" in full_text):
        return []

    # 送金日
    m_date = re.search(r'送金日[：:]\s*(\d{4})/(\d{1,2})/(\d{1,2})', full_text)
    send_date = ""
    if m_date:
        send_date = f"{m_date.group(1)}{int(m_date.group(2)):02d}{int(m_date.group(3)):02d}"

    rows = []
    for line in full_text.split('\n'):
        # データ行: 先頭が通し番号（数字）
        m = re.match(
            r'^(\d+)\s+(.+?)\s+(口座振替|CVS発行|コンビニ)\s+(\d{10})\s+\d+\s+\S+\s+¥([\d,]+)',
            line
        )
        if not m:
            continue
        name_property = m.group(2)
        code = m.group(4)
        amount = clean_amount(m.group(5))
        if amount is None:
            continue

        # 名前と物件名が混在している。先頭2トークンを名前とする
        tokens = name_property.split()
        name = " ".join(tokens[:2]) if len(tokens) >= 2 else name_property

        rows.append({
            "勘定日": send_date,
            "金額": amount,
            "振込依頼人コード": code,
            "振込依頼人カナ": kanji_to_katakana(name),
        })
    return rows


# ---- フェア信用保証 PDF (スキャン、OCR) ----
def convert_fair_pdf(raw: bytes, filename: str) -> list[dict]:
    """フェア信用保証 収納代行送金完了書PDF（スキャン画像、OCR使用）"""
    pages = ocr_pdf_pages(raw)
    send_date = ""
    rows = []

    for page_words in pages:
        all_text = " ".join(w[2] for w in sorted(page_words, key=lambda w: w[1]))
        if not (is_life_advance(all_text) or "7678903" in all_text):
            continue
        # 送金日（令和年 or 西暦）
        if not send_date:
            sorted_words = sorted(page_words, key=lambda w: w[1])
            all_text = " ".join(w[2] for w in sorted_words)
            # 令和X年M月D日
            m = re.search(r'令和.{0,2}(\d+)年.{0,2}(\d{1,2}).{0,2}月.{0,2}(\d{1,2})', all_text)
            if m:
                try:
                    reiwa = int(m.group(1))
                    if 1 <= reiwa <= 30:
                        send_date = f"{reiwa+2018}{int(m.group(2)):02d}{int(m.group(3)):02d}"
                except Exception:
                    pass
            if not send_date:
                send_date = _extract_date_from_ocr(page_words)

        # y座標でグループ化
        row_groups = group_ocr_by_row(page_words, y_tol=20)

        for row_words in row_groups:
            row_text = " ".join(w[2] for w in row_words)
            # 承認番号パターン（02-XXXXXX）
            code_m = re.search(r'(\d{2}-\d{5,7})', row_text)
            if not code_m:
                continue
            code = code_m.group(1)

            # 金額：右側（x>800）の最大値
            amount_words = [(w[0], w[2]) for w in row_words
                           if w[0] > 800 and clean_amount(w[2]) is not None]
            if not amount_words:
                continue
            amount = clean_amount(sorted(amount_words)[-1][1])
            if amount is None or amount < 1000:
                continue

            # 契約者名（中央x=400-700付近）
            name_words = [w[2] for w in row_words if 300 < w[0] < 750]
            name = " ".join(name_words).replace("様", "").strip()

            rows.append({
                "勘定日": send_date,
                "金額": amount,
                "振込依頼人コード": code,
                "振込依頼人カナ": kanji_to_katakana(name),
            })

    return rows


# ---- オリコフォレントインシュア PDF (スキャン) ----
def convert_orico_pdf(raw: bytes, filename: str) -> list[dict]:
    """オリコフォレントインシュア PDF（スキャン画像、精度限定）"""
    pages = ocr_pdf_pages(raw)
    send_date = ""
    rows = []

    for page_words in pages:
        all_text = " ".join(w[2] for w in sorted(page_words, key=lambda w: w[1]))
        if not (is_life_advance(all_text) or "7678903" in all_text):
            continue
        if not send_date:
            send_date = _extract_date_from_ocr(page_words)

        row_groups = group_ocr_by_row(page_words, y_tol=20)
        for row_words in row_groups:
            row_text = " ".join(w[2] for w in row_words)
            # 金額が含まれる行で右側の数値を抽出
            amount_words = [(w[0], w[2]) for w in row_words
                           if w[0] > 800 and clean_amount(w[2]) is not None and clean_amount(w[2]) >= 10000]
            if not amount_words:
                continue
            amount = clean_amount(sorted(amount_words)[-1][1])
            if amount is None:
                continue

            # 番号らしきもの（左側）
            code_words = [w[2] for w in row_words if w[0] < 400
                         and re.search(r'\d{5,}', w[2])]
            code = code_words[0] if code_words else ""

            rows.append({
                "勘定日": send_date,
                "金額": amount,
                "振込依頼人コード": code,
                "振込依頼人カナ": "",
            })

    return rows


def convert_zenhoren_pdf(raw: bytes, filename: str) -> list[dict]:
    """全保連 振替精算書PDF"""
    _full = extract_pdf_text(raw)
    if not (is_life_advance(_full) or "7678903" in _full):
        return []
    send_date = ""
    all_table_rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if not send_date:
                m = re.search(r'振込日[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日', text)
                if m:
                    send_date = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
            for table in page.extract_tables():
                for row in table:
                    if row and row[0] and re.match(r'\d{10}', str(row[0])):
                        all_table_rows.append(row)

    rows = []
    for row in all_table_rows:
        if len(row) < 10:
            continue
        if row[8] != "口座振替" or row[9] != "○":
            continue
        amount = clean_amount(row[7])  # 振込額
        if not amount:
            continue
        rows.append({
            "勘定日": send_date,
            "金額": amount,
            "振込依頼人コード": str(row[0]).strip(),
            "振込依頼人カナ": kanji_to_katakana(str(row[1]).strip()),
        })
    return rows


def convert_fourseasons_pdf(raw: bytes, filename: str) -> list[dict]:
    """フォーシーズ 集金代行PDF"""
    rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    if not row or not row[0]:
                        continue
                    raw_ym = str(row[0]).replace('\n', '').strip()
                    if not re.match(r'\d{4}-\d{2}', raw_ym):
                        continue
                    col5 = str(row[5] or "").replace('\n', '').replace('\r', '')
                    if not is_life_advance(col5):
                        continue
                    ym = re.sub(r'[^0-9]', '', raw_ym)  # "202606"
                    send_date = ym + "01" if len(ym) == 6 else ""
                    amount = clean_amount(row[10])
                    if not amount:
                        continue
                    kana = kanji_to_katakana(
                        unicodedata.normalize('NFKC', str(row[2] or "").replace('\n', '').strip())
                    )
                    code = str(row[3] or "").strip()
                    rows.append({
                        "勘定日": send_date,
                        "金額": amount,
                        "振込依頼人コード": code,
                        "振込依頼人カナ": kana,
                    })
    return rows


def convert_casa_pdf(raw: bytes, filename: str) -> list[dict]:
    """カーサ/リコーリース 送金明細PDF"""
    rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        # Page 2: extract (date, contract_no) pairs in row order
        contract_entries = []
        if len(pdf.pages) > 1:
            p2_text = pdf.pages[1].extract_text() or ""
            for m in re.finditer(r'(\d{6})\s+(?:引落対象者|代位弁済)\s*(\d+)', p2_text):
                ym = m.group(1)
                contract_entries.append((ym + "01", m.group(2)))

        # Page 1: parse words by y-coordinate
        p1_words = pdf.pages[0].extract_words()

        # Group words by y (±5px tolerance)
        row_groups: dict[float, list[tuple]] = {}
        for w in p1_words:
            y = w['top']
            bucket = next((ky for ky in row_groups if abs(ky - y) <= 5), y)
            row_groups.setdefault(bucket, []).append((w['x0'], w['text']))

        # Build ordered list of data rows (skip header y<70 and footer y>430)
        data_rows = [
            sorted(row_groups[ky], key=lambda w: w[0])
            for ky in sorted(row_groups)
            if 70 <= ky <= 430
        ]

        for idx, row_words in enumerate(data_rows):
            # 口座名義人: x≈371 (half-width kana field)
            koza_words = [w[1] for w in row_words if 360 <= w[0] <= 445]
            koza = " ".join(koza_words)
            # ライフアドバンス口座: starts with ｶ)ﾗｲﾌｱﾄﾞﾊﾞ
            if "ｶ)ﾗｲﾌｱﾄﾞﾊﾞ" not in koza:
                continue

            # 契約者名: x=525–615
            name = " ".join(w[1] for w in row_words if 525 <= w[0] < 617)

            # 送金金額: rightmost word at x>690
            amount_words = [(w[0], w[1]) for w in row_words if w[0] > 690]
            if not amount_words:
                continue
            amount = clean_amount(max(amount_words, key=lambda w: w[0])[1])
            if not amount:
                continue

            # Date and contract number from Page 2 by row index
            send_date, code = contract_entries[idx] if idx < len(contract_entries) else ("", "")

            rows.append({
                "勘定日": send_date,
                "金額": amount,
                "振込依頼人コード": code,
                "振込依頼人カナ": kanji_to_katakana(name),
            })

    return rows


def convert_capco_pdf(raw: bytes, filename: str) -> list[dict]:
    """CAPCOエージェンシー お支払明細書PDF（スキャン、300DPI OCR）"""
    import numpy as np
    reader = get_ocr_reader()
    rows = []

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            img = page.to_image(resolution=300)
            arr = np.array(img.original)
            results = reader.readtext(arr, detail=1)
            # 日付検索にはconf不問で全ワードを使用、行解析は0.3以上のみ
            all_words = sorted(
                [(float(bbox[0][0]), float(bbox[0][1]), text, float(conf))
                 for bbox, text, conf in results],
                key=lambda w: w[1]
            )
            page_words = [w for w in all_words if w[3] > 0.3]
            all_text = " ".join(w[2] for w in all_words)

            # ライフアドバンス口座(7678903)があるページのみ処理
            if "7678903" not in all_text:
                continue

            # 日付: 精算日 "226年 5月21" → 2026年5月21日 (OCR誤認識の3桁年を補正)
            send_date = ""
            for m in re.finditer(r'(\d{2,4})年\s*(\d{1,2})[月目]\s*(\d{1,2})', all_text):
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 100 <= y <= 299:
                    y = 2000 + (y % 100)
                if 2020 <= y <= 2040 and 1 <= mo <= 12 and 1 <= d <= 31:
                    send_date = f"{y}{mo:02d}{d:02d}"
                    break
            if not send_date:
                # 支払月フォールバック: "2026年6月分" → 20260601
                m2 = re.search(r'(\d{2,4})年\s*(\d{1,2})[月目]', all_text)
                if m2:
                    y, mo = int(m2.group(1)), int(m2.group(2))
                    if 100 <= y <= 299:
                        y = 2000 + (y % 100)
                    if 2020 <= y <= 2040 and 1 <= mo <= 12:
                        send_date = f"{y}{mo:02d}01"

            # 管理番号: ページ上部(y<800)の右端にある4〜8桁の数字
            top_nums = sorted(
                [w for w in page_words if w[1] < 800 and re.match(r'^\d{4,8}$', w[2])],
                key=lambda w: w[0], reverse=True
            )
            code = top_nums[0][2] if top_nums else ""

            # データ行: 部屋番号(1〜4桁)で始まる行から名前・合計金額を取得
            for row_words in group_ocr_by_row(page_words, y_tol=15):
                sorted_w = sorted(row_words, key=lambda w: w[0])
                if not sorted_w or not re.match(r'^\d{1,4}$', sorted_w[0][2]):
                    continue

                name_parts = []
                amounts = []
                for x, y, text, conf in sorted_w[1:]:
                    ntext = unicodedata.normalize('NFKC', text)
                    digits = re.sub(r'[,，]', '', ntext)
                    if re.match(r'^\d{4,}$', digits):
                        amounts.append((x, ntext))
                    elif not amounts:
                        if len(text) >= 2 and not re.match(r'^[-一ー]+$', text) \
                                and text.lower() not in ('ol', 'o|', '0|'):
                            name_parts.append(text)

                if not name_parts or not amounts:
                    continue
                total = clean_amount(max(amounts, key=lambda a: a[0])[1])
                if not total or total < 1000:
                    continue

                rows.append({
                    "勘定日": send_date,
                    "金額": total,
                    "振込依頼人コード": code,
                    "振込依頼人カナ": kanji_to_katakana(" ".join(name_parts)),
                })

    return rows


def convert_ierabu_passthrough(raw: bytes, filename: str) -> list[dict]:
    """いえらぶ形式CSVのパススルー（勘定日が空の場合はファイル名から補完）"""
    # ファイル名から YYYYMM を抽出して日付を補完
    m = re.search(r'(\d{6})', filename)
    fallback_date = (m.group(1) + "01") if m else ""

    text = decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        date = row.get("勘定日", "").strip() or fallback_date
        amount = clean_amount(row.get("金額", ""))
        if not amount:
            continue
        rows.append({
            "勘定日": date,
            "金額": amount,
            "振込依頼人コード": row.get("振込依頼人コード", "").strip(),
            "振込依頼人カナ": row.get("振込依頼人カナ", "").strip(),
        })
    return rows


def convert_orico_csv(raw: bytes, filename: str) -> list[dict]:
    """オリコフォレントインシュア 家賃明細CSV"""
    text = decode_csv_bytes(raw)
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        # ライフアドバンス口座のみ
        if row.get("口座番号", "").strip() != "7678903":
            continue
        amount = clean_amount(row.get("振込額", ""))
        if not amount:
            continue
        send_date = row.get("支払日", "").strip()
        # 契約者氏名カナ は全角カナ・全角スペース → そのまま使用
        kana = row.get("契約者氏名カナ", "").replace("　", " ").strip()
        code = row.get("承認番号", "").strip()
        rows.append({
            "勘定日": send_date,
            "金額": amount,
            "振込依頼人コード": code,
            "振込依頼人カナ": kana,
        })
    return rows


# フォーマットID → 変換関数マッピング
CONVERTERS = {
    "fb": convert_fb,
    "safety_csv": convert_safety_csv,
    "rakuten": convert_rakuten,
    "safety_pdf": convert_safety_pdf,
    "rakuten_bank": convert_rakuten_bank,
    "elzs_pdf": convert_elzs_pdf,
    "nap_pdf": convert_nap_pdf,
    "premialife_pdf": convert_premialife_pdf,
    "arc_pdf": convert_arc_pdf,
    "jid_pdf": convert_jid_pdf,
    "jrag_pdf": convert_jrag_pdf,
    "fair_pdf": convert_fair_pdf,
    "orico_pdf": convert_orico_pdf,
    "zenhoren_pdf": convert_zenhoren_pdf,
    "fourseasons_pdf": convert_fourseasons_pdf,
    "casa_pdf": convert_casa_pdf,
    "orico_csv": convert_orico_csv,
    "capco_pdf": convert_capco_pdf,
    "ierabu_passthrough": convert_ierabu_passthrough,
    "epos_pdf": convert_epos_pdf,
}


# ---------------------------------------------------------------------------
# フォーマット判定
# ---------------------------------------------------------------------------

CSV_SIGNATURES = [
    {"id": "safety_csv", "must": {"振替日", "振替内訳（賃料等）", "保証番号", "契約者名カナ", "送金先名"}},
    {"id": "fb",         "must": {"振替日", "請求額", "引落口座名義"}},
    {"id": "rakuten",    "must": {"請求年月", "賃料等", "契約者名カナ（半）"}},
    {"id": "orico_csv",          "must": {"承認番号", "振込額", "支払日", "契約者氏名カナ", "口座番号"}},
    {"id": "ierabu_passthrough", "must": {"勘定日", "金額", "振込依頼人コード", "振込依頼人カナ"}},
]

# PDF内テキストキーワードによるフォーマット判定
PDF_SIGNATURES = [
    {"id": "elzs_pdf",       "must": ["集金代行 送金明細書", "保証番号"]},
    {"id": "elzs_pdf",       "must": ["LACTii 送金明細書",  "保証番号"]},
    {"id": "nap_pdf",        "must": ["収納代行", "実質送金額", "振替日"]},
    {"id": "premialife_pdf", "must": ["株式会社プレミアライフ", "家賃送金明細"]},
    {"id": "arc_pdf",        "must": ["マイガードプレミアム", "定時送金のお知らせ"]},
    {"id": "jid_pdf",        "must": ["送金予定明細表", "保証番号"]},
    {"id": "jrag_pdf",       "must": ["日本賃貸住宅保証機構"]},
    {"id": "safety_pdf",     "must": ["日本セーフティー", "送金明細"]},
    {"id": "zenhoren_pdf",  "must": ["振替精算書", "振込日", "承認番号"]},
    {"id": "fourseasons_pdf","must": ["フォーシーズ", "集金代行区"]},
    {"id": "casa_pdf",      "must": ["リコーリース", "送金金額", "口座名義人"]},
    {"id": "epos_pdf",      "must": ["株式会社エポスカード", "振込予定日", "契約番号"]},
]

FMT_BY_ID = {fmt["id"]: fmt for fmt in FORMAT_REGISTRY}


def detect_format_from_content(raw: bytes, filename: str) -> dict | None:
    if filename.lower().endswith(".pdf"):
        if not PDF_AVAILABLE:
            return None
        try:
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages[:2])
            if text.strip():
                for sig in PDF_SIGNATURES:
                    if all(kw in text for kw in sig["must"]):
                        return FMT_BY_ID.get(sig["id"])
        except Exception:
            pass
        return None

    # CSV
    try:
        text = decode_csv_bytes(raw)
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return None
        cols = set(reader.fieldnames)
        for sig in CSV_SIGNATURES:
            if sig["must"].issubset(cols):
                return FMT_BY_ID.get(sig["id"])
    except Exception:
        pass
    return None


def detect_format(filename: str, raw: bytes | None = None) -> dict | None:
    name_nfc = unicodedata.normalize("NFC", filename)
    for fmt in FORMAT_REGISTRY:
        if fmt["match"](name_nfc):
            return fmt
    if raw is not None:
        return detect_format_from_content(raw, filename)
    return None


# ---------------------------------------------------------------------------
# Flask ルーティング
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "ファイルが選択されていません"}), 400

    fallback_date = request.form.get("fallback_date", "").strip()

    all_rows: list[dict] = []
    results: list[dict] = []

    for f in files:
        filename = f.filename or ""
        raw = f.read()
        fmt = detect_format(filename, raw)
        if fmt is None:
            results.append({
                "filename": filename,
                "status": "unknown",
                "message": "対応フォーマット不明",
            })
            continue

        try:
            converter = CONVERTERS[fmt["id"]]
            rows = converter(raw, filename)
            all_rows.extend(rows)
            results.append({
                "filename": filename,
                "status": "ok",
                "format": fmt["name"],
                "count": len(rows),
            })
        except Exception as e:
            results.append({
                "filename": filename,
                "status": "error",
                "message": str(e),
            })

    # 勘定日が空の行にフォールバック日付を補完
    if fallback_date:
        for row in all_rows:
            if not row.get("勘定日"):
                row["勘定日"] = fallback_date

    # 出力 CSV 生成（UTF-8 BOM付き）
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["勘定日", "金額", "振込依頼人コード", "振込依頼人カナ"],
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerows(all_rows)

    csv_bytes = ("﻿" + output.getvalue()).encode("utf-8")
    out_filename = datetime.now().strftime("%Y%m") + "_ierabu_import.csv"

    return jsonify({
        "results": results,
        "total": len(all_rows),
        "csv_b64": __import__("base64").b64encode(csv_bytes).decode(),
        "filename": out_filename,
    })


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", debug=False, port=port)
