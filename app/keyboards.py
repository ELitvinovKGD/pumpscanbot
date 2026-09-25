from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.db import Wallet, short_address


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Кошельки", callback_data="wallets:list")],
            [InlineKeyboardButton(text="Добавить кошелек", callback_data="wallets:add")],
            [InlineKeyboardButton(text="Глобальный минимум", callback_data="settings:global_min")],
            [InlineKeyboardButton(text="Лимит Helius", callback_data="helius:limit")],
            [InlineKeyboardButton(text="Проверить связь", callback_data="connection:check")],
            [InlineKeyboardButton(text="Синхронизировать Helius", callback_data="helius:sync")],
            [InlineKeyboardButton(text="Помощь", callback_data="help")],
        ]
    )


def persistent_menu() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу Telegram.

    Inline-кнопки удобны для карточек кошельков, но старое сообщение легко
    теряется выше в чате. Эта клавиатура всегда остается под рукой.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Меню"), KeyboardButton(text="Кошельки")],
            [KeyboardButton(text="Добавить кошелек"), KeyboardButton(text="Глобальный минимум")],
            [KeyboardButton(text="Лимит Helius"), KeyboardButton(text="Проверить связь")],
            [KeyboardButton(text="Статус")],
            [KeyboardButton(text="Sync Helius"), KeyboardButton(text="Тест уведомления")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выбери действие",
    )


def wallets_menu(wallets: list[Wallet]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for wallet in wallets:
        status = "✅" if wallet.enabled else "⏸"
        text = f"{status} {wallet.effective_label} · {short_address(wallet.address)}"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"wallet:{wallet.id}")])
    rows.append([InlineKeyboardButton(text="Добавить", callback_data="wallets:add")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_details_menu(wallet: Wallet) -> InlineKeyboardMarkup:
    toggle_text = "Выключить" if wallet.enabled else "Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=toggle_text, callback_data=f"wallet:{wallet.id}:toggle"),
                InlineKeyboardButton(text="Переименовать", callback_data=f"wallet:{wallet.id}:rename"),
            ],
            [
                InlineKeyboardButton(text="Мин. сумма", callback_data=f"wallet:{wallet.id}:min"),
                InlineKeyboardButton(text="Сброс мин.", callback_data=f"wallet:{wallet.id}:min_reset"),
            ],
            [InlineKeyboardButton(text="Удалить", callback_data=f"wallet:{wallet.id}:delete")],
            [InlineKeyboardButton(text="Назад", callback_data="wallets:list")],
        ]
    )
