from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .db import ExpenseDB


class ExpenseChartService:
    COLOR_PALETTE = [
        "#0F766E",
        "#F97316",
        "#2563EB",
        "#DC2626",
        "#7C3AED",
        "#CA8A04",
        "#0891B2",
        "#BE123C",
        "#65A30D",
        "#4338CA",
    ]

    def __init__(
        self,
        db: ExpenseDB,
        quickchart_url: str = "https://quickchart.io/chart",
        timezone_name: str = "Asia/Jakarta",
    ) -> None:
        self.db = db
        self.quickchart_url = quickchart_url
        self.tz = ZoneInfo(timezone_name)

    async def render_monthly_category_chart(self, user_key: str) -> bytes:
        category_totals = self.db.category_totals_for_period(user_key, "month")
        month_label = datetime.now(self.tz).strftime("%m/%Y")

        if category_totals:
            labels = [category for category, _ in category_totals]
            values = [amount for _, amount in category_totals]
            colors = [self.COLOR_PALETTE[idx % len(self.COLOR_PALETTE)] for idx in range(len(labels))]
            title = f"Pengeluaran bulan ini per kategori ({month_label})"
        else:
            labels = ["Belum ada data"]
            values = [1]
            colors = ["#CBD5E1"]
            title = f"Belum ada pengeluaran bulan ini ({month_label})"

        payload = {
            "version": "4",
            "width": 900,
            "height": 600,
            "format": "png",
            "backgroundColor": "white",
            "chart": {
                "type": "doughnut",
                "data": {
                    "labels": labels,
                    "datasets": [
                        {
                            "data": values,
                            "backgroundColor": colors,
                            "borderColor": "#FFFFFF",
                            "borderWidth": 2,
                        }
                    ],
                },
                "options": {
                    "cutout": "48%",
                    "plugins": {
                        "legend": {"position": "bottom"},
                        "title": {
                            "display": True,
                            "text": title,
                            "font": {"size": 20},
                            "color": "#111827",
                        },
                    },
                },
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.quickchart_url,
                json=payload,
                headers={"Accept": "image/png"},
            )
            response.raise_for_status()
            return response.content

    def build_filename(self) -> str:
        return f"grafik-pengeluaran-{datetime.now(self.tz).strftime('%Y-%m')}.png"
