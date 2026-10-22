#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "Run this script as the normal Ubuntu user, without sudo." >&2
    exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_USER="$(id -un)"
ENV_FILE="$APP_DIR/.env"

echo "[1/7] Checking .env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$APP_DIR/.env.example" "$ENV_FILE"
    echo "Created $ENV_FILE. Fill it in, then run this installer again." >&2
    exit 1
fi

if grep -Eq '^(TELEGRAM_BOT_TOKEN|HELIUS_API_KEY)=($|PASTE_)' "$ENV_FILE"; then
    echo "Fill TELEGRAM_BOT_TOKEN and HELIUS_API_KEY in $ENV_FILE first." >&2
    exit 1
fi

# Secrets remain plain text on disk, so only the service account may read them.
chmod 700 "$APP_DIR"
chmod 600 "$ENV_FILE"

echo "[2/7] Installing Ubuntu packages"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip curl ca-certificates gnupg

if ! command -v ngrok >/dev/null 2>&1; then
    echo "[3/7] Installing ngrok"
    curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
        | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
    echo "deb https://ngrok-agent.s3.amazonaws.com buster main" \
        | sudo tee /etc/apt/sources.list.d/ngrok.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y ngrok
else
    echo "[3/7] ngrok is already installed"
fi

echo "[4/7] Checking ngrok authentication"
NGROK_CONFIG="${HOME}/.config/ngrok/ngrok.yml"
if [[ ! -f "$NGROK_CONFIG" ]] || ! grep -q 'authtoken:' "$NGROK_CONFIG"; then
    read -r -s -p "Paste the ngrok authtoken: " NGROK_AUTHTOKEN
    echo
    if [[ -z "$NGROK_AUTHTOKEN" ]]; then
        echo "ngrok authtoken cannot be empty." >&2
        exit 1
    fi
    ngrok config add-authtoken "$NGROK_AUTHTOKEN"
fi
chmod 700 "${HOME}/.config/ngrok"
chmod 600 "$NGROK_CONFIG"

echo "[5/7] Creating the Python environment"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
mkdir -p "$APP_DIR/data"
chmod 700 "$APP_DIR/data"
if [[ -f "$APP_DIR/data/pumpscanbot.sqlite3" ]]; then
    chmod 600 "$APP_DIR/data/pumpscanbot.sqlite3"
fi
chmod +x "$APP_DIR/deploy/oracle/run_ngrok.sh"

echo "[6/7] Installing systemd services"
escape_sed() {
    printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

SAFE_USER="$(escape_sed "$APP_USER")"
SAFE_DIR="$(escape_sed "$APP_DIR")"

sed -e "s|@@APP_USER@@|$SAFE_USER|g" -e "s|@@APP_DIR@@|$SAFE_DIR|g" \
    "$APP_DIR/deploy/oracle/pumpscanbot-ngrok.service.template" \
    | sudo tee /etc/systemd/system/pumpscanbot-ngrok.service >/dev/null

sed -e "s|@@APP_USER@@|$SAFE_USER|g" -e "s|@@APP_DIR@@|$SAFE_DIR|g" \
    "$APP_DIR/deploy/oracle/pumpscanbot.service.template" \
    | sudo tee /etc/systemd/system/pumpscanbot.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable pumpscanbot-ngrok.service pumpscanbot.service

echo "[7/7] Starting PumpScanBot"
sudo systemctl restart pumpscanbot-ngrok.service
sudo systemctl restart pumpscanbot.service

echo
echo "Installation complete. Check the bot with:"
echo "  sudo systemctl status pumpscanbot --no-pager"
echo "  sudo journalctl -u pumpscanbot -f"
