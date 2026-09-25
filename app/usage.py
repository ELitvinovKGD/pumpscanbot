from __future__ import annotations

from datetime import datetime
from typing import Any

from app.helius import HeliusClient


async def format_helius_usage(helius: HeliusClient) -> str:
    """Запрашиваем Helius usage и превращаем его в понятный Telegram-текст."""
    try:
        usage = await helius.get_project_usage()
    except Exception as exc:
        return f"Не смог получить лимит Helius: {exc}"
    return format_helius_usage_payload(usage)


def format_helius_usage_payload(usage: dict[str, Any]) -> str:
    details = usage.get("subscriptionDetails") or {}
    breakdown = usage.get("usage") or {}
    billing_cycle = details.get("billingCycle") or {}

    plan = details.get("plan", "unknown")
    credits_remaining = _as_number(usage.get("creditsRemaining"))
    credits_used = _as_number(usage.get("creditsUsed"))
    credits_limit = _as_number(details.get("creditsLimit"))
    webhook_used = breakdown.get("webhook", "?")
    rpc_used = breakdown.get("rpc", "?")

    percent_left = _percent(credits_remaining, credits_limit)
    reset_text = _format_reset_date(billing_cycle.get("end"))

    return (
        "📊 <b>Helius credits</b>\n\n"
        f"Plan: <code>{plan}</code>\n"
        f"Осталось: <b>{_fmt_num(credits_remaining)}</b> из <b>{_fmt_num(credits_limit)}</b>\n"
        f"Использовано: <b>{_fmt_num(credits_used)}</b>\n"
        f"Остаток: <b>{percent_left}</b>\n"
        f"Сброс: <b>{reset_text}</b>\n\n"
        f"Webhook events: <b>{webhook_used}</b>\n"
        f"RPC usage: <b>{rpc_used}</b>"
    )


def usage_warning_key(usage: dict[str, Any], threshold_percent: int) -> str:
    """Ключ для защиты от повторных предупреждений в одном billing cycle."""
    details = usage.get("subscriptionDetails") or {}
    billing_cycle = details.get("billingCycle") or {}
    cycle_end = str(billing_cycle.get("end") or "unknown-cycle")
    return f"helius_usage_warning:{cycle_end}:{threshold_percent}"


def is_usage_below_threshold(usage: dict[str, Any], threshold_percent: int) -> bool:
    details = usage.get("subscriptionDetails") or {}
    credits_remaining = _as_number(usage.get("creditsRemaining"))
    credits_limit = _as_number(details.get("creditsLimit"))
    if credits_remaining is None or credits_limit in (None, 0):
        return False
    return (credits_remaining / credits_limit) * 100 <= threshold_percent


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent(value: float | None, total: float | None) -> str:
    if value is None or total in (None, 0):
        return "?"
    return f"{(value / total) * 100:.1f}%"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "?"
    if value.is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")


def _format_reset_date(raw_value: Any) -> str:
    if not raw_value:
        return "?"
    raw_text = str(raw_value)
    try:
        # Helius обычно отдает дату вида YYYY-MM-DD. Показываем как дату, без
        # выдуманного времени, потому что точный час сброса API может не вернуть.
        return datetime.fromisoformat(raw_text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return raw_text
