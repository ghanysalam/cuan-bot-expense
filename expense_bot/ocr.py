from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import httpx

from .parser import format_idr, infer_category, parse_amount_token


OCR_ENDPOINT = "https://api.ocr.space/parse/image"
DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
DATE_WORD_RE = re.compile(
    r"\b(\d{1,2}\s+(?:jan|feb|mar|apr|mei|may|jun|jul|agu|aug|sep|okt|oct|nov|des|dec)[a-z]*\s+\d{2,4})\b",
    re.I,
)
MONEY_TOKEN_RE = re.compile(
    r"(?i)\b(?:rp\.?\s*|idr\s*)?\d[\d.,]*(?:\s*(?:rb|ribu|k|jt|juta)\b)?\b"
)
TOTAL_KEYWORDS = (
    "grand total",
    "total bayar",
    "amount due",
    "netto",
    "jumlah",
    "total",
)
BANK_TOTAL_KEYWORDS = (
    "total amount",
    "jumlah transfer",
    "nominal transfer",
    "transfer amount",
    "debit amount",
    "nominal",
    "total debit",
)
IGNORE_TOTAL_HINTS = (
    "ppn",
    "tax",
    "pajak",
    "service",
    "change",
    "kembalian",
    "payment",
    "paid",
    "debit",
    "credit",
    "cash",
    "tunai",
    "diskon",
    "discount",
    "admin",
    "subtotal",
    "sub total",
    "saldo",
    "balance",
    "available",
    "fee",
    "pan",
    "terminal id",
    "reference no",
    "reference",
    "ref",
)
MERCHANT_SKIP_HINTS = (
    "struk",
    "receipt",
    "invoice",
    "tanggal",
    "date",
    "table",
    "cashier",
    "no.",
    "telp",
    "phone",
)
BANK_HINTS = (
    "bank",
    "rekening",
    "no rek",
    "account",
    "transfer",
    "recipient",
    "va ",
    "virtual account",
    "m-banking",
    "mobile banking",
    "internet banking",
    "debit",
    "kredit",
    "qris",
    "trx",
    "ref",
)
BANK_NAMES = (
    "bca",
    "bri",
    "bni",
    "mandiri",
    "permata",
    "cimb",
    "danamon",
    "ocbc",
    "dbs",
    "jago",
    "blu",
    "seabank",
    "maybank",
)

MERCHANT_CATEGORY_OVERRIDES = {
    "alfamart": "Belanja Bulanan",
    "indomaret": "Belanja Bulanan",
    "superindo": "Belanja Bulanan",
    "hypermart": "Belanja Bulanan",
    "starbucks": "Kopi/Snack",
    "kopi kenangan": "Kopi/Snack",
    "janji jiwa": "Kopi/Snack",
    "fore": "Kopi/Snack",
    "mcd": "Makanan & Minuman",
    "kfc": "Makanan & Minuman",
    "burger king": "Makanan & Minuman",
}


@dataclass
class ReceiptExtraction:
    merchant: str
    date_text: Optional[str]
    total: int
    category: str
    used_fallback_total: bool
    is_bank_transaction: bool


@dataclass
class OCRResult:
    raw_text: str
    receipt: Optional[ReceiptExtraction]
    needs_manual_total_confirmation: bool
    reply_text: str


def _normalize_lines(ocr_input: str | Sequence[str]) -> list[str]:
    if isinstance(ocr_input, str):
        raw_lines = ocr_input.splitlines()
    else:
        raw_lines = list(ocr_input)
    return [re.sub(r"\s+", " ", line).strip() for line in raw_lines if str(line).strip()]


def _extract_amounts(text: str) -> list[int]:
    candidates = [match.group(0) for match in MONEY_TOKEN_RE.finditer(text)]
    amounts: list[int] = []
    for token in candidates:
        if not _is_plausible_money_token(token):
            continue
        parsed = parse_amount_token(token)
        if parsed and 100 <= parsed <= 2_000_000_000:
            amounts.append(parsed)
    return amounts


def _is_plausible_money_token(token: str) -> bool:
    token_low = token.lower().strip()
    clean = token_low.replace(" ", "")
    has_currency = ("rp" in token_low) or ("idr" in token_low)
    clean = clean.replace("rp.", "").replace("rp", "").replace("idr", "")
    if not clean:
        return False

    has_suffix = clean.endswith(("rb", "ribu", "k", "jt", "juta"))
    digits_only = re.sub(r"\D", "", clean)
    if not digits_only:
        return False

    if not has_suffix and len(digits_only) > 12:
        return False

    # Protect against PAN/reference/terminal IDs (long plain digits without money markers).
    if (
        not has_suffix
        and not has_currency
        and "." not in clean
        and "," not in clean
        and len(digits_only) >= 8
    ):
        return False

    if not has_suffix and ("." in clean or "," in clean):
        normalized = clean.replace(",", ".")
        groups = normalized.split(".")
        if len(groups) > 5:
            return False
        if len(groups) > 1 and any(len(part) != 3 for part in groups[1:]):
            return False

    return True


def _pick_merchant(lines: list[str]) -> str:
    first_lines = lines[:3] if lines else []
    merchant = "Merchant tidak diketahui"
    best_score = -999

    for line in first_lines:
        low = line.lower()
        if any(hint in low for hint in MERCHANT_SKIP_HINTS):
            continue

        letters = sum(1 for c in line if c.isalpha())
        digits = sum(1 for c in line if c.isdigit())
        score = letters - (digits * 2)
        if "rp" in low:
            score -= 8
        if score > best_score and letters >= 3:
            best_score = score
            merchant = line.title()
    return merchant


def _detect_bank_transaction(lines: list[str]) -> bool:
    joined = " ".join(lines).lower()
    has_bank_name = any(bank in joined for bank in BANK_NAMES)
    has_bank_context = any(
        hint in joined
        for hint in (
            "transfer",
            "penerima",
            "recipient",
            "beneficiary",
            "receiver",
            "rekening",
            "account",
            "nominal",
            "debit",
            "saldo",
            "ref",
            "trx",
            "qris",
        )
    )
    if has_bank_name and has_bank_context:
        return True

    score = sum(1 for hint in BANK_HINTS if hint in joined)
    return score >= 2


def _pick_bank_merchant(lines: list[str]) -> str:
    recipient_labels = (
        "penerima",
        "recipient",
        "receiver",
        "beneficiary",
        "tujuan",
        "kepada",
        "nama penerima",
        "nama tujuan",
    )
    invalid_hints = ("pan", "ref", "terminal", "id", "rekening", "account")

    for idx, line in enumerate(lines):
        low = line.lower()
        if any(label in low for label in recipient_labels):
            candidate = ""
            inline_match = re.search(
                r"(?i)(?:penerima|recipient|receiver|beneficiary|tujuan|kepada)\s*[:=-]\s*(.+)$",
                line,
            )
            if inline_match:
                candidate = inline_match.group(1).strip()
            elif idx + 1 < len(lines):
                candidate = lines[idx + 1].strip()

            candidate_low = candidate.lower()
            digit_ratio = (sum(c.isdigit() for c in candidate) / max(len(candidate), 1))
            if (
                len(candidate) >= 3
                and not any(hint in candidate_low for hint in invalid_hints)
                and digit_ratio < 0.4
            ):
                return candidate.title()

    for line in lines[:6]:
        low = line.lower()
        for bank in BANK_NAMES:
            if bank in low:
                return f"Transfer {bank.upper()}"

    return "Transaksi Bank"


def _pick_category(merchant: str, lines: Iterable[str]) -> str:
    merchant_low = merchant.lower()
    for key, category in MERCHANT_CATEGORY_OVERRIDES.items():
        if key in merchant_low:
            return category

    joined = " ".join(lines)
    infer_from_text = infer_category(f"{merchant} {joined}")
    if infer_from_text != "Lainnya":
        return infer_from_text
    return "Belanja Lainnya"


def _extract_total(lines: list[str], is_bank_transaction: bool) -> tuple[Optional[int], bool]:
    keywords = BANK_TOTAL_KEYWORDS + TOTAL_KEYWORDS if is_bank_transaction else TOTAL_KEYWORDS
    keyword_amounts: list[int] = []
    cut_labels_re = re.compile(
        r"(?i)\b("
        r"source of fund|qris reference|reference|ref no|merchant pan|customer pan|"
        r"terminal id|acquirer|saldo|balance|available|fee|admin"
        r")\b"
    )

    for idx, line in enumerate(lines):
        low = line.lower()
        if "subtotal" in low or "sub total" in low:
            continue
        if not any(keyword in low for keyword in keywords):
            continue

        found_inline = False
        for keyword in keywords:
            if keyword in low:
                segment = line[low.find(keyword) + len(keyword) :]
                cut_match = cut_labels_re.search(segment)
                if cut_match:
                    segment = segment[: cut_match.start()]
                amounts = _extract_amounts(segment)
                if amounts:
                    keyword_amounts.append(amounts[0])
                    found_inline = True

        if not found_inline and idx + 1 < len(lines):
            next_line = lines[idx + 1].lower()
            if not any(h in next_line for h in IGNORE_TOTAL_HINTS):
                next_amounts = _extract_amounts(lines[idx + 1])
                if next_amounts:
                    keyword_amounts.append(next_amounts[0])

    if keyword_amounts:
        # Prefer median-ish value among keyword hits to avoid OCR outlier spikes.
        sorted_amounts = sorted(keyword_amounts)
        return sorted_amounts[len(sorted_amounts) // 2], False

    all_amounts: list[int] = []
    for line in lines:
        low = line.lower()
        if any(hint in low for hint in IGNORE_TOTAL_HINTS):
            continue
        all_amounts.extend([amount for amount in _extract_amounts(line) if amount >= 1000])

    if all_amounts:
        return max(all_amounts), True
    return None, False


def _is_noisy(lines: list[str], total: Optional[int], used_fallback_total: bool) -> bool:
    if not lines:
        return True

    joined = " ".join(lines)
    alnum_count = sum(1 for c in joined if c.isalnum())
    printable_count = sum(1 for c in joined if c.strip())
    ratio = (alnum_count / printable_count) if printable_count else 0

    very_short = len(lines) <= 2
    no_total = total is None or (total is not None and total < 1000)
    weak_text = ratio < 0.45
    weak_fallback = used_fallback_total and (very_short or weak_text)
    return no_total or weak_fallback


def extract_receipt_data(ocr_input: str | Sequence[str]) -> OCRResult:
    lines = _normalize_lines(ocr_input)
    raw_text = "\n".join(lines)

    is_bank_transaction = _detect_bank_transaction(lines)
    total, used_fallback = _extract_total(lines, is_bank_transaction=is_bank_transaction)
    noisy = _is_noisy(lines, total, used_fallback)

    if noisy:
        return OCRResult(
            raw_text=raw_text,
            receipt=None,
            needs_manual_total_confirmation=True,
            reply_text=(
                "Sepertinya struknya agak buram, boleh konfirmasi total belanjanya berapa, Kak?"
            ),
        )

    merchant = _pick_bank_merchant(lines) if is_bank_transaction else _pick_merchant(lines)
    date_match = DATE_RE.search(raw_text)
    if date_match:
        date_text = date_match.group(1)
    else:
        date_word_match = DATE_WORD_RE.search(raw_text)
        date_text = date_word_match.group(1) if date_word_match else "-"
    category = "Transfer/Bank" if is_bank_transaction else _pick_category(merchant, lines)

    receipt = ReceiptExtraction(
        merchant=merchant,
        date_text=date_text,
        total=total or 0,
        category=category,
        used_fallback_total=used_fallback,
        is_bank_transaction=is_bank_transaction,
    )
    source_label = "bukti transaksi bank" if is_bank_transaction else "struk"
    confirmation = (
        f"Wah, {source_label} dari {receipt.merchant} ya! Berhasil dicatat nih:\n\n"
        f"Total: {format_idr(receipt.total)}\n\n"
        f"Kategori: {receipt.category}\n\n"
        f"Tanggal: {receipt.date_text}\n"
        "Mau langsung simpan atau ada yang mau diubah?"
    )
    return OCRResult(
        raw_text=raw_text,
        receipt=receipt,
        needs_manual_total_confirmation=False,
        reply_text=confirmation,
    )


class ReceiptOCR:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def scan_receipt(self, image_bytes: bytes) -> Optional[OCRResult]:
        if not self.enabled:
            return None

        files = {"file": ("receipt.jpg", image_bytes, "image/jpeg")}
        data = {
            "apikey": self.api_key,
            "language": "eng",
            "isOverlayRequired": "false",
            "OCREngine": "2",
            "scale": "true",
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(OCR_ENDPOINT, data=data, files=files)
            response.raise_for_status()
            payload = response.json()

        parsed_results = payload.get("ParsedResults") or []
        if not parsed_results:
            return None

        raw_text = str(parsed_results[0].get("ParsedText", "")).strip()
        if not raw_text:
            return None

        return extract_receipt_data(raw_text)
