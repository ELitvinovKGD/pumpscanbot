from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app import access, admin, telegram_bot
from app.config import Settings
from app.db import Database
from app.helius import HeliusClient
from app.usage_monitor import run_usage_monitor
from app.web import create_app


async def main() -> None:
    """Стартуем Telegram polling и FastAPI webhook endpoint в одном процессе."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # httpx на уровне INFO пишет полный URL запроса, а Helius принимает
    # api-key в query string. Без этой строки ключ попадает в журнал сервера.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings.load()
    settings.validate_for_startup()

    db = Database(settings.database_path)
    _migrate_single_user_data(db, settings)
    helius = HeliusClient(settings, db)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher(storage=MemoryStorage())
    access_middleware = access.AccessMiddleware(db, settings)
    dispatcher.message.outer_middleware(access_middleware)
    dispatcher.callback_query.outer_middleware(access_middleware)
    # Порядок важен: сначала /start и заявки, потом админка, потом обычные действия.
    dispatcher.include_router(access.router)
    dispatcher.include_router(admin.router)
    dispatcher.include_router(telegram_bot.router)

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
        addresses = db.tracked_addresses()
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
            _prune_processed_forever(db),
        )
    finally:
        await bot.session.close()


def _migrate_single_user_data(db: Database, settings: Settings) -> None:
    """Однократно переносим кошельки и минимум старой версии на главного админа."""
    admin_id = settings.primary_admin_id
    if admin_id is None or db.get_setting("multiuser_migrated") == "1":
        return
    legacy_min = None
    if db.has_setting(telegram_bot.GLOBAL_MIN_KEY):
        try:
            legacy_min = float(db.get_setting(telegram_bot.GLOBAL_MIN_KEY))
        except ValueError:
            legacy_min = None
    moved = db.claim_legacy_data(admin_id, legacy_min)
    db.set_setting("multiuser_migrated", "1")
    logging.info("Multi-user migration: %s wallet(s) assigned to admin %s", moved, admin_id)


async def _prune_processed_forever(db: Database) -> None:
    while True:
        try:
            removed = db.prune_processed()
            if removed:
                logging.info("Pruned %s old processed transaction marks", removed)
        except Exception:
            logging.exception("Prune of processed transactions failed")
        await asyncio.sleep(24 * 3600)


if __name__ == "__main__":
    asyncio.run(main())
