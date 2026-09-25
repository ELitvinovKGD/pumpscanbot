from __future__ import annotations

import re
from datetime import datetime
from html import escape

import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db import Database, short_address
from app.detector import BuyEvent
from app.helius import HeliusClient
from app.keyboards import main_menu, persistent_menu, wallet_details_menu, wallets_menu
from app.notifier import format_wallet_details, notify_buy_event
from app.usage import format_helius_usage


BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
GLOBAL_MIN_KEY = "global_min_sol"


class BotStates(StatesGroup):
    adding_wallet = State()
    renaming_wallet = State()
    setting_wallet_min = State()
    setting_global_min = State()


router = Router()


def _is_allowed(settings: Settings, user_id: int | None) -> bool:
    """Проверяем доступ к управлению ботом.

    Если TELEGRAM_ADMIN_USER_IDS пустой, доступ открыт. Это удобно для быстрого
    локального теста. Для реального использования лучше вписать свой user id.
    """
    if not settings.telegram_admin_user_ids:
        return True
    return user_id in settings.telegram_admin_user_ids


async def _ensure_allowed(message_or_query: Message | CallbackQuery, settings: Settings) -> bool:
    user_id = message_or_query.from_user.id if message_or_query.from_user else None
    if _is_allowed(settings, user_id):
        return True

    text = "У тебя нет доступа к управлению этим ботом."
    if isinstance(message_or_query, CallbackQuery):
        await message_or_query.answer(text, show_alert=True)
    else:
        await message_or_query.answer(text)
    return False


def _global_min_sol(db: Database, settings: Settings) -> float:
    return float(db.get_setting(GLOBAL_MIN_KEY, str(settings.default_min_sol)))


async def _sync_helius_and_report(message: Message, db: Database, helius: HeliusClient) -> None:
    """Обновляем Helius webhook после изменения списка кошельков."""
    addresses = [wallet.address for wallet in db.list_wallets(enabled_only=True)]
    try:
        result = await helius.sync_webhook(addresses)
    except Exception as exc:
        result = f"Helius sync не удался: {exc}"
    await message.answer(result)


@router.message(CommandStart())
async def start(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    db.add_notification_chat(message.chat.id)
    await message.answer(
        "Нижнее меню включено. Если старые кнопки уехали вверх, нажми «Меню».",
        reply_markup=persistent_menu(),
    )
    await message.answer(
        "Готов следить за кошельками. Добавь адреса и поставь минимальную сумму покупки.",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def help_command(message: Message, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer(_help_text(), reply_markup=main_menu())


@router.message(Command("menu"))
async def menu_command(message: Message, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer("Главное меню:", reply_markup=persistent_menu())
    await message.answer("Выбери действие:", reply_markup=main_menu())


@router.message(Command("wallets"))
async def wallets_command(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer("Кошельки:", reply_markup=wallets_menu(db.list_wallets()))


@router.message(Command("limit"))
async def limit_command(message: Message, settings: Settings, helius: HeliusClient) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer(await _format_helius_usage(helius))


@router.message(Command("whoami"))
async def whoami_command(message: Message, settings: Settings) -> None:
    """Показываем Telegram user id для TELEGRAM_ADMIN_USER_IDS."""
    if not await _ensure_allowed(message, settings):
        return
    user_id = message.from_user.id if message.from_user else "unknown"
    await message.answer(
        "Твой Telegram user id:\n"
        f"<code>{user_id}</code>\n\n"
        "Если хочешь закрыть управление ботом только для себя, вставь его в .env:\n"
        f"<code>TELEGRAM_ADMIN_USER_IDS={user_id}</code>"
    )


@router.message(Command("sync"))
async def sync_command(message: Message, db: Database, settings: Settings, helius: HeliusClient) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await _sync_helius_and_report(message, db, helius)


@router.message(Command("status"))
async def status_command(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer(_format_status(db))


@router.message(Command("connection"))
async def connection_command(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer(await _check_connection(db, settings))


@router.message(Command("test_alert"))
async def test_alert_command(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await _send_test_alert(message, db)


@router.message(Command("debug_on"))
async def debug_on_command(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    db.set_setting("debug_webhook_notifications", "1")
    await message.answer(
        "Debug включен. Если webhook придет, но buy/sell не распознается, бот пришлет техническое сообщение."
    )


@router.message(Command("debug_off"))
async def debug_off_command(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    db.set_setting("debug_webhook_notifications", "0")
    await message.answer("Debug выключен.")


@router.message(F.text.casefold() == "меню")
async def menu_button(message: Message, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer("Главное меню:", reply_markup=main_menu())


@router.message(F.text.casefold() == "кошельки")
async def wallets_button(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer("Кошельки:", reply_markup=wallets_menu(db.list_wallets()))


@router.message(F.text.casefold() == "добавить кошелек")
async def add_wallet_button(message: Message, state: FSMContext, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await state.set_state(BotStates.adding_wallet)
    await message.answer(
        "Отправь адрес кошелька и подпись через пробел.\n"
        "Пример:\n"
        "<code>7xKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx Красный</code>"
    )


@router.message(F.text.casefold() == "глобальный минимум")
async def global_min_button(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await state.set_state(BotStates.setting_global_min)
    await message.answer(
        f"Текущий глобальный минимум: <b>{_global_min_sol(db, settings)} SOL</b>\n"
        "Отправь новое число. Например: <code>0.1</code>"
    )


@router.message(F.text.casefold() == "лимит helius")
async def helius_limit_button(message: Message, settings: Settings, helius: HeliusClient) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer(await _format_helius_usage(helius))


@router.message(F.text.casefold() == "статус")
async def status_button(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer(_format_status(db))


@router.message(F.text.casefold() == "проверить связь")
async def connection_button(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await message.answer(await _check_connection(db, settings))


@router.message(F.text.casefold() == "тест уведомления")
async def test_alert_button(message: Message, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await _send_test_alert(message, db)


@router.message(F.text.casefold() == "sync helius")
async def helius_sync_button(message: Message, db: Database, settings: Settings, helius: HeliusClient) -> None:
    if not await _ensure_allowed(message, settings):
        return
    await _sync_helius_and_report(message, db, helius)


@router.callback_query(F.data == "menu")
async def menu_callback(query: CallbackQuery, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    await query.message.edit_text("Главное меню:", reply_markup=main_menu())
    await query.answer()


@router.callback_query(F.data == "help")
async def help_callback(query: CallbackQuery, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    await query.message.edit_text(_help_text(), reply_markup=main_menu())
    await query.answer()


@router.callback_query(F.data == "wallets:list")
async def wallets_callback(query: CallbackQuery, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    await query.message.edit_text("Кошельки:", reply_markup=wallets_menu(db.list_wallets()))
    await query.answer()


@router.callback_query(F.data == "wallets:add")
async def add_wallet_callback(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    await state.set_state(BotStates.adding_wallet)
    await query.message.answer(
        "Отправь адрес кошелька и подпись через пробел.\n"
        "Пример:\n"
        "<code>7xKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx Alpha sniper</code>"
    )
    await query.answer()


@router.message(BotStates.adding_wallet)
async def add_wallet_message(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    helius: HeliusClient,
) -> None:
    if not await _ensure_allowed(message, settings):
        return

    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if not parts or not _looks_like_wallet(parts[0]):
        await message.answer("Не похоже на Solana-адрес. Попробуй еще раз.")
        return

    address = parts[0]
    label = parts[1].strip() if len(parts) > 1 else short_address(address)
    wallet = db.add_wallet(address, label)
    await state.clear()
    await message.answer(f"Добавил: <b>{wallet.effective_label}</b>\n<code>{wallet.address}</code>")
    await _sync_helius_and_report(message, db, helius)
    await message.answer("Кошельки:", reply_markup=wallets_menu(db.list_wallets()))


@router.callback_query(F.data.regexp(r"^wallet:\d+$"))
async def wallet_details_callback(query: CallbackQuery, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    wallet_id = int(query.data.split(":")[1])
    wallet = db.get_wallet(wallet_id)
    if not wallet:
        await query.answer("Кошелек не найден.", show_alert=True)
        return
    await query.message.edit_text(
        format_wallet_details(wallet, _global_min_sol(db, settings)),
        reply_markup=wallet_details_menu(wallet),
    )
    await query.answer()


@router.callback_query(F.data.regexp(r"^wallet:\d+:toggle$"))
async def toggle_wallet_callback(
    query: CallbackQuery,
    db: Database,
    settings: Settings,
    helius: HeliusClient,
) -> None:
    if not await _ensure_allowed(query, settings):
        return
    wallet_id = int(query.data.split(":")[1])
    wallet = db.get_wallet(wallet_id)
    if not wallet:
        await query.answer("Кошелек не найден.", show_alert=True)
        return

    db.set_wallet_enabled(wallet.id, not wallet.enabled)
    updated = db.get_wallet(wallet.id)
    await query.message.edit_text(
        format_wallet_details(updated, _global_min_sol(db, settings)),
        reply_markup=wallet_details_menu(updated),
    )
    await query.answer("Статус изменен.")

    # Отправляем отдельное сообщение, чтобы не ломать уже отредактированное меню.
    await _sync_helius_and_report(query.message, db, helius)


@router.callback_query(F.data.regexp(r"^wallet:\d+:rename$"))
async def rename_wallet_callback(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    wallet_id = int(query.data.split(":")[1])
    await state.update_data(wallet_id=wallet_id)
    await state.set_state(BotStates.renaming_wallet)
    await query.message.answer("Отправь новую подпись для кошелька.")
    await query.answer()


@router.message(BotStates.renaming_wallet)
async def rename_wallet_message(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    data = await state.get_data()
    wallet_id = int(data["wallet_id"])
    label = (message.text or "").strip()
    if not label:
        await message.answer("Подпись не должна быть пустой.")
        return
    db.set_wallet_label(wallet_id, label)
    await state.clear()
    await message.answer("Подпись обновлена.", reply_markup=wallets_menu(db.list_wallets()))


@router.callback_query(F.data.regexp(r"^wallet:\d+:min$"))
async def wallet_min_callback(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    wallet_id = int(query.data.split(":")[1])
    await state.update_data(wallet_id=wallet_id)
    await state.set_state(BotStates.setting_wallet_min)
    await query.message.answer("Отправь минимальную сумму покупки в SOL для этого кошелька. Например: <code>0.25</code>")
    await query.answer()


@router.message(BotStates.setting_wallet_min)
async def wallet_min_message(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    value = _parse_positive_float(message.text)
    if value is None:
        await message.answer("Нужно число больше или равно 0. Например: <code>0.25</code>")
        return
    data = await state.get_data()
    db.set_wallet_min_sol(int(data["wallet_id"]), value)
    await state.clear()
    await message.answer("Минимум для кошелька обновлен.", reply_markup=wallets_menu(db.list_wallets()))


@router.callback_query(F.data.regexp(r"^wallet:\d+:min_reset$"))
async def wallet_min_reset_callback(query: CallbackQuery, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    wallet_id = int(query.data.split(":")[1])
    db.set_wallet_min_sol(wallet_id, None)
    wallet = db.get_wallet(wallet_id)
    await query.message.edit_text(
        format_wallet_details(wallet, _global_min_sol(db, settings)),
        reply_markup=wallet_details_menu(wallet),
    )
    await query.answer("Теперь используется глобальный минимум.")


@router.callback_query(F.data.regexp(r"^wallet:\d+:delete$"))
async def delete_wallet_callback(
    query: CallbackQuery,
    db: Database,
    settings: Settings,
    helius: HeliusClient,
) -> None:
    if not await _ensure_allowed(query, settings):
        return
    wallet_id = int(query.data.split(":")[1])
    db.delete_wallet(wallet_id)
    await query.message.edit_text("Кошелек удален.", reply_markup=wallets_menu(db.list_wallets()))
    await query.answer()
    await _sync_helius_and_report(query.message, db, helius)


@router.callback_query(F.data == "settings:global_min")
async def global_min_callback(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    await state.set_state(BotStates.setting_global_min)
    await query.message.answer(
        f"Текущий глобальный минимум: <b>{_global_min_sol(db, settings)} SOL</b>\n"
        "Отправь новое число. Например: <code>0.1</code>"
    )
    await query.answer()


@router.message(BotStates.setting_global_min)
async def global_min_message(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(message, settings):
        return
    value = _parse_positive_float(message.text)
    if value is None:
        await message.answer("Нужно число больше или равно 0. Например: <code>0.1</code>")
        return
    db.set_setting(GLOBAL_MIN_KEY, str(value))
    await state.clear()
    await message.answer(f"Глобальный минимум обновлен: <b>{value} SOL</b>", reply_markup=main_menu())


@router.callback_query(F.data == "helius:limit")
async def helius_limit_callback(query: CallbackQuery, settings: Settings, helius: HeliusClient) -> None:
    if not await _ensure_allowed(query, settings):
        return
    await query.message.answer(await _format_helius_usage(helius))
    await query.answer()


@router.callback_query(F.data == "helius:sync")
async def helius_sync_callback(query: CallbackQuery, db: Database, settings: Settings, helius: HeliusClient) -> None:
    if not await _ensure_allowed(query, settings):
        return
    await _sync_helius_and_report(query.message, db, helius)
    await query.answer()


@router.callback_query(F.data == "connection:check")
async def connection_callback(query: CallbackQuery, db: Database, settings: Settings) -> None:
    if not await _ensure_allowed(query, settings):
        return
    await query.message.answer(await _check_connection(db, settings))
    await query.answer()


def _looks_like_wallet(value: str) -> bool:
    return bool(BASE58_RE.match(value.strip()))


def _parse_positive_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw.strip().replace(",", "."))
    except ValueError:
        return None
    if value < 0:
        return None
    return value


async def _format_helius_usage(helius: HeliusClient) -> str:
    return await format_helius_usage(helius)


async def _send_test_alert(message: Message, db: Database) -> None:
    """Проверяем, что бот умеет отправлять уведомления в сохраненный Telegram chat.

    Это локальная эмуляция: Helius/ngrok не участвуют и credits не тратятся.
    """
    db.add_notification_chat(message.chat.id)
    wallets = db.list_wallets(enabled_only=True)
    wallet = wallets[0] if wallets else None
    if wallet is None:
        await message.answer("Сначала добавь и включи хотя бы один кошелек.")
        return

    event = BuyEvent(
        wallet=wallet,
        signature="TEST_SIGNATURE_DO_NOT_OPEN",
        token_mint="TEST_MINT_pump",
        token_amount=1234567,
        sol_spent=0.1234,
        source="LOCAL_TEST",
        timestamp=None,
    )
    await notify_buy_event(message.bot, db, event)
    await message.answer("Тест отправлен. Если выше пришла зеленая покупка, Telegram-уведомления работают.")


async def _check_connection(db: Database, settings: Settings) -> str:
    """Проверяем публичный маршрут и локально сохраненный результат Helius sync.

    Запрос идет только на собственный /health через ngrok. Helius API здесь не
    вызывается, поэтому на эту проверку credits не расходуются.
    """
    public_url = settings.public_webhook_url
    url_is_configured = bool(public_url and "PASTE_" not in public_url)
    public_ok = False
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
    if last_webhook_at:
        last_webhook = datetime.fromtimestamp(int(last_webhook_at)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        last_webhook = "еще не приходил"

    current_url = escape(public_url) if url_is_configured else "не настроен"
    return (
        "<b>Проверка связи</b>\n\n"
        "Telegram: <b>работает</b>\n"
        "Локальный бот: <b>работает</b>\n"
        f"ngrok: <b>{public_detail}</b>\n"
        f"Helius URL: <b>{'синхронизирован' if helius_synced else 'требует sync'}</b>\n"
        f"Последнее событие Helius: <b>{last_webhook}</b>\n\n"
        f"Текущая ссылка: <code>{current_url}</code>\n"
        "Проверка не расходует Helius credits."
    )


def _format_status(db: Database) -> str:
    wallets = db.list_wallets()
    enabled_wallets = [wallet for wallet in wallets if wallet.enabled]
    chats = db.list_notification_chats()
    last_webhook_at = db.get_setting("last_webhook_at")
    last_transactions = db.get_setting("last_webhook_transactions", "0")
    last_detected = db.get_setting("last_webhook_detected", "0")
    last_notified = db.get_setting("last_webhook_notified", "0")
    total_calls = db.get_setting("total_webhook_calls", "0")
    debug_enabled = db.get_setting("debug_webhook_notifications") == "1"

    if last_webhook_at:
        last_text = datetime.fromtimestamp(int(last_webhook_at)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        last_text = "еще не приходил"

    return (
        "🧭 <b>Статус бота</b>\n\n"
        f"Кошельки: <b>{len(enabled_wallets)}</b> включено из <b>{len(wallets)}</b>\n"
        f"Telegram chats для уведомлений: <b>{len(chats)}</b>\n"
        f"Webhook вызовов всего: <b>{total_calls}</b>\n"
        f"Последний webhook: <b>{last_text}</b>\n"
        f"В последнем webhook транзакций: <b>{last_transactions}</b>\n"
        f"Распознано buy/sell: <b>{last_detected}</b>\n"
        f"Отправлено уведомлений: <b>{last_notified}</b>\n"
        f"Debug: <b>{'включен' if debug_enabled else 'выключен'}</b>"
    )


def _help_text() -> str:
    return (
        "<b>Как пользоваться</b>\n\n"
        "1. Нажми «Добавить кошелек».\n"
        "2. Отправь адрес и подпись: <code>ADDRESS Alpha wallet</code>.\n"
        "3. Бот сообщает о любой активности: swap, перевод SOL/токена и другие действия.\n"
        "4. Минимальная сумма применяется к подробным pump-покупкам и продажам; "
        "универсальные уведомления приходят без порога.\n\n"
        "Команды: /menu, /wallets, /limit, /status, /connection, /sync, /test_alert, /debug_on, /debug_off, /whoami, /help"
    )
