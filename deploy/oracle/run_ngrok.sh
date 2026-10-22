#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$APP_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE" >&2
    exit 1
fi

PORT="$(sed -n 's/^PORT=//p' "$ENV_FILE" | tail -n 1 | tr -d '\r')"
PORT="${PORT:-8080}"

exec ngrok http "$PORT"
