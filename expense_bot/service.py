from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date
from zoneinfo import ZoneInfo

from .db import ExpenseDB, PendingReceipt
from .parser import (
    format_date_id,
    format_idr,
    normalize_category,
    parse_amount_from_text,
    parse_expense_input,
    parse_split_bill,
)


class ExpenseService:
    def __init__(self, db: ExpenseDB, timezone_name: str = "Asia/Jakarta") -> None:
        self.db = db
        self.tz = ZoneInfo(timezone_name)

    def help_text(self) -> str:
        return (
            "Halo, aku CuanBot. Catat pengeluaran jadi cepat dan rapi.\n\n"
            "Contoh input:\n"
            "- Beli kopi 25rb\n"
            "- Bayar listrik 450000 kategori Tagihan\n\n"
            "Perintah utama:\n"
            "- /total\n"
            "- /total_hari_ini\n"
            "- /total_minggu\n"
            "- /total_bulan\n"
            "- /list 10\n"
            "- /grafik\n"
            "- /budget\n"
            "- /budget 2500000\n"
            "- /budget kategori Makanan & Minuman 700000\n"
            "- /hapus <id>\n"
            "- /reset ya\n\n"
            "Fitur tambahan:\n"
            "- Split bill: ketik kalimat dengan 'patungan' atau 'split bill'\n"
            "- Scan struk: kirim foto struk lalu konfirmasi simpan"
        )

    def handle_text(self, user_key: str, text: str) -> str:
        clean = text.strip()
        if not clean:
            return "Kirim item + nominal ya. Contoh: `beli kopi 25rb`"

        normalized = clean.lower().strip()
        if normalized in {"start", "help", "/start", "/help"}:
            return self.help_text()
        if "laporan minggu" in normalized:
            return "Perintah laporan minggu sudah dihapus. Pakai `/total_minggu` ya."
        if "laporan bulan" in normalized or "laporan bulanan" in normalized:
            return "Perintah laporan bulan sudah dihapus. Pakai `/total_bulan` ya."
        if any(
            keyword in normalized
            for keyword in ("investasi", "crypto", "kripto", "forex", "leverage", "futures")
        ):
            return (
                "Aku fokus bantu pencatatan, budget, dan penghematan dulu ya.\n"
                "Kalau mau, aku bisa bantu hitung pos pengeluaran yang bisa dipangkas."
            )

        split = parse_split_bill(clean)
        if split:
            return self._reply_split_bill(split)

        parsed = parse_expense_input(clean)
        if parsed:
            return self.record_expense(
                user_key=user_key,
                item=parsed.item,
                amount=parsed.amount,
                category=parsed.category,
            )

        return (
            "Formatnya belum kebaca.\n"
            "Coba: `beli kopi 25rb` atau `/help` untuk lihat contoh lengkap."
        )

    def record_expense(
        self,
        user_key: str,
        item: str,
        amount: int,
        category: str,
        expense_date: date | None = None,
    ) -> str:
        expense_id = self.db.add_expense(
            user_key=user_key,
            item=item,
            amount=amount,
            category=category,
            expense_date=expense_date,
        )
        confirmation = (
            f"Siap! {item} senilai {format_idr(amount)} sudah masuk catatan {category}.\n"
            f"ID transaksi: #{expense_id}"
        )
        alerts = self._build_budget_alerts(user_key=user_key, category=category)
        if alerts:
            return confirmation + "\n\n" + alerts
        return confirmation

    def render_summary(self, user_key: str) -> str:
        today_total = self.db.total_for_period(user_key, "today")
        week_total = self.db.total_for_period(user_key, "week")
        month_total = self.db.total_for_period(user_key, "month")
        month_breakdown = self._category_breakdown_text(
            self.db.category_totals_for_period(user_key, "month")
        )
        return (
            "Ringkasan total:\n"
            f"- Hari ini: {format_idr(today_total)}\n"
            f"- Minggu ini: {format_idr(week_total)}\n"
            f"- Bulan ini: {format_idr(month_total)}\n"
            f"{month_breakdown}"
        )

    def render_period_report(self, user_key: str, period: str) -> list[str]:
        records = self.db.list_for_period(user_key, period)
        period_label = {"today": "hari ini", "week": "minggu ini", "month": "bulan ini"}[period]

        if not records:
            return [f"Belum ada transaksi untuk {period_label}."]

        lines = [f"Daftar pengeluaran {period_label} ({len(records)} transaksi):"]
        total = 0
        category_totals: dict[str, int] = defaultdict(int)
        for rec in records:
            created_local = rec.created_at.astimezone(self.tz)
            total += rec.amount
            category_totals[rec.category] += rec.amount
            lines.append(
                (
                    f"#{rec.id} | {format_date_id(rec.expense_date)} | "
                    f"{created_local.strftime('%H:%M')} | {rec.item} | "
                    f"{format_idr(rec.amount)} | {rec.category}"
                )
            )

        lines.append("")
        lines.append(f"Total {period_label}: {format_idr(total)}")
        lines.append("Total per kategori:")
        for category, amount in sorted(category_totals.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- {category}: {format_idr(amount)}")
        return self._chunk_lines(lines)

    def render_recent_list(self, user_key: str, limit: int = 10) -> list[str]:
        records = self.db.list_recent(user_key=user_key, limit=limit)
        if not records:
            return ["Belum ada transaksi."]

        lines = [f"Transaksi terakhir ({len(records)} data):"]
        listed_total = 0
        category_totals: dict[str, int] = defaultdict(int)
        for rec in records:
            local_dt = rec.created_at.astimezone(self.tz)
            listed_total += rec.amount
            category_totals[rec.category] += rec.amount
            lines.append(
                (
                    f"#{rec.id} | {format_date_id(rec.expense_date)} {local_dt.strftime('%H:%M')} | "
                    f"{rec.item} | {format_idr(rec.amount)} | {rec.category}"
                )
            )

        lines.append("")
        lines.append(f"Total dari daftar ini: {format_idr(listed_total)}")
        lines.append("Total per kategori (dari daftar ini):")
        for category, total in sorted(category_totals.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- {category}: {format_idr(total)}")
        return self._chunk_lines(lines)

    def reply_delete(self, user_key: str, args: list[str]) -> str:
        if not args:
            return "Gunakan: /hapus <id>. Contoh: /hapus 10"
        if not args[0].isdigit():
            return "ID transaksi harus angka. Contoh: /hapus 10"

        expense_id = int(args[0])
        deleted = self.db.delete_by_id(user_key=user_key, expense_id=expense_id)
        if not deleted:
            return f"Transaksi #{expense_id} tidak ditemukan."
        return f"Transaksi #{expense_id} dihapus."

    def reply_reset(self, user_key: str, args: list[str]) -> str:
        if not args or args[0] != "ya":
            return "Untuk konfirmasi, gunakan: /reset ya"
        count = self.db.clear_user(user_key=user_key)
        return f"Semua data kamu dihapus ({count} transaksi)."

    def reply_budget(self, user_key: str, raw_text: str, args: list[str]) -> str:
        text_lower = raw_text.lower()

        if re.search(r"(?i)budget\s+kategori", raw_text):
            match = re.search(
                r"(?i)budget\s+kategori\s+(.+?)\s+((?:rp\.?\s*)?\d[\d.,]*(?:\s*(?:rb|ribu|k|jt|juta))?)$",
                raw_text,
            )
            if not match:
                return "Format budget kategori: /budget kategori <nama kategori> <nominal>"
            category = normalize_category(match.group(1))
            amount = parse_amount_from_text(match.group(2))
            if not amount or amount <= 0:
                return "Nominal budget kategori tidak valid."
            self.db.set_category_budget(user_key=user_key, category=category, limit_amount=amount)
            return f"Budget kategori {category} diset ke {format_idr(amount)} per minggu."

        amount = None
        if args:
            amount = parse_amount_from_text(" ".join(args))
        if amount is None:
            amount = parse_amount_from_text(text_lower.replace("atur", "").replace("set", ""))

        if amount and amount > 0:
            self.db.set_weekly_budget(user_key=user_key, amount=amount)
            return f"Budget mingguan kamu sekarang {format_idr(amount)}."

        weekly_budget = self.db.get_weekly_budget(user_key)
        category_budgets = self.db.list_category_budgets(user_key)
        if not category_budgets:
            return (
                f"Budget mingguan saat ini: {format_idr(weekly_budget)}.\n"
                "Belum ada budget kategori khusus.\n"
                "Contoh set: /budget kategori Makanan & Minuman 700000"
            )

        lines = [f"Budget mingguan: {format_idr(weekly_budget)}", "Budget kategori:"]
        for category, limit_amount in category_budgets:
            lines.append(f"- {category}: {format_idr(limit_amount)}")
        return "\n".join(lines)

    def monthly_category_totals(self, user_key: str) -> list[tuple[str, int]]:
        return self.db.category_totals_for_period(user_key, "month")

    def save_pending_receipt(self, pending: PendingReceipt) -> None:
        self.db.save_pending_receipt(pending)

    def get_pending_receipt(self, user_key: str) -> PendingReceipt | None:
        return self.db.get_pending_receipt(user_key)

    def clear_pending_receipt(self, user_key: str) -> None:
        self.db.clear_pending_receipt(user_key)

    def update_pending_receipt(self, pending: PendingReceipt) -> None:
        self.db.save_pending_receipt(pending)

    def _reply_split_bill(self, split) -> str:
        per_person = int(math.ceil(split.grand_total / split.people))
        lines = [
            "Mode patungan aktif",
            f"Subtotal: {format_idr(split.subtotal)}",
            f"Service: {format_idr(split.service_amount)}",
            f"Pajak: {format_idr(split.tax_amount)}",
            f"Total akhir: {format_idr(split.grand_total)}",
            f"Per orang ({split.people} orang): {format_idr(per_person)}",
            "",
            "Teks tagih (copy):",
            (
                f"`Patungan ya. Total {format_idr(split.grand_total)} untuk {split.people} orang, "
                f"jadi per orang {format_idr(per_person)}.`"
            ),
        ]
        return "\n".join(lines)

    def _build_budget_alerts(self, user_key: str, category: str) -> str:
        alerts: list[str] = []

        weekly_budget = self.db.get_weekly_budget(user_key)
        total_week = self.db.total_for_period(user_key, "week")
        if weekly_budget > 0:
            ratio = total_week / weekly_budget
            if ratio >= 1:
                alerts.append(
                    f"Budget mingguan terlewati ({format_idr(total_week)} / {format_idr(weekly_budget)}). "
                    "Gas rem dikit ya."
                )
            elif ratio >= 0.8:
                alerts.append(
                    f"Budget mingguan sudah {int(ratio * 100)}% "
                    f"({format_idr(total_week)} / {format_idr(weekly_budget)})."
                )

        category_budget = self.db.get_category_budget(user_key, category)
        if category_budget is None:
            category_budget = self._default_category_budget(weekly_budget)
        category_total = self.db.total_by_category_for_period(user_key, "week", category)
        if category_budget > 0:
            ratio = category_total / category_budget
            if ratio >= 1:
                alerts.append(
                    f"Kategori {category} sudah lewat budget "
                    f"({format_idr(category_total)} / {format_idr(category_budget)})."
                )
            elif ratio >= 0.8:
                alerts.append(
                    f"Kategori {category} sudah {int(ratio * 100)}% dari limit "
                    f"({format_idr(category_total)} / {format_idr(category_budget)})."
                )

        return "\n".join(alerts)

    @staticmethod
    def _default_category_budget(weekly_budget: int) -> int:
        return max(300000, int(weekly_budget * 0.3))

    @staticmethod
    def _category_breakdown_text(category_totals: list[tuple[str, int]]) -> str:
        if not category_totals:
            return "Belum ada pengeluaran pada periode ini."
        rows = [f"- {category}: {format_idr(amount)}" for category, amount in category_totals]
        return "Total per kategori:\n" + "\n".join(rows)

    @staticmethod
    def _chunk_lines(lines: list[str], max_chars: int = 3500) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        for line in lines:
            candidate = "\n".join(current + [line]).strip()
            if current and len(candidate) > max_chars:
                chunks.append("\n".join(current).strip())
                current = [line]
                continue
            current.append(line)
        if current:
            chunks.append("\n".join(current).strip())
        return chunks
