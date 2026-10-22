#!/usr/bin/env bash
set -Eeuo pipefail

echo "=== PumpScanBot ==="
sudo systemctl status pumpscanbot --no-pager --lines=8 || true
echo
echo "=== ngrok ==="
sudo systemctl status pumpscanbot-ngrok --no-pager --lines=8 || true
echo
echo "=== Public tunnel ==="
curl -fsS http://127.0.0.1:4040/api/tunnels \
    | python3 -c 'import json,sys; data=json.load(sys.stdin); print(*[item["public_url"] for item in data.get("tunnels", []) if item.get("proto") == "https"], sep="\n")'
