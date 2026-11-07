#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "Run this script as the normal Ubuntu user, without sudo." >&2
    exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$APP_DIR/.env"
BRANCH="${1:-main}"
SERVICE="pumpscanbot.service"
BOT_WAS_ACTIVE=0

cd "$APP_DIR"

if [[ ! -d .git ]]; then
    echo "$APP_DIR is not a Git checkout." >&2
    exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "$ENV_FILE does not exist." >&2
    exit 1
fi

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    echo "Python environment is missing. Run ./deploy/oracle/install.sh first." >&2
    exit 1
fi

CURRENT_BRANCH="$(git branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
    echo "Current branch is '$CURRENT_BRANCH', expected '$BRANCH'." >&2
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Tracked files contain local changes. Commit, discard, or move them before updating." >&2
    git status --short
    exit 1
fi

DATABASE_VALUE="$($APP_DIR/.venv/bin/python - "$ENV_FILE" <<'PY'
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "DATABASE_PATH":
        print(value.strip())
        break
else:
    print("data/pumpscanbot.sqlite3")
PY
)"

if [[ "$DATABASE_VALUE" = /* ]]; then
    DATABASE_PATH="$DATABASE_VALUE"
else
    DATABASE_PATH="$APP_DIR/$DATABASE_VALUE"
fi

BACKUP_DIR="$(dirname "$DATABASE_PATH")/backups"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if sudo systemctl is-active --quiet "$SERVICE"; then
    BOT_WAS_ACTIVE=1
fi

restore_service_on_error() {
    status=$?
    trap - EXIT
    if [[ "$status" -ne 0 && "$BOT_WAS_ACTIVE" -eq 1 ]]; then
        echo "Update failed. Starting the previous service state again." >&2
        sudo systemctl start "$SERVICE" || true
    fi
    exit "$status"
}
trap restore_service_on_error EXIT

echo "[1/6] Fetching origin/$BRANCH"
git fetch origin "$BRANCH"

echo "[2/6] Stopping PumpScanBot"
sudo systemctl stop "$SERVICE"

echo "[3/6] Backing up data"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

if [[ -f "$DATABASE_PATH" ]]; then
    cp -p "$DATABASE_PATH" "$BACKUP_DIR/$(basename "$DATABASE_PATH").$TIMESTAMP"
    chmod 600 "$BACKUP_DIR/$(basename "$DATABASE_PATH").$TIMESTAMP"
    echo "Database backup: $BACKUP_DIR/$(basename "$DATABASE_PATH").$TIMESTAMP"
else
    echo "Database does not exist yet: $DATABASE_PATH"
fi

cp -p "$ENV_FILE" "$BACKUP_DIR/env.$TIMESTAMP"
chmod 600 "$BACKUP_DIR/env.$TIMESTAMP"

echo "[4/6] Updating code"
git pull --ff-only origin "$BRANCH"

echo "[5/6] Updating Python dependencies"
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

echo "[6/6] Starting PumpScanBot"
sudo systemctl start "$SERVICE"
sleep 2
sudo systemctl status "$SERVICE" --no-pager --lines=12

trap - EXIT
echo
echo "Update complete: $(git log -1 --oneline)"
