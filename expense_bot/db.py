from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


@dataclass
class ExpenseRecord:
    id: int
    user_key: str
    item: str
    amount: int
    category: str
    expense_date: date
    created_at: datetime


@dataclass
class PendingReceipt:
    user_key: str
    item: str
    amount: int
    category: str
    expense_date: date
    raw_payload: dict[str, Any]
    is_bank_transaction: bool


class ExpenseDB:
    PERIOD_FILTERS = {
        "today": "expense_date = CURRENT_DATE",
        "week": "expense_date BETWEEN date_trunc('week', CURRENT_DATE)::date AND CURRENT_DATE",
        "month": "expense_date BETWEEN date_trunc('month', CURRENT_DATE)::date AND CURRENT_DATE",
    }

    def __init__(
        self,
        database_url: str,
        timezone_name: str = "Asia/Jakarta",
        min_pool_size: int = 1,
        max_pool_size: int = 5,
    ) -> None:
        self.database_url = database_url.strip()
        self.timezone_name = timezone_name
        self.tz = ZoneInfo(timezone_name)
        self.pool = ConnectionPool(
            conninfo=self.database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=30,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
            configure=self._configure_connection,
        )
        self._opened = False

    def _configure_connection(self, conn) -> None:
        conn.execute("SELECT set_config('TimeZone', %s, false)", (self.timezone_name,))

    def open(self) -> None:
        if self._opened:
            return
        self.pool.open()
        self.pool.wait()
        self._opened = True

    def close(self) -> None:
        if not self._opened:
            return
        self.pool.close()
        self._opened = False

    def ensure_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id BIGSERIAL PRIMARY KEY,
                    user_key TEXT NOT NULL,
                    item TEXT NOT NULL,
                    amount BIGINT NOT NULL CHECK (amount > 0),
                    category TEXT NOT NULL DEFAULT 'Lainnya',
                    expense_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_expenses_user_expense_date
                ON expenses (user_key, expense_date DESC, created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_key TEXT PRIMARY KEY,
                    weekly_budget BIGINT NOT NULL DEFAULT 2100000
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS category_budgets (
                    user_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    limit_amount BIGINT NOT NULL,
                    PRIMARY KEY (user_key, category)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_receipts (
                    user_key TEXT PRIMARY KEY,
                    item TEXT NOT NULL,
                    amount BIGINT NOT NULL,
                    category TEXT NOT NULL,
                    expense_date DATE NOT NULL,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    is_bank_transaction BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

    def add_expense(
        self,
        user_key: str,
        item: str,
        amount: int,
        category: str,
        expense_date: Optional[date] = None,
    ) -> int:
        with self.pool.connection() as conn:
            if expense_date is None:
                row = conn.execute(
                    """
                    INSERT INTO expenses (user_key, item, amount, category)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user_key, item, amount, category),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    INSERT INTO expenses (user_key, item, amount, category, expense_date)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user_key, item, amount, category, expense_date),
                ).fetchone()
        return int(row["id"]) if row else 0

    def list_recent(self, user_key: str, limit: int = 10) -> list[ExpenseRecord]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, user_key, item, amount, category, expense_date, created_at
                FROM expenses
                WHERE user_key = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (user_key, limit),
            ).fetchall()
        return [self._row_to_expense(row) for row in rows]

    def list_for_period(self, user_key: str, period: str) -> list[ExpenseRecord]:
        filter_sql = self.PERIOD_FILTERS[period]
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, user_key, item, amount, category, expense_date, created_at
                FROM expenses
                WHERE user_key = %s
                  AND {filter_sql}
                ORDER BY expense_date DESC, created_at DESC
                """,
                (user_key,),
            ).fetchall()
        return [self._row_to_expense(row) for row in rows]

    def total_for_period(self, user_key: str, period: str) -> int:
        filter_sql = self.PERIOD_FILTERS[period]
        with self.pool.connection() as conn:
            row = conn.execute(
                f"""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM expenses
                WHERE user_key = %s
                  AND {filter_sql}
                """,
                (user_key,),
            ).fetchone()
        return int(row["total"]) if row else 0

    def total_by_category_for_period(self, user_key: str, period: str, category: str) -> int:
        filter_sql = self.PERIOD_FILTERS[period]
        with self.pool.connection() as conn:
            row = conn.execute(
                f"""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM expenses
                WHERE user_key = %s
                  AND category = %s
                  AND {filter_sql}
                """,
                (user_key, category),
            ).fetchone()
        return int(row["total"]) if row else 0

    def category_totals_for_period(self, user_key: str, period: str) -> list[tuple[str, int]]:
        filter_sql = self.PERIOD_FILTERS[period]
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT category, COALESCE(SUM(amount), 0) AS total
                FROM expenses
                WHERE user_key = %s
                  AND {filter_sql}
                GROUP BY category
                ORDER BY total DESC, category ASC
                """,
                (user_key,),
            ).fetchall()
        return [(str(row["category"]), int(row["total"])) for row in rows]

    def delete_by_id(self, user_key: str, expense_id: int) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM expenses WHERE user_key = %s AND id = %s",
                (user_key, expense_id),
            )
        return cur.rowcount > 0

    def clear_user(self, user_key: str) -> int:
        with self.pool.connection() as conn:
            deleted = conn.execute("DELETE FROM expenses WHERE user_key = %s", (user_key,))
            conn.execute("DELETE FROM category_budgets WHERE user_key = %s", (user_key,))
            conn.execute("DELETE FROM pending_receipts WHERE user_key = %s", (user_key,))
        return int(deleted.rowcount)

    def get_weekly_budget(self, user_key: str) -> int:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_key, weekly_budget)
                VALUES (%s, 2100000)
                ON CONFLICT(user_key) DO NOTHING
                """,
                (user_key,),
            )
            row = conn.execute(
                "SELECT weekly_budget FROM user_settings WHERE user_key = %s",
                (user_key,),
            ).fetchone()
        return int(row["weekly_budget"]) if row else 2100000

    def set_weekly_budget(self, user_key: str, amount: int) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_key, weekly_budget)
                VALUES (%s, %s)
                ON CONFLICT(user_key)
                DO UPDATE SET weekly_budget = excluded.weekly_budget
                """,
                (user_key, amount),
            )

    def set_category_budget(self, user_key: str, category: str, limit_amount: int) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO category_budgets (user_key, category, limit_amount)
                VALUES (%s, %s, %s)
                ON CONFLICT(user_key, category)
                DO UPDATE SET limit_amount = excluded.limit_amount
                """,
                (user_key, category, limit_amount),
            )

    def get_category_budget(self, user_key: str, category: str) -> Optional[int]:
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT limit_amount
                FROM category_budgets
                WHERE user_key = %s AND category = %s
                """,
                (user_key, category),
            ).fetchone()
        if not row:
            return None
        return int(row["limit_amount"])

    def list_category_budgets(self, user_key: str) -> list[tuple[str, int]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT category, limit_amount
                FROM category_budgets
                WHERE user_key = %s
                ORDER BY category ASC
                """,
                (user_key,),
            ).fetchall()
        return [(str(row["category"]), int(row["limit_amount"])) for row in rows]

    def save_pending_receipt(self, pending: PendingReceipt) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO pending_receipts (
                    user_key,
                    item,
                    amount,
                    category,
                    expense_date,
                    raw_payload,
                    is_bank_transaction,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT(user_key)
                DO UPDATE SET
                    item = excluded.item,
                    amount = excluded.amount,
                    category = excluded.category,
                    expense_date = excluded.expense_date,
                    raw_payload = excluded.raw_payload,
                    is_bank_transaction = excluded.is_bank_transaction,
                    updated_at = NOW()
                """,
                (
                    pending.user_key,
                    pending.item,
                    pending.amount,
                    pending.category,
                    pending.expense_date,
                    Jsonb(pending.raw_payload),
                    pending.is_bank_transaction,
                ),
            )

    def get_pending_receipt(self, user_key: str) -> Optional[PendingReceipt]:
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT user_key, item, amount, category, expense_date, raw_payload, is_bank_transaction
                FROM pending_receipts
                WHERE user_key = %s
                """,
                (user_key,),
            ).fetchone()
        if not row:
            return None
        return PendingReceipt(
            user_key=str(row["user_key"]),
            item=str(row["item"]),
            amount=int(row["amount"]),
            category=str(row["category"]),
            expense_date=row["expense_date"],
            raw_payload=dict(row["raw_payload"] or {}),
            is_bank_transaction=bool(row["is_bank_transaction"]),
        )

    def clear_pending_receipt(self, user_key: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM pending_receipts WHERE user_key = %s", (user_key,))

    @staticmethod
    def _row_to_expense(row: dict[str, Any]) -> ExpenseRecord:
        return ExpenseRecord(
            id=int(row["id"]),
            user_key=str(row["user_key"]),
            item=str(row["item"]),
            amount=int(row["amount"]),
            category=str(row["category"]),
            expense_date=row["expense_date"],
            created_at=row["created_at"],
        )
