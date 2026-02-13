# CuanBot Telegram (Expense + OCR Struk)

CuanBot adalah chatbot Telegram untuk mencatat pengeluaran, budgeting, split bill, laporan periodik, dan scan struk/bukti transaksi bank.

## Fitur
- Input natural: `Beli kopi 25rb`, `Belanja indomaret 180.000`.
- Kategori otomatis.
- Budget mingguan default `Rp2.100.000` + alert 80%.
- Split bill: `patungan total 450rb untuk 3 orang service 5% pajak 10%`.
- Laporan mingguan/bulanan (Top 3 kategori + sisa budget).
- OCR struk:
  - Merchant dari 3 baris pertama.
  - Tanggal transaksi (`DD/MM/YY` atau `DD-MM-YYYY`).
  - Total prioritas keyword: `TOTAL`, `GRAND TOTAL`, `TOTAL BAYAR`, `NETTO`, `AMOUNT DUE`, `JUMLAH`.
  - Fallback: angka terbesar jika keyword tidak ditemukan.
  - Jika noise tinggi: bot minta konfirmasi manual total.
- OCR bukti transaksi bank:
  - Deteksi transfer/debit/QRIS.
  - Prioritas nominal transfer (`NOMINAL`, `JUMLAH TRANSFER`, `AMOUNT`, `DEBIT`).
  - Abaikan saldo, admin, pajak, kembalian.

## 1) Instalasi dari Nol (Windows CMD)
### A. Siapkan project
```bat
cd /d e:\chatbot
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### B. Siapkan env
```bat
copy .env.example .env
```

Isi `e:\chatbot\.env`:
```env
DB_PATH=data/expenses.db
BOT_TIMEZONE=Asia/Jakarta
TELEGRAM_BOT_TOKEN=ISI_TOKEN_BOTFATHER
OCR_SPACE_API_KEY=OPSIONAL
```

Keterangan:
- `TELEGRAM_BOT_TOKEN` wajib.
- `OCR_SPACE_API_KEY` opsional (untuk scan foto struk langsung di Telegram).

### C. Jalankan lokal
```bat
python telegram_bot.py
```

Jika terminal diam tanpa error, bot aktif dan menunggu chat.

## 2) Cara Buat Token Telegram
1. Buka Telegram, chat `@BotFather`.
2. Kirim `/newbot`.
3. Ikuti instruksi nama dan username bot.
4. Copy token yang diberikan BotFather.
5. Tempel ke `.env` pada `TELEGRAM_BOT_TOKEN=...`.

## 3) Alur OCR Struk di Chat
1. Kirim foto struk ke bot.
2. Bot balas format:
   - Merchant
   - Total
   - Kategori
   - Tanggal
3. Konfirmasi dengan:
   - `simpan`, atau
   - `batal`, atau
   - `ubah total ...`, `ubah kategori ...`, `ubah merchant ...`, `ubah tanggal ...`

Jika struk buram, bot akan kirim:
`Sepertinya struknya agak buram, boleh konfirmasi total belanjanya berapa, Kak?`

Jika foto adalah bukti transaksi bank, bot akan memberi label kategori `Transfer/Bank`.

## 4) Uji Extractor OCR dari Output PaddleOCR
Modul `expense_bot/ocr.py` mendukung input `list[str]` atau teks mentah.

Contoh cepat:
```bat
python -c "from expense_bot.ocr import extract_receipt_data; print(extract_receipt_data(['STARBUCKS','13/02/26','Grand Total 89.000']).reply_text)"
```

## 5) Deploy Online ke Railway (24/7)
### A. Push ke GitHub
```bat
cd /d e:\chatbot
git init
git add .
git commit -m "init cuanbot"
```
Lalu buat repo GitHub dan push.

### B. Deploy di Railway
1. Login ke https://railway.app
2. `New Project` -> `Deploy from GitHub Repo`.
3. Pilih repo `chatbot`.
4. Set start command:
```bash
python telegram_bot.py
```

### C. Environment Variables di Railway
Isi variable:
- `TELEGRAM_BOT_TOKEN=<token>`
- `BOT_TIMEZONE=Asia/Jakarta`
- `DB_PATH=/data/expenses.db`
- `OCR_SPACE_API_KEY=<optional>`

### D. Persistensi Database
1. Tambah `Volume` di service Railway.
2. Mount path ke `/data`.
3. Karena `DB_PATH=/data/expenses.db`, data SQLite tetap aman saat redeploy.

### E. Verifikasi
1. Trigger deploy.
2. Buka Logs sampai status normal (tanpa error restart loop).
3. Uji bot di Telegram:
   - `/start`
   - `Beli kopi 25rb`
   - `/laporan minggu ini`

## 6) Command Bot
- `/help`
- `/total`
- `/total minggu`
- `/total bulan`
- `/list` atau `/list 20`
- `/laporan minggu ini`
- `/laporan bulan ini`
- `/budget`
- `/budget 2500000`
- `/budget kategori Makanan & Minuman 700000`
- `/hapus <id>`
- `/reset ya`

## Keamanan
- Jangan share token bot.
- Jika token bocor, revoke di BotFather (`/revoke`) lalu ganti di env lokal + Railway.
