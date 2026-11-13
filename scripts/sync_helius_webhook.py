from __future__ import annotations

import asyncio

from app.config import Settings
from app.db import Database
from app.helius import HeliusClient
from app.main import _migrate_single_user_data


async def main() -> None:
    """Ручная синхронизация Helius webhook.

    Полезно, если ты поменял PUBLIC_WEBHOOK_URL в .env после перезапуска ngrok
    и хочешь обновить Helius без кликов в Telegram.
    """
    settings = Settings.load()
    db = Database(settings.database_path)
    _migrate_single_user_data(db, settings)
    helius = HeliusClient(settings, db)
    addresses = db.tracked_addresses()
    print(await helius.sync_webhook(addresses))


if __name__ == "__main__":
    asyncio.run(main())
