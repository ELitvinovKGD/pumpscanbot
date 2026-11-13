from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.db import (
    STATUS_APPROVED,
    STATUS_BLOCKED,
    STATUS_PENDING,
    STATUS_REJECTED,
    User,
    Wallet,
    short_address,
)


# Тексты кнопок нижней клавиатуры. Обработчики сравнивают с ними.
BTN_MENU = "Меню"
BTN_WALLETS = "Кошельки"
BTN_ADD_WALLET = "Добавить кошелек"
BTN_MY_MIN = "Мой минимум"
BTN_TEST_ALERT = "Тест уведомления"
BTN_HELP = "Помощь"
BTN_ADMIN = "Админка"

STATUS_ICONS = {
    STATUS_PENDING: "🕓",
    STATUS_APPROVED: "✅",
    STATUS_REJECTED: "✖️",
    STATUS_BLOCKED: "⛔",
}
STATUS_NAMES = {
    STATUS_PENDING: "ждёт решения",
    STATUS_APPROVED: "доступ есть",
    STATUS_REJECTED: "отклонён",
    STATUS_BLOCKED: "заблокирован",
}


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


# ------------------------------------------------------------------ access


def request_access_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("📨 Запросить доступ", "access:request")]])


def application_menu(user_id: int) -> InlineKeyboardMarkup:
    """Кнопки под заявкой, которая приходит админу."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("✅ Одобрить", f"adm:approve:{user_id}"), btn("✖️ Отклонить", f"adm:reject:{user_id}")],
            [btn("Карточка пользователя", f"adm:user:{user_id}")],
        ]
    )


# -------------------------------------------------------------- user menus


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [btn("Кошельки", "wallets:list")],
        [btn("Добавить кошелек", "wallets:add")],
        [btn("Мой минимум", "settings:my_min")],
        [btn("Помощь", "help")],
    ]
    if is_admin:
        rows.append([btn("🛠 Админка", "adm:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def persistent_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Клавиатура быстрых действий внизу Telegram.

    Не запрашиваем persistent-режим: на мобильном он перехватывает системный
    жест «назад» даже после скрытия клавиатуры.
    """
    keyboard = [
        [KeyboardButton(text=BTN_MENU), KeyboardButton(text=BTN_WALLETS)],
        [KeyboardButton(text=BTN_ADD_WALLET), KeyboardButton(text=BTN_MY_MIN)],
        [KeyboardButton(text=BTN_TEST_ALERT), KeyboardButton(text=BTN_HELP)],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=False,
        input_field_placeholder="Выбери действие",
    )


def wallets_menu(wallets: list[Wallet]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for wallet in wallets:
        status = "✅" if wallet.enabled else "⏸"
        text = f"{status} {wallet.effective_label} · {short_address(wallet.address)}"
        rows.append([btn(text, f"wallet:{wallet.id}")])
    rows.append([btn("Добавить", "wallets:add")])
    rows.append([btn("Назад", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_details_menu(wallet: Wallet) -> InlineKeyboardMarkup:
    toggle_text = "Выключить" if wallet.enabled else "Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(toggle_text, f"wallet:{wallet.id}:toggle"), btn("Переименовать", f"wallet:{wallet.id}:rename")],
            [btn("Мин. сумма", f"wallet:{wallet.id}:min"), btn("Сброс мин.", f"wallet:{wallet.id}:min_reset")],
            [btn("Удалить", f"wallet:{wallet.id}:delete")],
            [btn("Назад", "wallets:list")],
        ]
    )


def confirm_delete_menu(wallet: Wallet) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("Да, удалить", f"wallet:{wallet.id}:delete_yes"), btn("Отмена", f"wallet:{wallet.id}")],
        ]
    )


# -------------------------------------------------------------- admin menus


def admin_home_menu(pending_count: int, debug_enabled: bool) -> InlineKeyboardMarkup:
    pending_text = f"🕓 Заявки ({pending_count})" if pending_count else "🕓 Заявки"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn(pending_text, "adm:list:pending"), btn("👥 Пользователи", "adm:list:all")],
            [btn("Лимит кошельков по умолчанию", "adm:default_limit")],
            [btn("Статус", "adm:status"), btn("Проверить связь", "adm:connection")],
            [btn("Лимит Helius", "adm:helius_limit"), btn("Sync Helius", "adm:sync")],
            [btn(f"Debug: {'вкл' if debug_enabled else 'выкл'}", "adm:debug_toggle")],
            [btn("Назад", "menu")],
        ]
    )


def admin_users_menu(users: list[User], wallet_counts: dict[int, int], back: str = "adm:home") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for user in users[:50]:
        icon = STATUS_ICONS.get(user.status, "•")
        name = user.full_name or (f"@{user.username}" if user.username else str(user.user_id))
        text = f"{icon} {name} · {wallet_counts.get(user.user_id, 0)} кош."
        rows.append([btn(text[:60], f"adm:user:{user.user_id}")])
    rows.append([btn("Назад", back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_menu(user: User, is_admin_user: bool) -> InlineKeyboardMarkup:
    uid = user.user_id
    rows: list[list[InlineKeyboardButton]] = []
    if not is_admin_user:
        if user.status in {STATUS_PENDING, STATUS_REJECTED}:
            rows.append([btn("✅ Одобрить", f"adm:approve:{uid}"), btn("✖️ Отклонить", f"adm:reject:{uid}")])
        elif user.status == STATUS_APPROVED:
            rows.append([btn("⛔ Заблокировать", f"adm:block:{uid}")])
        elif user.status == STATUS_BLOCKED:
            rows.append([btn("Разблокировать", f"adm:approve:{uid}")])
        rows.append([btn("Лимит кошельков", f"adm:limit:{uid}"), btn("Сброс лимита", f"adm:limit_reset:{uid}")])
    rows.append([btn("👛 Кошельки пользователя", f"adm:wallets:{uid}")])
    rows.append([btn("Назад к списку", "adm:list:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_back_to_user(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("Назад", f"adm:user:{user_id}")]])


def admin_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("Назад в админку", "adm:home")]])
