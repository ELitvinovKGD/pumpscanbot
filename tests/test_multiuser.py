"""Многопользовательский режим: миграция, приватность, заявки и админка."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser

from app import access, admin, telegram_bot
from app.config import Settings
from app.db import STATUS_APPROVED, STATUS_BLOCKED, STATUS_PENDING, Database, Wallet
from app.detector import detect_buy_events
from app.main import _migrate_single_user_data


ADMIN_ID = 1001
ALICE_ID = 2002
BOB_ID = 3003
ADDR_1 = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
ADDR_2 = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def make_settings(db_path: Path, default_limit: int = 2) -> Settings:
    return Settings(
        telegram_bot_token="42:TEST",
        telegram_admin_user_ids=(ADMIN_ID,),
        helius_api_key="",
        helius_project_id="",
        helius_webhook_id="",
        public_webhook_url="",
        helius_auth_header="",
        helius_auto_sync=True,
        host="127.0.0.1",
        port=8080,
        default_min_sol=0.1,
        database_path=db_path,
        helius_usage_warn_percent=10,
        helius_usage_check_interval_minutes=0,
        default_wallet_limit=default_limit,
    )


class DatabaseMigrationTest(unittest.TestCase):
    def test_legacy_wallets_go_to_primary_admin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.sqlite3"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    min_sol REAL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            conn.execute("INSERT INTO wallets(address, label, enabled, min_sol, created_at) VALUES (?, 'Красный', 1, NULL, 1)", (ADDR_1,))
            conn.execute("INSERT INTO wallets(address, label, enabled, min_sol, created_at) VALUES (?, 'Синий', 0, 0.5, 2)", (ADDR_2,))
            conn.execute("INSERT INTO settings VALUES ('global_min_sol', '0.3')")
            conn.commit()
            conn.close()

            db = Database(path)
            _migrate_single_user_data(db, make_settings(path))

            wallets = db.list_wallets(owner_id=ADMIN_ID)
            self.assertEqual([w.label for w in wallets], ["Красный", "Синий"])
            self.assertEqual(wallets[1].min_sol, 0.5)
            admin_user = db.get_user(ADMIN_ID)
            self.assertEqual(admin_user.status, STATUS_APPROVED)
            self.assertEqual(admin_user.min_sol, 0.3)
            self.assertEqual(db.tracked_addresses(), [ADDR_1])

            # Повторный запуск ничего не ломает.
            _migrate_single_user_data(db, make_settings(path))
            self.assertEqual(len(db.list_wallets()), 2)


class PrivacyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Database(Path(":memory:"))
        for uid in (ALICE_ID, BOB_ID):
            self.db.touch_user(uid, uid, "", f"user{uid}")
            self.db.set_user_status(uid, STATUS_APPROVED)

    def test_wallet_is_invisible_to_other_users(self) -> None:
        wallet = self.db.add_wallet(ALICE_ID, ADDR_1, "secret")
        self.assertIsNone(self.db.get_wallet(wallet.id, owner_id=BOB_ID))
        self.assertEqual(self.db.list_wallets(owner_id=BOB_ID), [])
        self.assertIsNotNone(self.db.get_wallet(wallet.id, owner_id=ALICE_ID))

    def test_same_address_tracked_independently(self) -> None:
        self.db.add_wallet(ALICE_ID, ADDR_1, "Alice label")
        self.db.add_wallet(BOB_ID, ADDR_1, "Bob label")
        tracked = self.db.list_tracked_wallets()
        self.assertEqual({(w.owner_id, w.label) for w in tracked}, {(ALICE_ID, "Alice label"), (BOB_ID, "Bob label")})
        self.assertEqual(self.db.tracked_addresses(), [ADDR_1])

    def test_blocked_user_wallets_are_not_tracked(self) -> None:
        self.db.add_wallet(ALICE_ID, ADDR_1, "a")
        self.db.add_wallet(BOB_ID, ADDR_2, "b")
        self.db.set_user_status(BOB_ID, STATUS_BLOCKED)
        self.assertEqual(self.db.tracked_addresses(), [ADDR_1])

    def test_buy_event_per_owner_with_owner_minimum(self) -> None:
        self.db.set_user_min_sol(BOB_ID, 1.0)
        self.db.add_wallet(ALICE_ID, ADDR_1, "a")
        self.db.add_wallet(BOB_ID, ADDR_1, "b")
        tx = {
            "signature": "sig",
            "source": "PUMP_FUN",
            "nativeTransfers": [{"fromUserAccount": ADDR_1, "toUserAccount": "pool", "amount": 500_000_000}],
            "tokenTransfers": [{"fromUserAccount": "pool", "toUserAccount": ADDR_1, "mint": "Xpump", "tokenAmount": 10}],
        }
        events = detect_buy_events(tx, self.db.list_tracked_wallets(), global_min_sol=0.1)
        # 0.5 SOL: проходит общий минимум Alice (0.1), но не минимум Bob (1.0).
        self.assertEqual([e.wallet.owner_id for e in events], [ALICE_ID])


class FakeSession(BaseSession):
    """Сессия aiogram без сети: записывает вызовы API и возвращает заглушки."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = getattr(method, "chat_id", None) or 0
            return Message(
                message_id=len(self.calls),
                date=datetime.now(),
                chat=Chat(id=int(chat_id), type="private"),
                text=method.text,
            )
        return True

    async def close(self) -> None:  # pragma: no cover
        pass

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        yield b""

    def texts_to(self, chat_id: int) -> list[str]:
        return [c.text for c in self.calls if isinstance(c, SendMessage) and c.chat_id == chat_id]

    def all_texts(self) -> list[str]:
        result = []
        for c in self.calls:
            if isinstance(c, (SendMessage, EditMessageText)):
                result.append(c.text)
            elif isinstance(c, AnswerCallbackQuery) and c.text:
                result.append(c.text)
        return result


class FakeHelius:
    def __init__(self) -> None:
        self.syncs = 0

    async def sync_tracked(self, force: bool = False) -> str:
        self.syncs += 1
        return "synced"


class BotFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.db = Database(Path(":memory:"))
        self.settings = make_settings(Path(":memory:"), default_limit=1)
        self.session = FakeSession()
        self.bot = Bot(token="42:TEST", session=self.session)
        self.helius = FakeHelius()
        self.dp = Dispatcher(storage=MemoryStorage())
        mw = access.AccessMiddleware(self.db, self.settings)
        self.dp.message.outer_middleware(mw)
        self.dp.callback_query.outer_middleware(mw)
        self.dp.include_router(access.router)
        self.dp.include_router(admin.router)
        self.dp.include_router(telegram_bot.router)
        self.update_id = 0

    async def asyncTearDown(self) -> None:
        # Роутеры — модульные синглтоны; отвязываем их, чтобы следующий тест мог подключить заново.
        for r in (access.router, admin.router, telegram_bot.router):
            r._parent_router = None

    def _user(self, uid: int) -> TgUser:
        return TgUser(id=uid, is_bot=False, first_name=f"User{uid}", username=f"u{uid}")

    async def send(self, uid: int, text: str) -> None:
        self.update_id += 1
        msg = Message(
            message_id=self.update_id,
            date=datetime.now(),
            chat=Chat(id=uid, type="private"),
            from_user=self._user(uid),
            text=text,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.update_id, message=msg), db=self.db, settings=self.settings, helius=self.helius)

    async def press(self, uid: int, data: str) -> None:
        self.update_id += 1
        msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=uid, type="private"), text="menu")
        query = CallbackQuery(id=str(self.update_id), from_user=self._user(uid), chat_instance="x", message=msg, data=data)
        await self.dp.feed_update(self.bot, Update(update_id=self.update_id, callback_query=query), db=self.db, settings=self.settings, helius=self.helius)

    async def test_full_access_flow(self) -> None:
        # Незнакомец не может ничего, кроме /start и заявки.
        await self.send(ALICE_ID, "Кошельки")
        self.assertIn("закрытый бот", self.session.texts_to(ALICE_ID)[-1])

        await self.press(ALICE_ID, "access:request")
        self.assertEqual(self.db.get_user(ALICE_ID).status, STATUS_PENDING)
        admin_msgs = self.session.texts_to(ADMIN_ID)
        self.assertTrue(any("Новая заявка" in t for t in admin_msgs))

        # Не-админ не может одобрить сам себя.
        await self.press(ALICE_ID, f"adm:approve:{ALICE_ID}")
        self.assertEqual(self.db.get_user(ALICE_ID).status, STATUS_PENDING)

        await self.press(ADMIN_ID, f"adm:approve:{ALICE_ID}")
        self.assertEqual(self.db.get_user(ALICE_ID).status, STATUS_APPROVED)
        self.assertTrue(any("одобрен" in t for t in self.session.texts_to(ALICE_ID)))

        # Лимит по умолчанию = 1.
        await self.send(ALICE_ID, "Добавить кошелек")
        await self.send(ALICE_ID, f"{ADDR_1} Мой")
        self.assertEqual(len(self.db.list_wallets(owner_id=ALICE_ID)), 1)
        self.assertEqual(self.helius.syncs, 1)
        await self.send(ALICE_ID, "Добавить кошелек")
        self.assertIn("Достигнут лимит", self.session.texts_to(ALICE_ID)[-1])

        # Админ увеличивает лимит.
        await self.press(ADMIN_ID, f"adm:limit:{ALICE_ID}")
        await self.send(ADMIN_ID, "3")
        self.assertEqual(self.db.get_user(ALICE_ID).wallet_limit, 3)

        # Bob одобрен и пытается открыть кошелек Alice по id.
        self.db.touch_user(BOB_ID, BOB_ID, "", "Bob")
        self.db.set_user_status(BOB_ID, STATUS_APPROVED)
        alice_wallet = self.db.list_wallets(owner_id=ALICE_ID)[0]
        before = len(self.session.calls)
        await self.press(BOB_ID, f"wallet:{alice_wallet.id}")
        await self.press(BOB_ID, f"wallet:{alice_wallet.id}:delete_yes")
        new_texts = " ".join(
            (getattr(c, "text", "") or "") for c in self.session.calls[before:]
        )
        self.assertNotIn(ADDR_1, new_texts)
        self.assertIn("не найден", new_texts)
        self.assertIsNotNone(self.db.get_wallet(alice_wallet.id))

        # Bob не может открыть админку.
        await self.press(BOB_ID, f"adm:wallets:{ALICE_ID}")
        self.assertNotIn(ADDR_1, " ".join(self.session.all_texts()[-3:]))

        # Админ видит кошельки Alice.
        await self.press(ADMIN_ID, f"adm:wallets:{ALICE_ID}")
        self.assertIn(ADDR_1, self.session.all_texts()[-1])

        # Блокировка: пользователь теряет доступ, адрес уходит из отслеживания.
        await self.press(ADMIN_ID, f"adm:block:{ALICE_ID}")
        self.assertEqual(self.db.tracked_addresses(), [])
        await self.send(ALICE_ID, "Кошельки")
        self.assertIn("закрыт", self.session.texts_to(ALICE_ID)[-1])

    async def test_group_chats_are_ignored(self) -> None:
        self.update_id += 1
        msg = Message(
            message_id=1, date=datetime.now(), chat=Chat(id=-100, type="supergroup"),
            from_user=self._user(ADMIN_ID), text="/admin",
        )
        await self.dp.feed_update(self.bot, Update(update_id=1, message=msg), db=self.db, settings=self.settings, helius=self.helius)
        self.assertEqual(self.session.calls, [])


class DetectorDoubleCountTest(unittest.TestCase):
    def test_token_amount_not_doubled_when_both_sources_present(self) -> None:
        wallet = Wallet(1, ADDR_1, "a", True, None, 0)
        tx = {
            "signature": "sig",
            "source": "PUMP_FUN",
            "nativeTransfers": [{"fromUserAccount": ADDR_1, "toUserAccount": "pool", "amount": 200_000_000}],
            "tokenTransfers": [{"fromUserAccount": "pool", "toUserAccount": ADDR_1, "mint": "Xpump", "tokenAmount": 100}],
            "accountData": [
                {
                    "account": ADDR_1,
                    "nativeBalanceChange": -200_000_000,
                    "tokenBalanceChanges": [
                        {"userAccount": ADDR_1, "mint": "Xpump", "rawTokenAmount": {"tokenAmount": "100000000", "decimals": 6}}
                    ],
                }
            ],
        }
        events = detect_buy_events(tx, [wallet], 0.1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].token_amount, 100)


if __name__ == "__main__":
    unittest.main()
