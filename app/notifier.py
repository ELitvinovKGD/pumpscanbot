from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.db import Wallet, short_address
from app.detector import ActivityEvent, BuyEvent, SellEvent
from app.market_data import DexScreenerClient, TokenQuote, USDC_MINT, USDT_MINT


ACTIVITY_NAMES = {
    "SWAP": "Обмен токенов",
    "TRANSFER": "Перевод",
    "SOL_TRANSFER": "Перевод SOL",
    "TOKEN_TRANSFER": "Перевод токена",
    "STAKE_SOL": "Стейкинг SOL",
    "UNSTAKE_SOL": "Вывод SOL из стейкинга",
}
MARKET_DATA = DexScreenerClient()
# Время в уведомлениях показываем по Тбилиси, а не по часовому поясу сервера
# (на Oracle VM это UTC).
DISPLAY_TZ = timezone(timedelta(hours=4))

logger = logging.getLogger(__name__)


def owner_chat_id(wallet: Wallet) -> int:
    """В личке с ботом chat_id совпадает с user_id, поэтому есть надёжный fallback."""
    return wallet.owner_chat_id or wallet.owner_id


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs: object) -> bool:
    """Отправка с логированием ошибок и одной повторной попыткой при flood-limit."""
    kwargs.setdefault("disable_web_page_preview", True)
    for attempt in range(2):
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            return True
        except TelegramRetryAfter as exc:
            if attempt == 0:
                await asyncio.sleep(min(exc.retry_after, 30))
                continue
            logger.warning("Telegram flood limit for chat %s, message dropped", chat_id)
        except TelegramForbiddenError:
            logger.info("Chat %s blocked the bot, message skipped", chat_id)
        except Exception:
            logger.exception("Failed to send Telegram message to chat %s", chat_id)
        return False
    return False


def _format_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def format_buy_event(event: BuyEvent) -> str:
    """Готовим компактное Telegram-сообщение без лишнего шума."""
    tx_url = f"https://solscan.io/tx/{event.signature}"
    mint_url = f"https://pump.fun/coin/{event.token_mint}"
    when = ""
    if event.timestamp:
        when = f"\nВремя: <code>{_format_time(event.timestamp)}</code>"

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


async def notify_buy_event(bot: Bot, event: BuyEvent) -> bool:
    """Уведомление получает только владелец кошелька."""
    return await safe_send(bot, owner_chat_id(event.wallet), format_buy_event(event))


def format_sell_event(event: SellEvent) -> str:
    """Сообщение о продаже: коротко, чтобы его можно было быстро читать с телефона."""
    tx_url = f"https://solscan.io/tx/{event.signature}"
    mint_url = f"https://pump.fun/coin/{event.token_mint}"
    when = ""
    if event.timestamp:
        when = f"\nВремя: <code>{_format_time(event.timestamp)}</code>"

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


async def notify_sell_event(bot: Bot, event: SellEvent) -> bool:
    return await safe_send(bot, owner_chat_id(event.wallet), format_sell_event(event))


def format_activity_event(event: ActivityEvent, quotes: dict[str, TokenQuote] | None = None) -> str:
    """Форматируем любую активность кошелька одним итоговым сообщением."""
    quotes = quotes or {}
    tx_url = f"https://solscan.io/tx/{event.signature}"
    title = ACTIVITY_NAMES.get(event.event_type.upper(), "Действие кошелька")
    when = ""
    if event.timestamp:
        when = f"\nВремя: <code>{_format_time(event.timestamp)}</code>"

    sent = [(mint, abs(amount)) for mint, amount in event.token_changes if amount < 0]
    received = [(mint, amount) for mint, amount in event.token_changes if amount > 0]
    stable_value = max(
        (abs(amount) for mint, amount in event.token_changes if mint in {USDC_MINT, USDT_MINT}),
        default=None,
    )

    changes: list[str] = []
    for label, items in (("Отдал", sent), ("Получил", received)):
        for mint, amount in items[:3]:
            changes.append(f"{label}: {_format_token_amount(mint, amount, quotes, stable_value)}")

    # В swap не показываем крошечный минус SOL: это network fee, а не часть обмена.
    show_sol = abs(event.sol_change) >= 0.0001 or not event.token_changes
    if show_sol and abs(event.sol_change) > 1e-12:
        direction = "Получил" if event.sol_change > 0 else "Отдал"
        changes.append(f"{direction}: <b>{_format_number(abs(event.sol_change))} SOL</b>")

    if not changes and event.description:
        changes.append(html.escape(event.description))
    changes_text = "\n".join(changes) if changes else "Изменения баланса не распознаны"

    return (
        f"🔔 <b>{html.escape(title)}</b>\n\n"
        f"Кошелек: <b>{html.escape(event.wallet.effective_label)}</b> "
        f"<code>{short_address(event.wallet.address)}</code>\n"
        f"Площадка: <b>{html.escape(event.source)}</b>\n\n"
        f"{changes_text}"
        f"{when}\n\n"
        f"<a href=\"{tx_url}\">Транзакция</a>"
    )


async def notify_activity_event(bot: Bot, event: ActivityEvent) -> bool:
    quotes = await MARKET_DATA.get_quotes([mint for mint, _ in event.token_changes])
    return await safe_send(bot, owner_chat_id(event.wallet), format_activity_event(event, quotes))


def _format_token_amount(
    mint: str,
    amount: float,
    quotes: dict[str, TokenQuote],
    stable_value: float | None,
) -> str:
    quote = quotes.get(mint)
    symbol = quote.symbol if quote and quote.symbol else short_address(mint)
    token_url = f"https://solscan.io/token/{mint}"
    usd_value = amount * quote.price_usd if quote and quote.price_usd is not None else stable_value
    usd_text = f" <code>≈ ${_format_number(usd_value, 2)}</code>" if usd_value is not None else ""
    return (
        f"<b>{_format_number(amount)} "
        f"<a href=\"{token_url}\">{html.escape(symbol)}</a></b>{usd_text}"
    )


def _format_number(value: float, decimals: int = 6) -> str:
    text = f"{value:,.{decimals}f}"
    return text.rstrip("0").rstrip(".")


def format_wallet_details(wallet: Wallet, global_min_sol: float) -> str:
    specific_min = wallet.min_sol
    if specific_min is not None:
        min_text = f"{specific_min} SOL"
    else:
        min_text = f"общий ({wallet.effective_min_sol(global_min_sol)} SOL)"
    status = "включен" if wallet.enabled else "выключен"
    return (
        f"<b>{html.escape(wallet.effective_label)}</b>\n\n"
        f"Адрес: <code>{wallet.address}</code>\n"
        f"Статус: <b>{status}</b>\n"
        f"Мин. сумма: <b>{min_text}</b>\n"
        f"Коротко: <code>{short_address(wallet.address)}</code>"
    )
