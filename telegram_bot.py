from __future__ import annotations

import logging
import os
from io import BytesIO

from dotenv import load_dotenv
from telegram.error import Conflict
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from expense_bot.db import ExpenseDB
from expense_bot.ocr import ReceiptOCR
from expense_bot.parser import format_idr, parse_amount_from_text
from expense_bot.service import ExpenseService


load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/expenses.db")
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "Asia/Jakarta")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "")
OCR_DEBUG = os.getenv("OCR_DEBUG", "0") == "1"

service = ExpenseService(db=ExpenseDB(DB_PATH), timezone_name=BOT_TIMEZONE)
receipt_ocr = ReceiptOCR(api_key=OCR_SPACE_API_KEY)
logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)


def _user_key(update: Update) -> str:
    user = update.effective_user
    return f"tg:{user.id}" if user else "tg:unknown"


def _build_command_text(command: str, args: list[str]) -> str:
    return f"/{command} {' '.join(args)}".strip()


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(service.help_text())


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(service.help_text())


async def total_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    response = service.handle_text(_user_key(update), _build_command_text("total", context.args))
    await update.message.reply_text(response)


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    response = service.handle_text(_user_key(update), _build_command_text("list", context.args))
    await update.message.reply_text(response)


async def hapus_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    response = service.handle_text(_user_key(update), _build_command_text("hapus", context.args))
    await update.message.reply_text(response)


async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    response = service.handle_text(_user_key(update), _build_command_text("reset", context.args))
    await update.message.reply_text(response)


async def budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    response = service.handle_text(_user_key(update), _build_command_text("budget", context.args))
    await update.message.reply_text(response)


async def laporan_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    response = service.handle_text(_user_key(update), _build_command_text("laporan", context.args))
    await update.message.reply_text(response)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return

    if not receipt_ocr.enabled:
        await update.message.reply_text(
            "Fitur scan struk belum aktif. Isi `OCR_SPACE_API_KEY` di environment dulu ya."
        )
        return

    largest_photo = update.message.photo[-1]
    tg_file = await largest_photo.get_file()
    buffer = BytesIO()
    await tg_file.download_to_memory(out=buffer)

    try:
        result = await receipt_ocr.scan_receipt(buffer.getvalue())
    except Exception:
        await update.message.reply_text(
            "Maaf, struknya belum kebaca sekarang. Coba foto lebih terang atau kirim ulang."
        )
        return

    if not result:
        await update.message.reply_text(
            "Aku belum bisa baca total struknya. Coba foto lebih jelas atau catat manual ya."
        )
        return

    if OCR_DEBUG:
        logger.info("OCR raw text:\n%s", result.raw_text)

    if result.needs_manual_total_confirmation or not result.receipt:
        await update.message.reply_text(result.reply_text)
        return

    default_item = (
        f"Transfer ke {result.receipt.merchant}"
        if result.receipt.is_bank_transaction
        else f"Belanja {result.receipt.merchant}"
    )
    context.user_data["pending_receipt"] = {
        "item": default_item,
        "amount": result.receipt.total,
        "category": result.receipt.category,
        "date_text": result.receipt.date_text,
        "is_bank_transaction": result.receipt.is_bank_transaction,
    }
    await update.message.reply_text(result.reply_text)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user_key = _user_key(update)
    text = update.message.text.strip()
    pending = context.user_data.get("pending_receipt")
    low = text.lower()

    if pending:
        if low in {"simpan", "ya", "y", "oke", "ok"}:
            response = service.record_expense(
                user_key=user_key,
                item=str(pending["item"]),
                amount=int(pending["amount"]),
                category=str(pending["category"]),
            )
            context.user_data.pop("pending_receipt", None)
            await update.message.reply_text(response)
            return
        if low.startswith("ubah total"):
            new_amount = parse_amount_from_text(text)
            if not new_amount or new_amount <= 0:
                await update.message.reply_text("Format ubah total: `ubah total 125000`")
                return
            pending["amount"] = new_amount
            await update.message.reply_text(
                f"Siap, total diubah jadi {format_idr(new_amount)}. Balas `simpan` atau lanjut ubah."
            )
            return
        if low.startswith("ubah kategori"):
            new_category = text[len("ubah kategori") :].strip()
            if not new_category:
                await update.message.reply_text("Format ubah kategori: `ubah kategori Makanan & Minuman`")
                return
            pending["category"] = new_category
            await update.message.reply_text(
                f"Siap, kategori diubah jadi {new_category}. Balas `simpan` atau lanjut ubah."
            )
            return
        if low.startswith("ubah merchant"):
            new_merchant = text[len("ubah merchant") :].strip()
            if not new_merchant:
                await update.message.reply_text("Format ubah merchant: `ubah merchant Nama Toko`")
                return
            pending["item"] = f"Belanja {new_merchant}"
            await update.message.reply_text(
                f"Siap, merchant diubah ke {new_merchant}. Balas `simpan` atau lanjut ubah."
            )
            return
        if low.startswith("ubah tanggal"):
            new_date = text[len("ubah tanggal") :].strip()
            if not new_date:
                await update.message.reply_text("Format ubah tanggal: `ubah tanggal 13/02/2026`")
                return
            pending["date_text"] = new_date
            await update.message.reply_text(
                f"Siap, tanggal diubah jadi {new_date}. Balas `simpan` atau lanjut ubah."
            )
            return
        if low in {"batal", "tidak", "ga", "gak"}:
            context.user_data.pop("pending_receipt", None)
            await update.message.reply_text("Oke, struknya tidak jadi disimpan.")
            return
        await update.message.reply_text(
            "Balas `simpan` untuk simpan struk, `batal` untuk batal, "
            "atau `ubah total/kategori/merchant/tanggal ...`."
        )
        return

    response = service.handle_text(user_key, text)
    await update.message.reply_text(response)


async def post_init(app: Application) -> None:
    # Ensure polling mode is clean even if webhook was set in the past.
    await app.bot.delete_webhook(drop_pending_updates=False)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.error(
            "Token bot sedang dipakai instance lain (Conflict). "
            "Pastikan hanya satu proses telegram_bot.py yang aktif."
        )
        return
    logger.exception("Unhandled error while processing update", exc_info=context.error)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum diisi di .env")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("total", total_handler))
    app.add_handler(CommandHandler("list", list_handler))
    app.add_handler(CommandHandler("hapus", hapus_handler))
    app.add_handler(CommandHandler("reset", reset_handler))
    app.add_handler(CommandHandler("budget", budget_handler))
    app.add_handler(CommandHandler("laporan", laporan_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    app.run_polling()


if __name__ == "__main__":
    main()
