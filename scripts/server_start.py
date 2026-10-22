from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
NGROK_API_URL = "http://127.0.0.1:4040/api/tunnels"


def read_env_value(name: str, default: str = "") -> str:
    """Read one value without loading secrets into the shell environment."""
    if not ENV_PATH.exists():
        return default

    for raw_line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return default


def write_env_value(name: str, value: str) -> None:
    """Update one .env field while preserving comments and all other values."""
    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
    replacement = f"{name}={value}"
    updated = False

    for index, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == name and "=" in line:
            lines[index] = replacement
            updated = True
            break

    if not updated:
        lines.append(replacement)

    temporary_path = ENV_PATH.with_suffix(".env.tmp")
    temporary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary_path.replace(ENV_PATH)


def wait_for_ngrok_url(port: int, timeout_seconds: int = 60) -> str:
    """Wait for ngrok and return the HTTPS tunnel connected to our port."""
    deadline = time.monotonic() + timeout_seconds
    last_error = "ngrok API is not ready"

    while time.monotonic() < deadline:
        try:
            with urlopen(NGROK_API_URL, timeout=3) as response:
                payload = json.load(response)

            for tunnel in payload.get("tunnels", []):
                public_url = str(tunnel.get("public_url", ""))
                address = str(tunnel.get("config", {}).get("addr", ""))
                if public_url.startswith("https://") and address.rstrip("/").endswith(f":{port}"):
                    return public_url.rstrip("/")
            last_error = f"no HTTPS tunnel for port {port}"
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)

        time.sleep(1)

    raise RuntimeError(f"ngrok did not become ready in {timeout_seconds}s: {last_error}")


def main() -> None:
    """Refresh the public URL and replace this launcher with the bot process."""
    if not ENV_PATH.exists():
        raise RuntimeError(f"{ENV_PATH} does not exist")

    port = int(read_env_value("PORT", "8080"))
    old_url = read_env_value("PUBLIC_WEBHOOK_URL").rstrip("/")
    public_url = wait_for_ngrok_url(port)
    url_changed = old_url != public_url

    if url_changed:
        write_env_value("PUBLIC_WEBHOOK_URL", public_url)
        print(f"[PumpScanBot] Public URL updated: {public_url}", flush=True)
    else:
        print(f"[PumpScanBot] Public URL unchanged: {public_url}", flush=True)

    environment = os.environ.copy()
    environment["PUMPSCAN_PUBLIC_URL_CHANGED"] = "1" if url_changed else "0"
    environment["PYTHONUNBUFFERED"] = "1"

    python = PROJECT_DIR / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError(f"Python environment not found: {python}")

    os.chdir(PROJECT_DIR)
    os.execve(str(python), [str(python), "-m", "app.main"], environment)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[PumpScanBot] Server startup failed: {exc}", file=sys.stderr, flush=True)
        raise
