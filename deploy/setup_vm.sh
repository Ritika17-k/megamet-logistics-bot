#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"

echo "==> Installing Python"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip unzip curl rsync

echo "==> Creating virtualenv + installing requirements"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Data files"
cd "$APP_DIR/data/ops_supplied"
for f in *.example.*; do
  real="${f/.example/}"
  [ -e "$real" ] || { cp "$f" "$real"; echo "   created $real from $f -- replace with your real data"; }
done
cd "$APP_DIR"
[ -e .env ] || { cp .env.example .env; echo "   created .env -- fill in WHATSAPP_TOKEN etc."; }

echo "==> Installing systemd service (auto-start on boot, auto-restart on crash)"
sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__USER__#$USER_NAME#g" \
    "$APP_DIR/deploy/megamet-webhook.service" | sudo tee /etc/systemd/system/megamet-webhook.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now megamet-webhook

echo
echo "Webhook is running on 127.0.0.1:8000. To expose it to Meta over HTTPS, either:"
echo "  - ngrok: install ngrok, 'ngrok config add-authtoken <TOKEN>', set NGROK_DOMAIN in .env,"
echo "    install deploy/megamet-ngrok.service the same way and enable it; or"
echo "  - a reverse proxy (Caddy/nginx) with your own domain."
echo "Logs:  journalctl -u megamet-webhook -f"
