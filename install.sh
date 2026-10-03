#!/usr/bin/env bash
# نصب نسخه‌ی پایتون روی سرور لینوکسی (Ubuntu/Debian) — با systemd
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"

command -v python3 >/dev/null || { echo "❌ python3 نصب نیست (نسخه 3.10 یا بالاتر)."; exit 1; }

if ! python3 -m venv venv 2>/dev/null; then
  echo "▶ نصب python3-venv ..."
  sudo apt-get update && sudo apt-get install -y python3-venv python3-pip
  python3 -m venv venv
fi

./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
mkdir -p data

if [ ! -f .env ]; then
  cp .env.example .env
  echo "✅ فایل .env ساخته شد. حتماً با nano ویرایشش کنید:  nano .env"
fi

sed "s|__DIR__|$DIR|g; s|__USER__|$RUN_USER|g" deploy/hamid-bot.service | sudo tee /etc/systemd/system/hamid-bot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable hamid-bot

echo
echo "✅ نصب تمام شد. بعد از ویرایش .env ربات را اجرا کنید:"
echo "   sudo systemctl start hamid-bot"
echo "   sudo journalctl -u hamid-bot -f     # دیدن لاگ"
