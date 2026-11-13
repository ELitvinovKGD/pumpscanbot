"""Админка: заявки, пользователи, лимиты и служебные функции бота.

Весь роутер закрыт фильтром IsAdmin: обычный пользователь не может вызвать ни
одну команду или кнопку отсюда, даже подставив callback_data вручную.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Any

import httpx
from aiogram import Bot, F, Router
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db import (
    STATUS_APPROVED,
    STATUS_BLOCKED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Database,
    User,
)
from app.helius import HeliusClient
from app.keyboards import (
    BTN_ADMIN,
    STATUS_ICONS,
    STATUS_NAMES,
    admin_back_home,
    admin_back_to_user,
    admin_home_menu,
    admin_user_menu,
    admin_users_menu,
    main_menu,
    persistent_menu,
)
from app.notifier import DISPLAY_TZ, safe_send
from app.telegram_bot import global_min_sol
from app.usage import format_helius_usage


logger = logging.getLogger(__name__)

DEBUG_KEY = "debug_webhook_notifications"


class IsAdmin(Filter):
    async def __call__(self, event: Any, is_admin: bool = False) -> bool:
        return is_admin


class AdminStates(StatesGroup):
    setting_user_limit = State()
    setting_default_limit = State()


router = Router(name="admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _fmt_time(timestamp: int | None) -> str:
    if not timestamp:
        return "—"
    return datetime.fromtimestamp(int(timestamp), DISPLAY_TZ).strftime("%Y-%m-%d %H:%M")


def _target_id(data: str) -> int:
    return int(data.rsplit(":", 1)[1])


# ------------------------------------------------------------------- home


def _home_text(db: Database, settings: Settings) -> str:
    total_wallets = len(db.list_wallets())
    tracked = len(db.tracked_addresses())
    return (
        "🛠 <b>Админка</b>\n\n"
        f"Заявок ждёт: <b>{db.count_users(STATUS_PENDING)}</b>\n"
        f"Пользователей с доступом: <b>{db.count_users(STATUS_APPROVED)}</b>\n"
        f"Заблокировано: <b>{db.count_users(STATUS_BLOCKED)}</b>\n"
        f"Кошельков всего: <b>{total_wallets}</b> (уникальных отслеживаемых адресов: <b>{tracked}</b>)\n"
        f"Лимит кошельков по умолчанию: <b>{db.default_wallet_limit(settings.default_wallet_limit)}</b>"
    )


def _home_markup(db: Database):
    return admin_home_menu(db.count_users(STATUS_PENDING), db.get_setting(DEBUG_KEY) == "1")


@router.message(Command("admin"))
@router.message(F.text == BTN_ADMIN)
async def admin_command(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    await message.answer(_home_text(db, settings), reply_markup=_home_markup(db))


@router.callback_query(F.data == "adm:home")
async def admin_home_callback(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    await query.message.edit_text(_home_text(db, settings), reply_markup=_home_markup(db))
    await query.answer()


# ------------------------------------------------------------------ users


@router.callback_query(F.data.in_({"adm:list:pending", "adm:list:all"}))
async def admin_users_callback(query: CallbackQuery, db: Database) -> None:
    pending_only = query.data.endswith("pending")
    users = db.list_users(STATUS_PENDING if pending_only else None)
    counts = {user.user_id: db.count_wallets(user.user_id) for user in users}
    if pending_only:
        title = "🕓 <b>Заявки</b>" if users else "Новых заявок нет."
    else:
        title = "👥 <b>Пользователи</b>" if users else "Пользователей пока нет."
    await query.message.edit_text(title, reply_markup=admin_users_menu(users, counts))
    await query.answer()


def _user_card(db: Database, settings: Settings, user: User) -> str:
    is_admin_user = settings.is_admin(user.user_id)
    count = db.count_wallets(user.user_id)
    if is_admin_user:
        limit_text = f"{count} (админ, без лимита)"
    else:
        limit = db.effective_wallet_limit(user, settings.default_wallet_limit)
        suffix = "" if user.wallet_limit is not None else " — по умолчанию"
        limit_text = f"{count}/{limit}{suffix}"
    username = f"@{html.escape(user.username)}" if user.username else "нет"
    min_text = f"{user.min_sol} SOL" if user.min_sol is not None else f"общий ({global_min_sol(db, settings)} SOL)"
    status = STATUS_NAMES.get(user.status, user.status)
    return (
        f"{STATUS_ICONS.get(user.status, '•')} <b>{html.escape(user.full_name or 'без имени')}</b>"
        f"{' · админ' if is_admin_user else ''}\n\n"
        f"Username: {username}\n"
        f"User id: <code>{user.user_id}</code>\n"
        f"Статус: <b>{status}</b>\n"
        f"Кошельки: <b>{limit_text}</b>\n"
        f"Минимум: <b>{min_text}</b>\n"
        f"Впервые написал: {_fmt_time(user.created_at)}\n"
        f"Заявка: {_fmt_time(user.requested_at)}\n"
        f"Решение: {_fmt_time(user.decided_at)}"
    )


async def _show_user(query: CallbackQuery, db: Database, settings: Settings, user_id: int) -> None:
    user = db.get_user(user_id)
    if user is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    await query.message.edit_text(
        _user_card(db, settings, user),
        reply_markup=admin_user_menu(user, settings.is_admin(user.user_id)),
    )


@router.callback_query(F.data.regexp(r"^adm:user:\d+$"))
async def admin_user_callback(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    await _show_user(query, db, settings, _target_id(query.data))
    await query.answer()


async def _change_status(
    query: CallbackQuery,
    db: Database,
    settings: Settings,
    helius: HeliusClient,
    bot: Bot,
    new_status: str,
) -> None:
    user_id = _target_id(query.data)
    user = db.get_user(user_id)
    if user is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    if settings.is_admin(user_id):
        await query.answer("Статус админа меняется только через .env.", show_alert=True)
        return
    if user.status == new_status:
        await query.answer("Уже применено.")
        await _show_user(query, db, settings, user_id)
        return

    old_status = user.status
    db.set_user_status(user_id, new_status)

    if new_status == STATUS_APPROVED:
        await safe_send(
            bot,
            user.chat_id or user_id,
            "✅ Доступ к боту одобрен! Добавь кошельки, за которыми хочешь следить.",
            reply_markup=persistent_menu(False),
        )
        await safe_send(bot, user.chat_id or user_id, "Выбери действие:", reply_markup=main_menu(False))
        answer = "Доступ выдан."
    elif new_status == STATUS_REJECTED:
        await safe_send(bot, user.chat_id or user_id, "Заявка на доступ отклонена.")
        answer = "Заявка отклонена."
    else:
        await safe_send(bot, user.chat_id or user_id, "⛔ Доступ к боту закрыт администратором.")
        answer = "Пользователь заблокирован."

    await query.answer(answer)
    await _show_user(query, db, settings, user_id)
    logger.info("Admin %s changed user %s status: %s -> %s", query.from_user.id, user_id, old_status, new_status)

    # Кошельки заблокированного пользователя больше не слушаем, при
    # разблокировке — возвращаем. Если адреса не изменились, Helius не трогаем.
    if STATUS_APPROVED in {old_status, new_status} and db.count_wallets(user_id):
        try:
            result = await helius.sync_tracked()
        except Exception as exc:
            logger.exception("Helius sync after status change failed")
            result = f"Helius sync не удался: {exc}"
        await query.message.answer(html.escape(result))


@router.callback_query(F.data.regexp(r"^adm:approve:\d+$"))
async def admin_approve(query: CallbackQuery, db: Database, settings: Settings, helius: HeliusClient, bot: Bot) -> None:
    await _change_status(query, db, settings, helius, bot, STATUS_APPROVED)


@router.callback_query(F.data.regexp(r"^adm:reject:\d+$"))
async def admin_reject(query: CallbackQuery, db: Database, settings: Settings, helius: HeliusClient, bot: Bot) -> None:
    await _change_status(query, db, settings, helius, bot, STATUS_REJECTED)


@router.callback_query(F.data.regexp(r"^adm:block:\d+$"))
async def admin_block(query: CallbackQuery, db: Database, settings: Settings, helius: HeliusClient, bot: Bot) -> None:
    await _change_status(query, db, settings, helius, bot, STATUS_BLOCKED)


@router.callback_query(F.data.regexp(r"^adm:wallets:\d+$"))
async def admin_user_wallets(query: CallbackQuery, db: Database, settings: Settings) -> None:
    user_id = _target_id(query.data)
    user = db.get_user(user_id)
    wallets = db.list_wallets(owner_id=user_id)
    name = html.escape(user.full_name if user and user.full_name else str(user_id))
    if not wallets:
        await query.message.edit_text(f"У <b>{name}</b> нет кошельков.", reply_markup=admin_back_to_user(user_id))
        await query.answer()
        return

    fallback = global_min_sol(db, settings)
    lines = [f"👛 <b>Кошельки: {name}</b> ({len(wallets)})\n"]
    for wallet in wallets:
        status = "✅" if wallet.enabled else "⏸"
        min_text = f"{wallet.min_sol}" if wallet.min_sol is not None else f"{wallet.effective_min_sol(fallback)} (общий)"
        lines.append(
            f"{status} <b>{html.escape(wallet.effective_label)}</b>\n"
            f"<code>{wallet.address}</code>\nмин. {min_text} SOL"
        )

    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 2 > 3800:
            chunks.append(current)
            current = ""
        current += line + "\n\n"
    chunks.append(current)

    await query.message.edit_text(chunks[0], reply_markup=admin_back_to_user(user_id) if len(chunks) == 1 else None)
    for index, chunk in enumerate(chunks[1:], start=1):
        markup = admin_back_to_user(user_id) if index == len(chunks) - 1 else None
        await query.message.answer(chunk, reply_markup=markup)
    await query.answer()


# ----------------------------------------------------------------- limits


@router.callback_query(F.data.regexp(r"^adm:limit:\d+$"))
async def admin_limit_callback(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    user_id = _target_id(query.data)
    user = db.get_user(user_id)
    if user is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    current = db.effective_wallet_limit(user, settings.default_wallet_limit)
    await state.set_state(AdminStates.setting_user_limit)
    await state.update_data(target_user_id=user_id)
    await query.message.answer(
        f"Текущий лимит: <b>{current}</b>. Отправь новое число кошельков для этого пользователя (0 — запретить добавление)."
    )
    await query.answer()


@router.message(AdminStates.setting_user_limit)
async def admin_limit_message(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    value = _parse_limit(message.text)
    if value is None:
        await message.answer("Нужно целое число от 0 до 1000.")
        return
    data = await state.get_data()
    user_id = int(data.get("target_user_id", 0))
    await state.clear()
    db.set_user_wallet_limit(user_id, value)
    count = db.count_wallets(user_id)
    note = ""
    if count > value:
        note = f"\nСейчас у пользователя {count} кошельков: они останутся, но новые добавить нельзя, пока он не удалит лишние."
    await message.answer(f"Лимит обновлён: <b>{value}</b>.{note}", reply_markup=admin_back_to_user(user_id))


@router.callback_query(F.data.regexp(r"^adm:limit_reset:\d+$"))
async def admin_limit_reset(query: CallbackQuery, db: Database, settings: Settings) -> None:
    user_id = _target_id(query.data)
    db.set_user_wallet_limit(user_id, None)
    await query.answer("Лимит сброшен на значение по умолчанию.")
    await _show_user(query, db, settings, user_id)


@router.callback_query(F.data == "adm:default_limit")
async def admin_default_limit_callback(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.set_state(AdminStates.setting_default_limit)
    await query.message.answer(
        f"Лимит по умолчанию: <b>{db.default_wallet_limit(settings.default_wallet_limit)}</b>.\n"
        "Он действует для всех, у кого нет персонального лимита. Отправь новое число."
    )
    await query.answer()


@router.message(AdminStates.setting_default_limit)
async def admin_default_limit_message(message: Message, state: FSMContext, db: Database) -> None:
    value = _parse_limit(message.text)
    if value is None:
        await message.answer("Нужно целое число от 0 до 1000.")
        return
    await state.clear()
    db.set_setting("default_wallet_limit", str(value))
    await message.answer(f"Лимит по умолчанию: <b>{value}</b>.", reply_markup=admin_back_home())


def _parse_limit(raw: str | None) -> int | None:
    try:
        value = int((raw or "").strip())
    except ValueError:
        return None
    return value if 0 <= value <= 1000 else None


# ---------------------------------------------------------- bot service


@router.message(Command("status"))
@router.message(F.text == "Статус")
async def status_command(message: Message, db: Database) -> None:
    await message.answer(format_status(db))


@router.callback_query(F.data == "adm:status")
async def status_callback(query: CallbackQuery, db: Database) -> None:
    await query.message.answer(format_status(db), reply_markup=admin_back_home())
    await query.answer()


@router.message(Command("connection"))
@router.message(F.text == "Проверить связь")
async def connection_command(message: Message, db: Database, settings: Settings) -> None:
    await message.answer(await check_connection(db, settings))


@router.callback_query(F.data == "adm:connection")
async def connection_callback(query: CallbackQuery, db: Database, settings: Settings) -> None:
    await query.answer("Проверяю…")
    await query.message.answer(await check_connection(db, settings), reply_markup=admin_back_home())


@router.message(Command("limit"))
@router.message(F.text == "Лимит Helius")
async def helius_limit_command(message: Message, helius: HeliusClient) -> None:
    await message.answer(await format_helius_usage(helius))


@router.callback_query(F.data == "adm:helius_limit")
async def helius_limit_callback(query: CallbackQuery, helius: HeliusClient) -> None:
    await query.answer()
    await query.message.answer(await format_helius_usage(helius), reply_markup=admin_back_home())


async def _force_sync(target: Message, helius: HeliusClient) -> None:
    try:
        result = await helius.sync_tracked(force=True)
    except Exception as exc:
        logger.exception("Manual Helius sync failed")
        result = f"Helius sync не удался: {exc}"
    await target.answer(html.escape(result))


@router.message(Command("sync"))
@router.message(F.text == "Sync Helius")
async def sync_command(message: Message, helius: HeliusClient) -> None:
    await _force_sync(message, helius)


@router.callback_query(F.data == "adm:sync")
async def sync_callback(query: CallbackQuery, helius: HeliusClient) -> None:
    await query.answer("Синхронизирую…")
    await _force_sync(query.message, helius)


@router.message(Command("debug_on"))
async def debug_on_command(message: Message, db: Database) -> None:
    db.set_setting(DEBUG_KEY, "1")
    await message.answer("Debug включен: нераспознанные webhook будут приходить админам.")


@router.message(Command("debug_off"))
async def debug_off_command(message: Message, db: Database) -> None:
    db.set_setting(DEBUG_KEY, "0")
    await message.answer("Debug выключен.")


@router.callback_query(F.data == "adm:debug_toggle")
async def debug_toggle_callback(query: CallbackQuery, db: Database, settings: Settings) -> None:
    enabled = db.get_setting(DEBUG_KEY) == "1"
    db.set_setting(DEBUG_KEY, "0" if enabled else "1")
    await query.answer(f"Debug {'выключен' if enabled else 'включен'}.")
    await query.message.edit_text(_home_text(db, settings), reply_markup=_home_markup(db))


def format_status(db: Database) -> str:
    wallets = db.list_wallets()
    enabled_wallets = [wallet for wallet in wallets if wallet.enabled]
    last_webhook_at = db.get_setting("last_webhook_at")
    last_text = _fmt_time(int(last_webhook_at)) if last_webhook_at else "еще не приходил"
    debug_enabled = db.get_setting(DEBUG_KEY) == "1"
    return (
        "🧭 <b>Статус бота</b>\n\n"
        f"Пользователей с доступом: <b>{db.count_users(STATUS_APPROVED)}</b>\n"
        f"Кошельки: <b>{len(enabled_wallets)}</b> включено из <b>{len(wallets)}</b>\n"
        f"Отслеживаемых адресов в Helius: <b>{len(db.tracked_addresses())}</b>\n"
        f"Webhook вызовов всего: <b>{db.get_setting('total_webhook_calls', '0')}</b>\n"
        f"Последний webhook: <b>{last_text}</b>\n"
        f"В последнем webhook транзакций: <b>{db.get_setting('last_webhook_transactions', '0')}</b>\n"
        f"Распознано событий: <b>{db.get_setting('last_webhook_detected', '0')}</b>\n"
        f"Отправлено уведомлений: <b>{db.get_setting('last_webhook_notified', '0')}</b>\n"
        f"Debug: <b>{'включен' if debug_enabled else 'выключен'}</b>"
    )


async def check_connection(db: Database, settings: Settings) -> str:
    """Проверяем публичный маршрут и сохранённый результат Helius sync.

    Запрос идет только на собственный /health. Helius API не вызывается,
    поэтому credits не расходуются.
    """
    public_url = settings.public_webhook_url
    url_is_configured = bool(public_url and "PASTE_" not in public_url)
    public_detail = "URL не заполнен в .env"

    if url_is_configured:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                response = await client.get(
                    f"{public_url}/health",
                    headers={"ngrok-skip-browser-warning": "true"},
                )
            public_ok = response.status_code == 200 and response.json().get("status") == "ok"
            public_detail = "доступен" if public_ok else f"ответ HTTP {response.status_code}"
        except Exception as exc:
            public_detail = f"недоступен ({type(exc).__name__})"

    expected_webhook_url = f"{public_url}/helius/webhook" if url_is_configured else ""
    synced_webhook_url = db.get_setting(HeliusClient.LAST_SYNCED_URL_KEY)
    helius_synced = bool(expected_webhook_url and synced_webhook_url == expected_webhook_url)
    last_webhook_at = db.get_setting("last_webhook_at")
    last_webhook = _fmt_time(int(last_webhook_at)) if last_webhook_at else "еще не приходил"

    current_url = html.escape(public_url) if url_is_configured else "не настроен"
    return (
        "<b>Проверка связи</b>\n\n"
        "Telegram: <b>работает</b>\n"
        "Локальный бот: <b>работает</b>\n"
        f"Публичный URL: <b>{public_detail}</b>\n"
        f"Helius URL: <b>{'синхронизирован' if helius_synced else 'требует sync'}</b>\n"
        f"Последнее событие Helius: <b>{last_webhook}</b>\n\n"
        f"Текущая ссылка: <code>{current_url}</code>\n"
        "Проверка не расходует Helius credits."
    )
