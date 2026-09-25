from __future__ import annotations

import asyncio

from app.config import Settings
from app.db import Database
from app.helius import HeliusClient


async def main() -> None:
    """Ручная синхронизация Helius webhook.

    Полезно, если ты поменял PUBLIC_WEBHOOK_URL в .env после перезапуска ngrok
    и хочешь обновить Helius без кликов в Telegram.
    """
    settings = Settings.load()
    db = Database(settings.database_path)
    helius = HeliusClient(settings, db)
    addresses = [wallet.address for wallet in db.list_wallets(enabled_only=True)]
    print(await helius.sync_webhook(addresses))


if __name__ == "__main__":
    asyncio.run(main())
