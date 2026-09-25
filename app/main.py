from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import Settings
from app.db import Database
from app.helius import HeliusClient
from app.telegram_bot import router
from app.usage_monitor import run_usage_monitor
from app.web import create_app


async def main() -> None:
    """Стартуем Telegram polling и FastAPI webhook endpoint в одном процессе."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = Settings.load()
    settings.validate_for_startup()

    db = Database(settings.database_path)
    helius = HeliusClient(settings, db)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    # Aiogram умеет прокидывать эти объекты в handlers по имени аргумента.
    dispatcher["db"] = db
    dispatcher["settings"] = settings
    dispatcher["helius"] = helius

    app = create_app(bot=bot, db=db, settings=settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    )

    logging.info("Starting PumpScanBot on http://%s:%s", settings.host, settings.port)

    async def sync_changed_public_url() -> None:
        """Даем HTTP-серверу подняться и обновляем Helius только при новом URL."""
        launcher_detected_change = os.getenv("PUMPSCAN_PUBLIC_URL_CHANGED") == "1"
        last_synced_url = db.get_setting(HeliusClient.LAST_SYNCED_URL_KEY)

        # Миграция для уже настроенного бота: до появления автоматического
        # запуска успешный URL не сохранялся в SQLite. Принимаем текущий URL
        # без лишнего платного PUT. После этого подтверждение записывается
        # только функцией sync_webhook после успешного ответа Helius.
        if not launcher_detected_change and not last_synced_url and helius.configured_for_webhooks():
            db.set_setting(HeliusClient.LAST_SYNCED_URL_KEY, helius.webhook_url)
            logging.info("Startup Helius check: existing URL remembered; sync skipped")
            return

        await asyncio.sleep(1)
        addresses = [wallet.address for wallet in db.list_wallets(enabled_only=True)]
        try:
            result = await helius.sync_webhook_if_url_changed(addresses)
            logging.info("Startup Helius check: %s", result)
        except Exception:
            logging.exception("Startup Helius sync failed")

    try:
        await asyncio.gather(
            dispatcher.start_polling(bot),
            server.serve(),
            run_usage_monitor(bot, db, settings, helius),
            sync_changed_public_url(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
