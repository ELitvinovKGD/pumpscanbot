from __future__ import annotations

import hmac
import logging
from time import time
from typing import Any

from aiogram import Bot
from fastapi import FastAPI, Header, HTTPException, Request

from app.config import Settings
from app.db import Database
from app.detector import (
    detect_activity_events,
    detect_buy_events,
    detect_sell_events,
    summarize_for_debug,
)
from app.notifier import notify_activity_event, notify_buy_event, notify_sell_event, safe_send
from app.telegram_bot import global_min_sol


logger = logging.getLogger(__name__)


def create_app(bot: Bot, db: Database, settings: Settings) -> FastAPI:
    app = FastAPI(title="PumpScanBot")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/helius/webhook")
    async def helius_webhook(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Принимаем Helius enhanced webhook.

        Helius присылает список транзакций. Мы разбираем каждую, ищем покупки
        и продажи отслеживаемыми кошельками, затем отправляем уведомления.
        """
        expected_auth = settings.helius_auth_header
        if expected_auth and "PASTE_" not in expected_auth and not hmac.compare_digest(
            (authorization or "").encode(), expected_auth.encode()
        ):
            raise HTTPException(status_code=401, detail="Invalid authorization header")

        payload = await request.json()
        transactions = payload if isinstance(payload, list) else [payload]
        # Только включённые кошельки одобренных пользователей. Если один адрес
        # отслеживают двое, это две записи — и каждый получит своё уведомление.
        wallets = db.list_tracked_wallets()
        fallback_min_sol = global_min_sol(db, settings)

        detected = 0
        notified = 0
        for tx in transactions:
            if not isinstance(tx, dict):
                continue
            tx_detected = 0
            specialized_wallets: set[int] = set()
            for event in detect_buy_events(tx, wallets, fallback_min_sol):
                detected += 1
                tx_detected += 1
                specialized_wallets.add(event.wallet.id)
                if not db.mark_processed_once(f"buy:{event.signature}", f"w{event.wallet.id}", event.token_mint):
                    continue
                if await notify_buy_event(bot, event):
                    notified += 1
            for event in detect_sell_events(tx, wallets, fallback_min_sol):
                detected += 1
                tx_detected += 1
                specialized_wallets.add(event.wallet.id)
                if not db.mark_processed_once(f"sell:{event.signature}", f"w{event.wallet.id}", event.token_mint):
                    continue
                if await notify_sell_event(bot, event):
                    notified += 1

            for event in detect_activity_events(tx, wallets):
                if event.wallet.id in specialized_wallets:
                    continue
                detected += 1
                tx_detected += 1
                if not db.mark_processed_once(f"activity:{event.signature}", f"w{event.wallet.id}", "activity"):
                    continue
                if await notify_activity_event(bot, event):
                    notified += 1

            if tx_detected == 0 and db.get_setting("debug_webhook_notifications") == "1":
                await _send_debug_message(bot, settings, summarize_for_debug(tx, wallets))

        db.set_setting("last_webhook_at", str(int(time())))
        db.set_setting("last_webhook_transactions", str(len(transactions)))
        db.set_setting("last_webhook_detected", str(detected))
        db.set_setting("last_webhook_notified", str(notified))
        db.set_setting("total_webhook_calls", str(int(db.get_setting("total_webhook_calls", "0")) + 1))

        logger.info(
            "Helius webhook processed: transactions=%s wallets=%s detected=%s notified=%s debug=%s",
            len(transactions),
            len(wallets),
            detected,
            notified,
            db.get_setting("debug_webhook_notifications") == "1",
        )

        return {"ok": True, "transactions": len(transactions), "detected": detected, "notified": notified}

    return app


async def _send_debug_message(bot: Bot, settings: Settings, text: str) -> None:
    """Debug содержит адреса и подписи кошельков всех пользователей — только админам."""
    for admin_id in settings.telegram_admin_user_ids:
        await safe_send(bot, admin_id, text)
