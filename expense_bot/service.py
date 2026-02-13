from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import ExpenseDB
from .parser import (
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
            "- /total (hari ini)\n"
            "- /total minggu\n"
            "- /total bulan\n"
            "- /laporan minggu ini\n"
            "- /list 10\n"
            "- /budget (lihat budget mingguan)\n"
            "- /budget 2500000 (atur budget mingguan)\n"
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
        tokenized = normalized.replace("/", "", 1).split()
        command = tokenized[0] if tokenized else ""
        args = tokenized[1:]

        if command in {"start", "help"}:
            return self.help_text()
        if command == "total":
            return self._reply_total(user_key, args)
        if command == "list":
            return self._reply_list(user_key, args)
        if command == "hapus":
            return self._reply_delete(user_key, args)
        if command == "reset":
            return self._reply_reset(user_key, args)
        if command in {"budget", "anggaran"}:
            return self._reply_budget(user_key, clean, args)
        if command in {"laporan", "report"}:
            return self._reply_report(user_key, clean, args)

        if normalized.startswith("atur budget") or normalized.startswith("set budget"):
            return self._reply_budget(user_key, clean, args=[])
        if "laporan minggu" in normalized:
            return self._render_report(user_key, period="week")
        if "laporan bulan" in normalized:
            return self._render_report(user_key, period="month")
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

    def record_expense(self, user_key: str, item: str, amount: int, category: str) -> str:
        expense_id = self.db.add_expense(
            user_key=user_key, item=item, amount=amount, category=category
        )
        confirmation = (
            f"Siap! {item} senilai {format_idr(amount)} sudah masuk catatan {category}. ✅\n"
            f"ID transaksi: #{expense_id}"
        )
        alerts = self._build_budget_alerts(user_key=user_key, category=category)
        if alerts:
            return confirmation + "\n\n" + alerts
        return confirmation

    def _reply_total(self, user_key: str, args: list[str]) -> str:
        joined = " ".join(args).lower()
        if "bulan" in joined:
            total = self._total_month(user_key)
            return f"Total pengeluaran bulan ini: {format_idr(total)}"
        if "minggu" in joined:
            total = self._total_week(user_key)
            return f"Total pengeluaran minggu ini: {format_idr(total)}"
        total = self._total_today(user_key)
        return f"Total pengeluaran hari ini: {format_idr(total)}"

    def _reply_list(self, user_key: str, args: list[str]) -> str:
        limit = 10
        if args and args[0].isdigit():
            limit = max(1, min(50, int(args[0])))

        records = self.db.list_recent(user_key=user_key, limit=limit)
        if not records:
            return "Belum ada transaksi."

        rows = []
        for rec in records:
            local_dt = datetime.fromisoformat(rec.created_at).astimezone(self.tz)
            dt_txt = local_dt.strftime("%d-%m %H:%M")
            rows.append(
                f"#{rec.id} | {dt_txt} | {rec.item} | {format_idr(rec.amount)} | {rec.category}"
            )
        return "Transaksi terakhir:\n" + "\n".join(rows)

    def _reply_delete(self, user_key: str, args: list[str]) -> str:
        if not args:
            return "Gunakan: /hapus <id>. Contoh: /hapus 10"
        if not args[0].isdigit():
            return "ID transaksi harus angka. Contoh: /hapus 10"

        expense_id = int(args[0])
        deleted = self.db.delete_by_id(user_key=user_key, expense_id=expense_id)
        if not deleted:
            return f"Transaksi #{expense_id} tidak ditemukan."
        return f"Transaksi #{expense_id} dihapus."

    def _reply_reset(self, user_key: str, args: list[str]) -> str:
        if not args or args[0] != "ya":
            return "Untuk konfirmasi, gunakan: /reset ya"
        count = self.db.clear_user(user_key=user_key)
        return f"Semua data kamu dihapus ({count} transaksi)."

    def _reply_budget(self, user_key: str, raw_text: str, args: list[str]) -> str:
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

    def _reply_report(self, user_key: str, raw_text: str, args: list[str]) -> str:
        joined = " ".join(args).lower() if args else raw_text.lower()
        if "bulan" in joined:
            return self._render_report(user_key, period="month")
        return self._render_report(user_key, period="week")

    def _render_report(self, user_key: str, period: str) -> str:
        if period == "month":
            start_utc, end_utc = self._local_month_range_utc()
            total = self._total_month(user_key)
            title = "Laporan bulan ini"
            budget = self.db.get_weekly_budget(user_key) * 4
            budget_label = "Sisa estimasi anggaran bulanan"
        else:
            start_utc, end_utc = self._local_week_range_utc()
            total = self._total_week(user_key)
            title = "Laporan minggu ini"
            budget = self.db.get_weekly_budget(user_key)
            budget_label = "Sisa anggaran mingguan"

        top3 = self.db.top_categories_between(
            user_key=user_key,
            start_iso=start_utc.isoformat(),
            end_iso=end_utc.isoformat(),
            limit=3,
        )
        remaining = budget - total

        lines = [f"{title} 📊", f"Total pengeluaran: {format_idr(total)}", "Top 3 kategori:"]
        if not top3:
            lines.append("1. Belum ada transaksi.")
        else:
            for idx, (category, amount) in enumerate(top3, start=1):
                lines.append(f"{idx}. {category}: {format_idr(amount)}")
        if remaining >= 0:
            lines.append(f"{budget_label}: {format_idr(remaining)}")
        else:
            lines.append(f"{budget_label}: -{format_idr(abs(remaining))} (melewati budget)")
        return "\n".join(lines)

    def _reply_split_bill(self, split) -> str:
        per_person = int(math.ceil(split.grand_total / split.people))
        lines = [
            "Mode patungan aktif 🤝",
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
        total_week = self._total_week(user_key)
        if weekly_budget > 0:
            ratio = total_week / weekly_budget
            if ratio >= 1:
                alerts.append(
                    f"Budget mingguan terlewati ({format_idr(total_week)} / {format_idr(weekly_budget)}). "
                    "Gas rem dikit ya 😄"
                )
            elif ratio >= 0.8:
                alerts.append(
                    f"Budget mingguan sudah {int(ratio * 100)}% "
                    f"({format_idr(total_week)} / {format_idr(weekly_budget)})."
                )

        category_budget = self.db.get_category_budget(user_key, category)
        if category_budget is None:
            category_budget = self._default_category_budget(weekly_budget)
        category_total = self._total_week_by_category(user_key, category)
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

    def _local_day_range_utc(self) -> tuple[datetime, datetime]:
        now_local = datetime.now(self.tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    def _local_week_range_utc(self) -> tuple[datetime, datetime]:
        now_local = datetime.now(self.tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=now_local.weekday()
        )
        end_local = start_local + timedelta(days=7)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    def _local_month_range_utc(self) -> tuple[datetime, datetime]:
        now_local = datetime.now(self.tz)
        start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start_local.month == 12:
            next_month = start_local.replace(year=start_local.year + 1, month=1)
        else:
            next_month = start_local.replace(month=start_local.month + 1)
        return start_local.astimezone(timezone.utc), next_month.astimezone(timezone.utc)

    def _total_today(self, user_key: str) -> int:
        start_utc, end_utc = self._local_day_range_utc()
        return self.db.total_between(user_key, start_utc.isoformat(), end_utc.isoformat())

    def _total_week(self, user_key: str) -> int:
        start_utc, end_utc = self._local_week_range_utc()
        return self.db.total_between(user_key, start_utc.isoformat(), end_utc.isoformat())

    def _total_month(self, user_key: str) -> int:
        start_utc, end_utc = self._local_month_range_utc()
        return self.db.total_between(user_key, start_utc.isoformat(), end_utc.isoformat())

    def _total_week_by_category(self, user_key: str, category: str) -> int:
        start_utc, end_utc = self._local_week_range_utc()
        return self.db.total_by_category_between(
            user_key=user_key,
            category=category,
            start_iso=start_utc.isoformat(),
            end_iso=end_utc.isoformat(),
        )
