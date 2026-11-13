"""Обработчики для одобренных пользователей: свои кошельки и свои настройки.

Все операции с кошельками идут через db.get_wallet(id, owner_id=user.user_id):
пользователь не может открыть или изменить чужой кошелек, даже если подставит
чужой id в callback_data. Админ смотрит чужие кошельки только через админку.
"""
from __future__ import annotations

import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db import Database, User, short_address
from app.detector import BuyEvent
from app.helius import HeliusClient
from app.keyboards import (
    BTN_ADD_WALLET,
    BTN_HELP,
    BTN_MENU,
    BTN_MY_MIN,
    BTN_TEST_ALERT,
    BTN_WALLETS,
    confirm_delete_menu,
    main_menu,
    persistent_menu,
    wallet_details_menu,
    wallets_menu,
)
from app.notifier import format_wallet_details, notify_buy_event, safe_send


logger = logging.getLogger(__name__)

BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# Общий минимум бота (fallback для тех, кто не задал свой). Ключ остался от
# однопользовательской версии.
GLOBAL_MIN_KEY = "global_min_sol"
MAX_LABEL_LENGTH = 40


class BotStates(StatesGroup):
    adding_wallet = State()
    renaming_wallet = State()
    setting_wallet_min = State()
    setting_my_min = State()


router = Router(name="user")


def global_min_sol(db: Database, settings: Settings) -> float:
    return float(db.get_setting(GLOBAL_MIN_KEY, str(settings.default_min_sol)))


def wallet_limit_for(db: Database, settings: Settings, user: User, is_admin: bool) -> int | None:
    """None — без ограничений (для админов)."""
    if is_admin:
        return None
    return db.effective_wallet_limit(user, settings.default_wallet_limit)


async def sync_helius_after_change(
    target: Message,
    db: Database,
    helius: HeliusClient,
    settings: Settings,
    is_admin: bool,
) -> None:
    """Обновляем Helius webhook, если общий список адресов изменился.

    Обычный пользователь не видит технических деталей (и сколько адресов
    отслеживает бот в целом). Об ошибке он узнаёт коротко, а админ — подробно.
    """
    try:
        result = await helius.sync_tracked()
        ok = True
    except Exception as exc:
        logger.exception("Helius sync failed")
        result = f"Helius sync не удался: {exc}"
        ok = False

    if is_admin:
        await target.answer(html.escape(result))
        return
    if not ok:
        await target.answer("⚠️ Не удалось сразу включить слежку. Администратор уже получил уведомление.")
        for admin_id in settings.telegram_admin_user_ids:
            await safe_send(target.bot, admin_id, f"⚠️ {html.escape(result)}")


def _wallets_header(db: Database, settings: Settings, user: User, is_admin: bool) -> str:
    count = db.count_wallets(user.user_id)
    limit = wallet_limit_for(db, settings, user, is_admin)
    limit_text = "без лимита" if limit is None else f"{count}/{limit}"
    return f"Твои кошельки ({limit_text}):"


async def _show_wallets(target: Message, db: Database, settings: Settings, user: User, is_admin: bool, edit: bool = False) -> None:
    text = _wallets_header(db, settings, user, is_admin)
    markup = wallets_menu(db.list_wallets(owner_id=user.user_id))
    if edit:
        await target.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


# ------------------------------------------------------------------ menu


@router.message(Command("menu"))
@router.message(F.text == BTN_MENU)
async def menu_command(message: Message, state: FSMContext, is_admin: bool) -> None:
    await state.clear()
    await message.answer("Главное меню:", reply_markup=persistent_menu(is_admin))
    await message.answer("Выбери действие:", reply_markup=main_menu(is_admin))


@router.callback_query(F.data == "menu")
async def menu_callback(query: CallbackQuery, state: FSMContext, is_admin: bool) -> None:
    await state.clear()
    await query.message.edit_text("Главное меню:", reply_markup=main_menu(is_admin))
    await query.answer()


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def help_command(message: Message, is_admin: bool) -> None:
    await message.answer(_help_text(is_admin), reply_markup=main_menu(is_admin))


@router.callback_query(F.data == "help")
async def help_callback(query: CallbackQuery, is_admin: bool) -> None:
    await query.message.edit_text(_help_text(is_admin), reply_markup=main_menu(is_admin))
    await query.answer()


@router.message(Command("whoami"))
async def whoami_command(message: Message, user: User) -> None:
    await message.answer(f"Твой Telegram user id:\n<code>{user.user_id}</code>")


# --------------------------------------------------------------- wallets


@router.message(Command("wallets"))
@router.message(F.text == BTN_WALLETS)
async def wallets_command(message: Message, db: Database, settings: Settings, user: User, is_admin: bool) -> None:
    await _show_wallets(message, db, settings, user, is_admin)


@router.callback_query(F.data == "wallets:list")
async def wallets_callback(query: CallbackQuery, db: Database, settings: Settings, user: User, is_admin: bool) -> None:
    await _show_wallets(query.message, db, settings, user, is_admin, edit=True)
    await query.answer()


async def _start_adding(target: Message, state: FSMContext, db: Database, settings: Settings, user: User, is_admin: bool) -> None:
    limit = wallet_limit_for(db, settings, user, is_admin)
    if limit is not None and db.count_wallets(user.user_id) >= limit:
        await target.answer(
            f"Достигнут лимит: <b>{limit}</b> кошельков. Удали ненужный или попроси администратора увеличить лимит."
        )
        return
    await state.set_state(BotStates.adding_wallet)
    await target.answer(
        "Отправь адрес кошелька и подпись через пробел.\n"
        "Пример:\n"
        "<code>7xKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx Красный</code>"
    )


@router.message(F.text == BTN_ADD_WALLET)
async def add_wallet_button(message: Message, state: FSMContext, db: Database, settings: Settings, user: User, is_admin: bool) -> None:
    await _start_adding(message, state, db, settings, user, is_admin)


@router.callback_query(F.data == "wallets:add")
async def add_wallet_callback(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings, user: User, is_admin: bool) -> None:
    await query.answer()
    await _start_adding(query.message, state, db, settings, user, is_admin)


@router.message(BotStates.adding_wallet)
async def add_wallet_message(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    helius: HeliusClient,
    user: User,
    is_admin: bool,
) -> None:
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if not parts or not _looks_like_wallet(parts[0]):
        await message.answer("Не похоже на Solana-адрес. Попробуй еще раз или нажми «Меню».")
        return

    address = parts[0]
    already_mine = db.get_owner_wallet_by_address(user.user_id, address) is not None
    limit = wallet_limit_for(db, settings, user, is_admin)
    if not already_mine and limit is not None and db.count_wallets(user.user_id) >= limit:
        await state.clear()
        await message.answer(f"Достигнут лимит: <b>{limit}</b> кошельков.")
        return

    label = parts[1].strip()[:MAX_LABEL_LENGTH] if len(parts) > 1 else short_address(address)
    wallet = db.add_wallet(user.user_id, address, label)
    await state.clear()
    await message.answer(
        f"{'Обновил' if already_mine else 'Добавил'}: <b>{html.escape(wallet.effective_label)}</b>\n"
        f"<code>{wallet.address}</code>"
    )
    await sync_helius_after_change(message, db, helius, settings, is_admin)
    await _show_wallets(message, db, settings, user, is_admin)


def _wallet_id(data: str) -> int:
    return int(data.split(":")[1])


async def _own_wallet(query: CallbackQuery, db: Database, user: User):
    wallet = db.get_wallet(_wallet_id(query.data), owner_id=user.user_id)
    if wallet is None:
        await query.answer("Кошелек не найден.", show_alert=True)
    return wallet


@router.callback_query(F.data.regexp(r"^wallet:\d+$"))
async def wallet_details_callback(query: CallbackQuery, db: Database, settings: Settings, user: User) -> None:
    wallet = await _own_wallet(query, db, user)
    if not wallet:
        return
    await query.message.edit_text(
        format_wallet_details(wallet, global_min_sol(db, settings)),
        reply_markup=wallet_details_menu(wallet),
    )
    await query.answer()


@router.callback_query(F.data.regexp(r"^wallet:\d+:toggle$"))
async def toggle_wallet_callback(
    query: CallbackQuery,
    db: Database,
    settings: Settings,
    helius: HeliusClient,
    user: User,
    is_admin: bool,
) -> None:
    wallet = await _own_wallet(query, db, user)
    if not wallet:
        return
    db.set_wallet_enabled(wallet.id, not wallet.enabled)
    updated = db.get_wallet(wallet.id, owner_id=user.user_id)
    await query.message.edit_text(
        format_wallet_details(updated, global_min_sol(db, settings)),
        reply_markup=wallet_details_menu(updated),
    )
    await query.answer("Статус изменен.")
    await sync_helius_after_change(query.message, db, helius, settings, is_admin)


@router.callback_query(F.data.regexp(r"^wallet:\d+:rename$"))
async def rename_wallet_callback(query: CallbackQuery, state: FSMContext, db: Database, user: User) -> None:
    wallet = await _own_wallet(query, db, user)
    if not wallet:
        return
    await state.update_data(wallet_id=wallet.id)
    await state.set_state(BotStates.renaming_wallet)
    await query.message.answer("Отправь новую подпись для кошелька.")
    await query.answer()


@router.message(BotStates.renaming_wallet)
async def rename_wallet_message(message: Message, state: FSMContext, db: Database, settings: Settings, user: User, is_admin: bool) -> None:
    data = await state.get_data()
    wallet = db.get_wallet(int(data.get("wallet_id", 0)), owner_id=user.user_id)
    label = (message.text or "").strip()[:MAX_LABEL_LENGTH]
    if wallet is None:
        await state.clear()
        await message.answer("Кошелек не найден.")
        return
    if not label:
        await message.answer("Подпись не должна быть пустой.")
        return
    db.set_wallet_label(wallet.id, label)
    await state.clear()
    await message.answer("Подпись обновлена.")
    await _show_wallets(message, db, settings, user, is_admin)


@router.callback_query(F.data.regexp(r"^wallet:\d+:min$"))
async def wallet_min_callback(query: CallbackQuery, state: FSMContext, db: Database, user: User) -> None:
    wallet = await _own_wallet(query, db, user)
    if not wallet:
        return
    await state.update_data(wallet_id=wallet.id)
    await state.set_state(BotStates.setting_wallet_min)
    await query.message.answer("Отправь минимальную сумму в SOL для этого кошелька. Например: <code>0.25</code>")
    await query.answer()


@router.message(BotStates.setting_wallet_min)
async def wallet_min_message(message: Message, state: FSMContext, db: Database, settings: Settings, user: User, is_admin: bool) -> None:
    value = _parse_positive_float(message.text)
    if value is None:
        await message.answer("Нужно число больше или равно 0. Например: <code>0.25</code>")
        return
    data = await state.get_data()
    wallet = db.get_wallet(int(data.get("wallet_id", 0)), owner_id=user.user_id)
    await state.clear()
    if wallet is None:
        await message.answer("Кошелек не найден.")
        return
    db.set_wallet_min_sol(wallet.id, value)
    await message.answer("Минимум для кошелька обновлен.")
    await _show_wallets(message, db, settings, user, is_admin)


@router.callback_query(F.data.regexp(r"^wallet:\d+:min_reset$"))
async def wallet_min_reset_callback(query: CallbackQuery, db: Database, settings: Settings, user: User) -> None:
    wallet = await _own_wallet(query, db, user)
    if not wallet:
        return
    db.set_wallet_min_sol(wallet.id, None)
    wallet = db.get_wallet(wallet.id, owner_id=user.user_id)
    await query.message.edit_text(
        format_wallet_details(wallet, global_min_sol(db, settings)),
        reply_markup=wallet_details_menu(wallet),
    )
    await query.answer("Теперь используется твой общий минимум.")


@router.callback_query(F.data.regexp(r"^wallet:\d+:delete$"))
async def delete_wallet_callback(query: CallbackQuery, db: Database, user: User) -> None:
    wallet = await _own_wallet(query, db, user)
    if not wallet:
        return
    await query.message.edit_text(
        f"Удалить кошелек <b>{html.escape(wallet.effective_label)}</b>?",
        reply_markup=confirm_delete_menu(wallet),
    )
    await query.answer()


@router.callback_query(F.data.regexp(r"^wallet:\d+:delete_yes$"))
async def delete_wallet_confirm_callback(
    query: CallbackQuery,
    db: Database,
    settings: Settings,
    helius: HeliusClient,
    user: User,
    is_admin: bool,
) -> None:
    wallet = await _own_wallet(query, db, user)
    if not wallet:
        return
    db.delete_wallet(wallet.id)
    await query.answer("Кошелек удален.")
    await _show_wallets(query.message, db, settings, user, is_admin, edit=True)
    await sync_helius_after_change(query.message, db, helius, settings, is_admin)


# ------------------------------------------------------------ own minimum


async def _ask_my_min(target: Message, state: FSMContext, db: Database, settings: Settings, user: User) -> None:
    current = user.min_sol if user.min_sol is not None else global_min_sol(db, settings)
    await state.set_state(BotStates.setting_my_min)
    await target.answer(
        f"Твой минимум для pump-покупок и продаж: <b>{current} SOL</b>\n"
        "Он действует на все твои кошельки, у которых нет своего минимума.\n"
        "Отправь новое число. Например: <code>0.1</code>"
    )


# «Глобальный минимум» — текст кнопки из старой клавиатуры, она может остаться у пользователей.
@router.message(F.text.in_({BTN_MY_MIN, "Глобальный минимум"}))
async def my_min_button(message: Message, state: FSMContext, db: Database, settings: Settings, user: User) -> None:
    await _ask_my_min(message, state, db, settings, user)


@router.callback_query(F.data == "settings:my_min")
async def my_min_callback(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings, user: User) -> None:
    await query.answer()
    await _ask_my_min(query.message, state, db, settings, user)


@router.message(BotStates.setting_my_min)
async def my_min_message(message: Message, state: FSMContext, db: Database, user: User, is_admin: bool) -> None:
    value = _parse_positive_float(message.text)
    if value is None:
        await message.answer("Нужно число больше или равно 0. Например: <code>0.1</code>")
        return
    db.set_user_min_sol(user.user_id, value)
    await state.clear()
    await message.answer(f"Твой минимум обновлен: <b>{value} SOL</b>", reply_markup=main_menu(is_admin))


# ------------------------------------------------------------ test alert


@router.message(Command("test_alert"))
@router.message(F.text == BTN_TEST_ALERT)
async def test_alert_command(message: Message, db: Database, user: User, bot: Bot) -> None:
    """Локальная проверка доставки: Helius не участвует, credits не тратятся."""
    wallets = db.list_wallets(owner_id=user.user_id, enabled_only=True)
    if not wallets:
        await message.answer("Сначала добавь и включи хотя бы один кошелек.")
        return
    event = BuyEvent(
        wallet=wallets[0],
        signature="TEST_SIGNATURE_DO_NOT_OPEN",
        token_mint="TEST_MINT_pump",
        token_amount=1234567,
        sol_spent=0.1234,
        source="LOCAL_TEST",
        timestamp=None,
    )
    await notify_buy_event(bot, event)
    await message.answer("Тест отправлен. Если выше пришла зеленая покупка, уведомления работают.")


# ---------------------------------------------------------------- helpers


def _looks_like_wallet(value: str) -> bool:
    return bool(BASE58_RE.match(value.strip()))


def _parse_positive_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw.strip().replace(",", "."))
    except ValueError:
        return None
    if value < 0 or value != value or value == float("inf"):
        return None
    return value


def _help_text(is_admin: bool) -> str:
    text = (
        "<b>Как пользоваться</b>\n\n"
        "1. Нажми «Добавить кошелек».\n"
        "2. Отправь адрес и подпись: <code>ADDRESS Alpha wallet</code>.\n"
        "3. Бот сообщает о любой активности: swap, перевод SOL/токена и другие действия.\n"
        "4. Минимальная сумма применяется к подробным pump-покупкам и продажам; "
        "универсальные уведомления приходят без порога.\n\n"
        "Другие пользователи бота не видят твоих кошельков и подписей.\n\n"
        "Команды: /menu, /wallets, /test_alert, /whoami, /help"
    )
    if is_admin:
        text += "\nАдмин: /admin, /status, /connection, /limit, /sync, /debug_on, /debug_off"
    return text
