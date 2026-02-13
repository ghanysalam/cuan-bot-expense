from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class ExpenseRecord:
    id: int
    user_key: str
    item: str
    amount: int
    category: str
    created_at: str


class ExpenseDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_key TEXT NOT NULL,
                    item TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Lainnya',
                    created_at TEXT NOT NULL
                )
                """
            )
            cols = [
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(expenses)").fetchall()
            ]
            if "category" not in cols:
                conn.execute(
                    "ALTER TABLE expenses ADD COLUMN category TEXT NOT NULL DEFAULT 'Lainnya'"
                )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_key TEXT PRIMARY KEY,
                    weekly_budget INTEGER NOT NULL DEFAULT 2100000
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS category_budgets (
                    user_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    limit_amount INTEGER NOT NULL,
                    PRIMARY KEY (user_key, category)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_expenses_user_created
                ON expenses (user_key, created_at)
                """
            )
            conn.commit()

    def _reset_sequence_if_table_empty(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT COUNT(*) AS total FROM expenses").fetchone()
        if row and int(row["total"]) == 0:
            # Reset AUTOINCREMENT counter when table is empty so next id starts from 1.
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'expenses'")

    def add_expense(self, user_key: str, item: str, amount: int, category: str) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO expenses (user_key, item, amount, category, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_key, item, amount, category, created_at),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_recent(self, user_key: str, limit: int = 10) -> List[ExpenseRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_key, item, amount, category, created_at
                FROM expenses
                WHERE user_key = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_key, limit),
            ).fetchall()
        return [
            ExpenseRecord(
                id=int(row["id"]),
                user_key=str(row["user_key"]),
                item=str(row["item"]),
                amount=int(row["amount"]),
                category=str(row["category"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def total_between(self, user_key: str, start_iso: str, end_iso: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM expenses
                WHERE user_key = ?
                  AND created_at >= ?
                  AND created_at < ?
                """,
                (user_key, start_iso, end_iso),
            ).fetchone()
        return int(row["total"]) if row else 0

    def total_by_category_between(
        self, user_key: str, category: str, start_iso: str, end_iso: str
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM expenses
                WHERE user_key = ?
                  AND category = ?
                  AND created_at >= ?
                  AND created_at < ?
                """,
                (user_key, category, start_iso, end_iso),
            ).fetchone()
        return int(row["total"]) if row else 0

    def top_categories_between(
        self, user_key: str, start_iso: str, end_iso: str, limit: int = 3
    ) -> List[tuple[str, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT category, COALESCE(SUM(amount), 0) AS total
                FROM expenses
                WHERE user_key = ?
                  AND created_at >= ?
                  AND created_at < ?
                GROUP BY category
                ORDER BY total DESC
                LIMIT ?
                """,
                (user_key, start_iso, end_iso, limit),
            ).fetchall()
        return [(str(row["category"]), int(row["total"])) for row in rows]

    def delete_by_id(self, user_key: str, expense_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM expenses WHERE user_key = ? AND id = ?",
                (user_key, expense_id),
            )
            self._reset_sequence_if_table_empty(conn)
            conn.commit()
        return cur.rowcount > 0

    def clear_user(self, user_key: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM expenses WHERE user_key = ?", (user_key,))
            conn.execute("DELETE FROM category_budgets WHERE user_key = ?", (user_key,))
            self._reset_sequence_if_table_empty(conn)
            conn.commit()
        return int(cur.rowcount)

    def get_weekly_budget(self, user_key: str) -> int:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_key, weekly_budget)
                VALUES (?, 2100000)
                ON CONFLICT(user_key) DO NOTHING
                """,
                (user_key,),
            )
            row = conn.execute(
                "SELECT weekly_budget FROM user_settings WHERE user_key = ?",
                (user_key,),
            ).fetchone()
            conn.commit()
        return int(row["weekly_budget"]) if row else 2100000

    def set_weekly_budget(self, user_key: str, amount: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (user_key, weekly_budget)
                VALUES (?, ?)
                ON CONFLICT(user_key)
                DO UPDATE SET weekly_budget = excluded.weekly_budget
                """,
                (user_key, amount),
            )
            conn.commit()

    def set_category_budget(self, user_key: str, category: str, limit_amount: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO category_budgets (user_key, category, limit_amount)
                VALUES (?, ?, ?)
                ON CONFLICT(user_key, category)
                DO UPDATE SET limit_amount = excluded.limit_amount
                """,
                (user_key, category, limit_amount),
            )
            conn.commit()

    def get_category_budget(self, user_key: str, category: str) -> Optional[int]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT limit_amount
                FROM category_budgets
                WHERE user_key = ? AND category = ?
                """,
                (user_key, category),
            ).fetchone()
        if not row:
            return None
        return int(row["limit_amount"])

    def list_category_budgets(self, user_key: str) -> List[tuple[str, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT category, limit_amount
                FROM category_budgets
                WHERE user_key = ?
                ORDER BY category ASC
                """,
                (user_key,),
            ).fetchall()
        return [(str(row["category"]), int(row["limit_amount"])) for row in rows]
