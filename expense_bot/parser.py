from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


AMOUNT_TOKEN_RE = re.compile(
    r"(?i)(?:rp\.?\s*)?(\d+(?:[.,]\d+)?(?:[.,]\d{3})*)(?:\s*(rb|ribu|k|jt|juta)\b)?"
)
PERCENT_RE = re.compile(r"(?i)(\d+(?:[.,]\d+)?)\s*%")
DATE_RE = re.compile(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})")

CATEGORY_KEYWORDS = {
    "Makanan & Minuman": [
        "kopi",
        "makan",
        "minum",
        "resto",
        "warung",
        "gofood",
        "grabfood",
        "snack",
        "jajan",
    ],
    "Transportasi": [
        "bensin",
        "bbm",
        "parkir",
        "tol",
        "gojek",
        "gocar",
        "grab",
        "kereta",
        "bus",
        "ojek",
    ],
    "Belanja": [
        "belanja",
        "indomaret",
        "alfamart",
        "supermarket",
        "shopee",
        "tokopedia",
        "lazada",
        "pakaian",
        "sepatu",
    ],
    "Tagihan": [
        "listrik",
        "pln",
        "air",
        "internet",
        "wifi",
        "pulsa",
        "paket data",
        "token",
        "bpjs",
    ],
    "Hiburan": [
        "nonton",
        "bioskop",
        "netflix",
        "spotify",
        "game",
        "steam",
        "rekreasi",
    ],
    "Kesehatan": [
        "dokter",
        "obat",
        "klinik",
        "apotek",
        "vitamin",
        "rumah sakit",
    ],
    "Pendidikan": [
        "kursus",
        "buku",
        "sekolah",
        "kuliah",
        "pelatihan",
    ],
}

STOPWORDS = (
    "beli",
    "bayar",
    "belanja",
    "order",
    "pesan",
    "pesen",
    "isi",
    "topup",
    "top up",
)


@dataclass
class ParsedExpense:
    item: str
    amount: int
    category: str


@dataclass
class ParsedSplitBill:
    subtotal: int
    people: int
    service_amount: int
    tax_amount: int

    @property
    def grand_total(self) -> int:
        return self.subtotal + self.service_amount + self.tax_amount


@dataclass
class ReceiptInfo:
    merchant: str
    date_text: Optional[str]
    total: int
    category: str


def format_idr(amount: int) -> str:
    return f"Rp{amount:,}".replace(",", ".")


def parse_amount_token(token: str) -> Optional[int]:
    token_lower = token.strip().lower().replace("rp", "").replace("idr", "")
    token_lower = token_lower.replace(" ", "")
    if not token_lower:
        return None

    suffix = ""
    for candidate in ("ribu", "rb", "k", "juta", "jt"):
        if token_lower.endswith(candidate):
            suffix = candidate
            token_lower = token_lower[: -len(candidate)]
            break

    if not token_lower:
        return None

    if suffix == "":
        digits_only = re.sub(r"\D", "", token_lower)
        if not digits_only:
            return None
        return int(digits_only)

    token_numeric = token_lower.replace(",", ".")
    try:
        base_float = float(token_numeric)
    except ValueError:
        return None

    if suffix in {"rb", "ribu", "k"}:
        return int(round(base_float * 1000))
    if suffix in {"jt", "juta"}:
        return int(round(base_float * 1000000))
    return int(round(base_float))


def parse_amount_from_text(text: str) -> Optional[int]:
    match = AMOUNT_TOKEN_RE.search(text)
    if not match:
        return None
    amount_str = match.group(0)
    amount = parse_amount_token(amount_str)
    if not amount or amount <= 0:
        return None
    return amount


def infer_category(item_text: str) -> str:
    low = item_text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in low for keyword in keywords):
            return category
    return "Lainnya"


def normalize_category(name: str) -> str:
    clean = re.sub(r"\s+", " ", name).strip().lower()
    if not clean:
        return "Lainnya"
    for category in CATEGORY_KEYWORDS:
        if clean == category.lower():
            return category
    return clean.title()


def parse_expense_input(text: str) -> Optional[ParsedExpense]:
    clean = re.sub(r"\s+", " ", text.strip())
    if not clean:
        return None

    category = ""
    category_match = re.search(r"(?i)(?:kategori|cat)\s*[:=-]?\s*([a-zA-Z/& ]+)$", clean)
    if category_match:
        category = normalize_category(category_match.group(1))
        clean = clean[: category_match.start()].strip()

    amount_match = AMOUNT_TOKEN_RE.search(clean)
    if not amount_match:
        return None

    amount = parse_amount_token(amount_match.group(0))
    if not amount or amount <= 0:
        return None

    item_candidate = (clean[: amount_match.start()] + clean[amount_match.end() :]).strip(
        " ,.-:"
    )
    item_low = item_candidate.lower()
    for stopword in STOPWORDS:
        if item_low.startswith(stopword + " "):
            item_candidate = item_candidate[len(stopword) :].strip()
            break

    if not item_candidate:
        return None

    item = item_candidate.strip().capitalize()
    selected_category = category or infer_category(item)
    return ParsedExpense(item=item, amount=amount, category=selected_category)


def parse_percentage_after_keyword(text: str, keyword: str) -> Optional[float]:
    match = re.search(rf"(?i){re.escape(keyword)}\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*%", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def parse_amount_after_keyword(text: str, keyword: str) -> Optional[int]:
    match = re.search(
        rf"(?i){re.escape(keyword)}\s*[:=]?\s*((?:rp\.?\s*)?\d[\d.,]*(?:\s*(?:rb|ribu|k|jt|juta)\b)?)(?![\d%])",
        text,
    )
    if not match:
        return None
    return parse_amount_token(match.group(1))


def parse_split_bill(text: str) -> Optional[ParsedSplitBill]:
    low = text.lower()
    if "split bill" not in low and "patungan" not in low:
        return None

    people_match = re.search(r"(?i)(?:bagi|untuk|dibagi)\s*(\d+)\s*(?:orang|org|pax|teman)?", low)
    if not people_match:
        people_match = re.search(r"(?i)(\d+)\s*(?:orang|org|pax|teman)", low)
    if not people_match:
        return None

    people = int(people_match.group(1))
    if people <= 0:
        return None

    subtotal = (
        parse_amount_after_keyword(text, "total")
        or parse_amount_after_keyword(text, "tagihan")
        or parse_amount_after_keyword(text, "bill")
        or parse_amount_from_text(text)
    )
    if not subtotal or subtotal <= 0:
        return None

    service_amount = parse_amount_after_keyword(text, "service") or 0
    if service_amount == 0:
        service_pct = parse_percentage_after_keyword(text, "service")
        if service_pct is not None:
            service_amount = int(round(subtotal * service_pct / 100))

    tax_amount = (
        parse_amount_after_keyword(text, "pajak")
        or parse_amount_after_keyword(text, "ppn")
        or 0
    )
    if tax_amount == 0:
        tax_pct = parse_percentage_after_keyword(text, "pajak")
        if tax_pct is None:
            tax_pct = parse_percentage_after_keyword(text, "ppn")
        if tax_pct is not None:
            tax_amount = int(round((subtotal + service_amount) * tax_pct / 100))

    return ParsedSplitBill(
        subtotal=subtotal,
        people=people,
        service_amount=service_amount,
        tax_amount=tax_amount,
    )


def parse_receipt_text(raw_text: str) -> Optional[ReceiptInfo]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return None

    merchant = "Merchant tidak dikenal"
    for line in lines[:5]:
        low = line.lower()
        if any(token in low for token in ("struk", "receipt", "tanggal", "date", "no.")):
            continue
        if re.search(r"[a-zA-Z]{3,}", line):
            merchant = line.title()
            break

    date_match = DATE_RE.search(raw_text)
    date_text = date_match.group(1) if date_match else None

    total = None
    keyword_patterns = [
        r"(?i)(?:grand\s*total|total\s*bayar|jumlah\s*bayar|total)\D{0,8}((?:rp\.?\s*)?\d[\d.,]*(?:\s*(?:rb|ribu|k|jt|juta))?)",
        r"(?i)(?:payment|paid)\D{0,8}((?:rp\.?\s*)?\d[\d.,]*(?:\s*(?:rb|ribu|k|jt|juta))?)",
    ]
    for pattern in keyword_patterns:
        match = re.search(pattern, raw_text)
        if match:
            parsed = parse_amount_token(match.group(1))
            if parsed and parsed > 0:
                total = parsed
                break

    if total is None:
        amounts = []
        for match in AMOUNT_TOKEN_RE.finditer(raw_text):
            parsed = parse_amount_token(match.group(0))
            if parsed and parsed > 0:
                amounts.append(parsed)
        if amounts:
            total = max(amounts)

    if not total:
        return None

    category = infer_category(merchant)
    return ReceiptInfo(merchant=merchant, date_text=date_text, total=total, category=category)
