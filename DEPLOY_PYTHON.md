# استقرار نسخه‌ی Python (روی سرور لینوکسی با systemd)

پیش‌نیاز: Ubuntu/Debian با Python 3.10+ و دسترسی `sudo`.

## ۱) آپلود در گیت‌هاب و دریافت روی سرور
```bash
# روی کامپیوتر خودتان (یک‌بار): محتوای این پوشه را در ریپوی گیت‌هاب commit/push کنید
# روی سرور:
git clone https://github.com/Akhavanatom33/Hamid_AUTO_PLUS.git
cd Hamid_AUTO_PLUS
```
> فایل `.env` و پوشه‌ی `data/` در `.gitignore` هستند و روی گیت‌هاب نمی‌روند (توکن و دیتابیس امن می‌مانند).

## ۲) نصب
```bash
bash install.sh
nano .env          # BOT_TOKEN, ADMIN_IDS, EYLAN_API_KEY ... را پر کنید
```
⚠️ **اولین آیدی در `ADMIN_IDS` همان «اخوان» است** (سود اخوان فقط برای او دیده می‌شود).

## ۳) اجرا
```bash
sudo systemctl start hamid-bot
sudo systemctl status hamid-bot
sudo journalctl -u hamid-bot -f      # لاگ زنده
```
ربات بعد از ریبوت سرور خودش بالا می‌آید.

## ۴) برگرداندن دیتابیس قبلی
داخل تلگرام: `/admin` ← **📤 ارسال دیتابیس** ← فایل قبلی را به‌صورت File بفرستید ← **✅ تأیید**.
(یا فایل را با نام `shop.sqlite3` در پوشه‌ی `data/` بگذارید و `sudo systemctl restart hamid-bot` بزنید.)

## ۵) آپدیت‌های بعدی
```bash
cd Hamid_AUTO_PLUS && git pull
./venv/bin/pip install -r requirements.txt
sudo systemctl restart hamid-bot
```
قیمت‌های تغییرداده‌شده و سود اخوان داخل دیتابیس‌اند و با آپدیت از بین نمی‌روند.

## بکاپ
دکمه‌ی **📥 دریافت دیتابیس** در پنل مدیریت هر زمان یک بکاپ کامل می‌فرستد.
