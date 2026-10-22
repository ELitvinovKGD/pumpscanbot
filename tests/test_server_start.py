from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import server_start


class ServerStartTest(unittest.TestCase):
    def test_updates_public_url_without_touching_other_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                "# Keys\n"
                "TELEGRAM_BOT_TOKEN=secret-token\n"
                "PUBLIC_WEBHOOK_URL=https://old.example\n"
                "HELIUS_API_KEY=secret-helius\n",
                encoding="utf-8",
            )

            with patch.object(server_start, "ENV_PATH", env_path):
                server_start.write_env_value(
                    "PUBLIC_WEBHOOK_URL", "https://new.ngrok-free.app"
                )
                self.assertEqual(
                    server_start.read_env_value("PUBLIC_WEBHOOK_URL"),
                    "https://new.ngrok-free.app",
                )

            content = env_path.read_text(encoding="utf-8")
            self.assertIn("TELEGRAM_BOT_TOKEN=secret-token", content)
            self.assertIn("HELIUS_API_KEY=secret-helius", content)
            self.assertNotIn("https://old.example", content)


if __name__ == "__main__":
    unittest.main()
