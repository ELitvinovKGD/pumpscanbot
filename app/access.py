"""Доступ к боту: заявки, проверка статуса, /start.

Middleware стоит перед всеми обработчиками. Он:
1. работает только в личных чатах (в группе чужие кошельки стали бы видны всем);
2. создаёт/обновляет запись пользователя;
3. админам из .env всегда выдаёт доступ;
4. неодобренным пропускает только /start и кнопку «Запросить доступ».
"""
from __future__ import annotations

import html
import logging
from time import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import Settings
from app.db import (
    REAPPLY_COOLDOWN_SECONDS,
    STATUS_APPROVED,
    STATUS_BLOCKED,
    STATUS_NEW,
    STATUS_PENDING,
    STATUS_REJECTED,
    Database,
    User,
)
from app.keyboards import application_menu, main_menu, persistent_menu, request_access_menu
from app.notifier import safe_send


logger = logging.getLogger(__name__)

ACCESS_REQUEST_CALLBACK = "access:request"

router = Router(name="access")


class AccessMiddleware(BaseMiddleware):
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat = event.message.chat
        else:
            return None

        if chat.type != "private" or event.from_user is None:
            return None

        tg_user = event.from_user
        user = self.db.touch_user(tg_user.id, chat.id, tg_user.username or "", tg_user.full_name or "")
        is_admin = self.settings.is_admin(tg_user.id)
        if is_admin and user.status != STATUS_APPROVED:
            self.db.set_user_status(tg_user.id, STATUS_APPROVED)
            user = self.db.get_user(tg_user.id)  # type: ignore[assignment]

        data["user"] = user
        data["is_admin"] = is_admin

        if user.is_approved or _is_access_flow(event):
            return await handler(event, data)

        await _deny(event, user)
        return None


def _is_access_flow(event: TelegramObject) -> bool:
    if isinstance(event, CallbackQuery):
        return event.data == ACCESS_REQUEST_CALLBACK
    if isinstance(event, Message):
        parts = (event.text or "").split(maxsplit=1)
        return bool(parts) and parts[0].split("@", 1)[0] == "/start"
    return False


async def _deny(event: TelegramObject, user: User) -> None:
    text, markup = access_screen(user)
    if isinstance(event, CallbackQuery):
        await event.answer("Нет доступа", show_alert=False)
        if event.message is not None:
            await event.message.answer(text, reply_markup=markup)
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=markup)


def can_reapply(user: User, now: int | None = None) -> bool:
    if user.status == STATUS_NEW:
        return True
    if user.status == STATUS_REJECTED:
        now = now or int(time())
        return (user.decided_at or 0) + REAPPLY_COOLDOWN_SECONDS <= now
    return False


def access_screen(user: User) -> tuple[str, Any]:
    """Текст и кнопка для пользователя без доступа."""
    if user.status == STATUS_PENDING:
        return "🕓 Заявка на доступ отправлена и ждёт решения администратора. Я напишу, когда её рассмотрят.", None
    if user.status == STATUS_BLOCKED:
        return "⛔ Доступ к боту закрыт администратором.", None
    if user.status == STATUS_REJECTED and not can_reapply(user):
        hours_left = max(1, ((user.decided_at or 0) + REAPPLY_COOLDOWN_SECONDS - int(time())) // 3600)
        return f"Заявка отклонена. Повторно подать её можно примерно через {hours_left} ч.", None
    return (
        "👋 Это закрытый бот для отслеживания активности Solana-кошельков.\n\n"
        "Чтобы пользоваться им, отправь заявку — администратор её рассмотрит.",
        request_access_menu(),
    )


@router.message(CommandStart())
async def start(message: Message, user: User, is_admin: bool) -> None:
    if not user.is_approved:
        text, markup = access_screen(user)
        await message.answer(text, reply_markup=markup)
        return
    await message.answer(
        "Нижнее меню включено. Если старые кнопки уехали вверх, нажми «Меню».",
        reply_markup=persistent_menu(is_admin),
    )
    await message.answer(
        "Готов следить за кошельками. Добавь адреса и при желании поставь минимальную сумму.",
        reply_markup=main_menu(is_admin),
    )


@router.callback_query(F.data == ACCESS_REQUEST_CALLBACK)
async def request_access(query: CallbackQuery, user: User, db: Database, settings: Settings, bot: Bot) -> None:
    if user.is_approved:
        await query.answer("Доступ уже есть, нажми /start")
        return
    if not can_reapply(user):
        await query.answer()
        text, markup = access_screen(user)
        if query.message is not None:
            await query.message.edit_text(text, reply_markup=markup)
        return

    db.set_user_status(user.user_id, STATUS_PENDING)
    await query.answer("Заявка отправлена")
    if query.message is not None:
        await query.message.edit_text(
            "🕓 Заявка отправлена. Как только администратор её рассмотрит, я пришлю сообщение."
        )
    await notify_admins_about_application(bot, db, settings, user.user_id)


async def notify_admins_about_application(bot: Bot, db: Database, settings: Settings, user_id: int) -> None:
    user = db.get_user(user_id)
    if user is None:
        return
    username = f"@{html.escape(user.username)}" if user.username else "нет"
    text = (
        "📨 <b>Новая заявка на доступ</b>\n\n"
        f"Имя: <b>{html.escape(user.full_name or 'без имени')}</b>\n"
        f"Username: {username}\n"
        f"User id: <code>{user.user_id}</code>"
    )
    for admin_id in settings.telegram_admin_user_ids:
        await safe_send(bot, admin_id, text, reply_markup=application_menu(user.user_id))
    logger.info("Access request from user %s", user.user_id)
