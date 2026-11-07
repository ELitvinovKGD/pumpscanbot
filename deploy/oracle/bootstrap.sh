#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "Run this script as the normal Ubuntu user, without sudo." >&2
    exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$APP_DIR/.env"
DATA_DIR="/var/lib/pumpscanbot"
DATABASE_PATH="$DATA_DIR/production.sqlite3"

echo "[1/3] Creating server data directories"
sudo install -d -m 700 -o "$(id -un)" -g "$(id -gn)" "$DATA_DIR"
sudo install -d -m 700 -o "$(id -un)" -g "$(id -gn)" "$DATA_DIR/backups"

echo "[2/3] Preparing the server .env"
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$APP_DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    if grep -qE '^DATABASE_PATH=' "$ENV_FILE"; then
        sed -i "s|^DATABASE_PATH=.*|DATABASE_PATH=$DATABASE_PATH|" "$ENV_FILE"
    else
        printf '\nDATABASE_PATH=%s\n' "$DATABASE_PATH" >> "$ENV_FILE"
    fi

    echo
    echo "Created $ENV_FILE."
    echo "Open it with: nano $ENV_FILE"
    echo "Fill the secrets, save the file, then run this bootstrap script again."
    exit 0
fi

chmod 600 "$ENV_FILE"

DATABASE_VALUE="$(grep -E '^DATABASE_PATH=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
if [[ "$DATABASE_VALUE" != "$DATABASE_PATH" ]]; then
    echo "DATABASE_PATH currently points to: ${DATABASE_VALUE:-<empty>}" >&2
    echo "Expected server path: $DATABASE_PATH" >&2
    echo "If an old database already exists, follow the migration section in SERVER_RUNBOOK.md." >&2
    exit 1
fi

echo "[3/3] Installing and starting server services"
exec "$APP_DIR/deploy/oracle/install.sh"
