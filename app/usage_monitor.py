from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.config import Settings
from app.db import Database
from app.helius import HeliusClient
from app.usage import format_helius_usage_payload, is_usage_below_threshold, usage_warning_key


logger = logging.getLogger(__name__)


async def run_usage_monitor(bot: Bot, db: Database, settings: Settings, helius: HeliusClient) -> None:
    """Периодически проверяем Helius credits и предупреждаем в Telegram.

    Проверка идет редко, по умолчанию раз в 6 часов. Это почти не расходует
    лимиты и при этом дает ранний сигнал, если credits подходят к концу.
    """
    if settings.helius_usage_check_interval_minutes <= 0:
        logger.info("Helius usage monitor disabled.")
        return

    await asyncio.sleep(30)
    while True:
        try:
            await _check_once(bot, db, settings, helius)
        except Exception:
            logger.exception("Helius usage monitor check failed")

        await asyncio.sleep(settings.helius_usage_check_interval_minutes * 60)


async def _check_once(bot: Bot, db: Database, settings: Settings, helius: HeliusClient) -> None:
    chats = db.list_notification_chats()
    if not chats:
        return

    usage = await helius.get_project_usage()
    if not is_usage_below_threshold(usage, settings.helius_usage_warn_percent):
        return

    warning_key = usage_warning_key(usage, settings.helius_usage_warn_percent)
    if db.get_setting(warning_key):
        return

    text = (
        f"⚠️ <b>Helius credits ниже {settings.helius_usage_warn_percent}%</b>\n\n"
        f"{format_helius_usage_payload(usage)}"
    )
    sent = False
    for chat_id in chats:
        try:
            await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
            sent = True
        except Exception:
            continue

    # Помечаем предупреждение только если хотя бы одно сообщение реально ушло.
    if sent:
        db.set_setting(warning_key, "1")
