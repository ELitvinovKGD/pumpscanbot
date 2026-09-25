from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from time import time


@dataclass(frozen=True)
class Wallet:
    id: int
    address: str
    label: str
    enabled: bool
    min_sol: float | None
    created_at: int

    @property
    def effective_label(self) -> str:
        return self.label or short_address(self.address)


def short_address(address: str) -> str:
    """Делаем адрес читаемым для Telegram-сообщений."""
    if len(address) <= 12:
        return address
    return f"{address[:4]}...{address[-4:]}"


class Database:
    """Тонкая обертка над SQLite.

    Для такого локального бота SQLite достаточно: один файл, без сервера, легко
    перенести или удалить. Lock нужен потому, что Telegram polling и FastAPI
    webhook работают параллельно в одном процессе.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    min_sol REAL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_transactions (
                    key TEXT PRIMARY KEY,
                    signature TEXT NOT NULL,
                    wallet_address TEXT NOT NULL,
                    token_mint TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_chats (
                    chat_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def add_wallet(self, address: str, label: str) -> Wallet:
        now = int(time())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO wallets(address, label, enabled, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(address) DO UPDATE SET label=excluded.label
                """,
                (address, label, now),
            )
        return self.get_wallet_by_address(address)

    def list_wallets(self, enabled_only: bool = False) -> list[Wallet]:
        query = "SELECT * FROM wallets"
        params: tuple[object, ...] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_wallet(row) for row in rows]

    def get_wallet(self, wallet_id: int) -> Wallet | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM wallets WHERE id = ?", (wallet_id,)).fetchone()
        return self._row_to_wallet(row) if row else None

    def get_wallet_by_address(self, address: str) -> Wallet:
        with self._lock:
            row = self._conn.execute("SELECT * FROM wallets WHERE address = ?", (address,)).fetchone()
        if row is None:
            raise KeyError(address)
        return self._row_to_wallet(row)

    def set_wallet_enabled(self, wallet_id: int, enabled: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE wallets SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, wallet_id),
            )

    def set_wallet_label(self, wallet_id: int, label: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE wallets SET label = ? WHERE id = ?", (label, wallet_id))

    def set_wallet_min_sol(self, wallet_id: int, min_sol: float | None) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE wallets SET min_sol = ? WHERE id = ?", (min_sol, wallet_id))

    def delete_wallet(self, wallet_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM wallets WHERE id = ?", (wallet_id,))

    def add_notification_chat(self, chat_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO notification_chats(chat_id, enabled, created_at)
                VALUES (?, 1, ?)
                ON CONFLICT(chat_id) DO UPDATE SET enabled = 1
                """,
                (chat_id, int(time())),
            )

    def list_notification_chats(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT chat_id FROM notification_chats WHERE enabled = 1 ORDER BY created_at ASC"
            ).fetchall()
        return [int(row["chat_id"]) for row in rows]

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def mark_processed_once(self, signature: str, wallet_address: str, token_mint: str) -> bool:
        """Возвращает True только для новой связки transaction+wallet+token.

        Helius может повторить доставку webhook при сетевых сбоях. Эта таблица
        защищает Telegram от дублей.
        """
        key = f"{signature}:{wallet_address}:{token_mint}"
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO processed_transactions(key, signature, wallet_address, token_mint, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key, signature, wallet_address, token_mint, int(time())),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    @staticmethod
    def _row_to_wallet(row: sqlite3.Row) -> Wallet:
        return Wallet(
            id=int(row["id"]),
            address=str(row["address"]),
            label=str(row["label"]),
            enabled=bool(row["enabled"]),
            min_sol=float(row["min_sol"]) if row["min_sol"] is not None else None,
            created_at=int(row["created_at"]),
        )
