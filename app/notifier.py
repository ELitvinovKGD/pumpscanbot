from __future__ import annotations

import html
from datetime import datetime

from aiogram import Bot

from app.db import Database, Wallet, short_address
from app.detector import ActivityEvent, BuyEvent, SellEvent


ACTIVITY_NAMES = {
    "SWAP": "Обмен токенов",
    "TRANSFER": "Перевод",
    "SOL_TRANSFER": "Перевод SOL",
    "TOKEN_TRANSFER": "Перевод токена",
    "STAKE_SOL": "Стейкинг SOL",
    "UNSTAKE_SOL": "Вывод SOL из стейкинга",
}


def format_buy_event(event: BuyEvent) -> str:
    """Готовим компактное Telegram-сообщение без лишнего шума."""
    tx_url = f"https://solscan.io/tx/{event.signature}"
    mint_url = f"https://pump.fun/coin/{event.token_mint}"
    when = ""
    if event.timestamp:
        when_dt = datetime.fromtimestamp(event.timestamp)
        when = f"\nВремя: <code>{when_dt:%Y-%m-%d %H:%M:%S}</code>"

    return (
        "🟢 <b>Покупка pump-токена</b>\n\n"
        f"Кошелек: <b>{html.escape(event.wallet.effective_label)}</b>\n"
        f"Адрес: <code>{event.wallet.address}</code>\n"
        f"Потратил: <b>{event.sol_spent:.4f} SOL</b>\n"
        f"Получил: <b>{event.token_amount:g}</b>\n"
        f"Mint: <code>{event.token_mint}</code>\n"
        f"Источник: <code>{html.escape(event.source)}</code>"
        f"{when}\n\n"
        f"<a href=\"{tx_url}\">Транзакция</a> · <a href=\"{mint_url}\">Pump</a>"
    )


async def notify_buy_event(bot: Bot, db: Database, event: BuyEvent) -> None:
    chats = db.list_notification_chats()
    if not chats:
        return

    text = format_buy_event(event)
    for chat_id in chats:
        # Если пользователь заблокировал бота, просто пропускаем ошибку: один
        # мертвый чат не должен останавливать уведомления всем остальным.
        try:
            await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        except Exception:
            continue


def format_sell_event(event: SellEvent) -> str:
    """Сообщение о продаже: коротко, чтобы его можно было быстро читать с телефона."""
    tx_url = f"https://solscan.io/tx/{event.signature}"
    mint_url = f"https://pump.fun/coin/{event.token_mint}"
    when = ""
    if event.timestamp:
        when_dt = datetime.fromtimestamp(event.timestamp)
        when = f"\nВремя: <code>{when_dt:%Y-%m-%d %H:%M:%S}</code>"

    return (
        "🔴 <b>Продажа pump-токена</b>\n\n"
        f"Кошелек: <b>{html.escape(event.wallet.effective_label)}</b>\n"
        f"Адрес: <code>{event.wallet.address}</code>\n"
        f"Продал: <b>{event.token_amount:g}</b>\n"
        f"Получил: <b>{event.sol_received:.4f} SOL</b>\n"
        f"Mint: <code>{event.token_mint}</code>\n"
        f"Источник: <code>{html.escape(event.source)}</code>"
        f"{when}\n\n"
        f"<a href=\"{tx_url}\">Транзакция</a> · <a href=\"{mint_url}\">Pump</a>"
    )


async def notify_sell_event(bot: Bot, db: Database, event: SellEvent) -> None:
    chats = db.list_notification_chats()
    if not chats:
        return

    text = format_sell_event(event)
    for chat_id in chats:
        try:
            await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        except Exception:
            continue


def format_activity_event(event: ActivityEvent) -> str:
    """Форматируем любую активность кошелька одним итоговым сообщением."""
    tx_url = f"https://solscan.io/tx/{event.signature}"
    title = ACTIVITY_NAMES.get(event.event_type.upper(), "Действие кошелька")
    when = ""
    if event.timestamp:
        when_dt = datetime.fromtimestamp(event.timestamp)
        when = f"\nВремя: <code>{when_dt:%Y-%m-%d %H:%M:%S}</code>"

    changes: list[str] = []
    if abs(event.sol_change) > 1e-12:
        changes.append(f"SOL: <b>{event.sol_change:+.9f}</b>")
    for mint, amount in event.token_changes[:6]:
        short_mint = short_address(mint)
        changes.append(f"<code>{short_mint}</code>: <b>{amount:+g}</b>")
    changes_text = "\n".join(changes) if changes else "Изменения баланса не распознаны"

    description = ""
    if event.description:
        description = f"\nОписание: {html.escape(event.description)}"

    return (
        f"🔔 <b>{html.escape(title)}</b>\n\n"
        f"Кошелек: <b>{html.escape(event.wallet.effective_label)}</b>\n"
        f"Адрес: <code>{event.wallet.address}</code>\n"
        f"Источник: <code>{html.escape(event.source)}</code>\n"
        f"Тип: <code>{html.escape(event.event_type)}</code>\n\n"
        f"{changes_text}"
        f"{description}"
        f"{when}\n\n"
        f"<a href=\"{tx_url}\">Транзакция</a>"
    )


async def notify_activity_event(bot: Bot, db: Database, event: ActivityEvent) -> None:
    text = format_activity_event(event)
    for chat_id in db.list_notification_chats():
        try:
            await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        except Exception:
            continue


def format_wallet_details(wallet: Wallet, global_min_sol: float) -> str:
    specific_min = wallet.min_sol
    min_text = f"{specific_min} SOL" if specific_min is not None else f"глобальный ({global_min_sol} SOL)"
    status = "включен" if wallet.enabled else "выключен"
    return (
        f"<b>{html.escape(wallet.effective_label)}</b>\n\n"
        f"Адрес: <code>{wallet.address}</code>\n"
        f"Статус: <b>{status}</b>\n"
        f"Мин. сумма: <b>{min_text}</b>\n"
        f"Коротко: <code>{short_address(wallet.address)}</code>"
    )
