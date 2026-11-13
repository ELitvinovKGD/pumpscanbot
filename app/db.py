from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from time import time


# Статусы пользователя.
STATUS_NEW = "new"            # написал /start, но заявку ещё не отправил
STATUS_PENDING = "pending"    # заявка ждёт решения админа
STATUS_APPROVED = "approved"  # доступ выдан
STATUS_REJECTED = "rejected"  # заявка отклонена, можно подать повторно позже
STATUS_BLOCKED = "blocked"    # доступ отозван, повторная заявка невозможна

# Через сколько секунд после отказа можно подать заявку снова.
REAPPLY_COOLDOWN_SECONDS = 24 * 3600

DEFAULT_WALLET_LIMIT_KEY = "default_wallet_limit"


@dataclass(frozen=True)
class Wallet:
    id: int
    address: str
    label: str
    enabled: bool
    min_sol: float | None
    created_at: int
    # Владелец кошелька в боте. Значения по умолчанию оставлены, чтобы старые
    # тесты и вызовы, создающие Wallet позиционно, продолжали работать.
    owner_id: int = 0
    owner_min_sol: float | None = None
    owner_chat_id: int | None = None

    @property
    def effective_label(self) -> str:
        return self.label or short_address(self.address)

    def effective_min_sol(self, global_min_sol: float) -> float:
        """Минимум кошелька → минимум владельца → общий минимум бота."""
        if self.min_sol is not None:
            return self.min_sol
        if self.owner_min_sol is not None:
            return self.owner_min_sol
        return global_min_sol


@dataclass(frozen=True)
class User:
    user_id: int
    chat_id: int | None
    username: str
    full_name: str
    status: str
    wallet_limit: int | None
    min_sol: float | None
    created_at: int
    requested_at: int | None
    decided_at: int | None

    @property
    def display_name(self) -> str:
        name = self.full_name or "без имени"
        if self.username:
            return f"{name} (@{self.username})"
        return name

    @property
    def is_approved(self) -> bool:
        return self.status == STATUS_APPROVED


def short_address(address: str) -> str:
    """Делаем адрес читаемым для Telegram-сообщений."""
    if len(address) <= 12:
        return address
    return f"{address[:4]}...{address[-4:]}"


_WALLET_SELECT = """
    SELECT w.*, u.min_sol AS owner_min_sol, u.chat_id AS owner_chat_id
    FROM wallets w
    LEFT JOIN users u ON u.user_id = w.owner_id
"""


class Database:
    """Тонкая обертка над SQLite.

    Для такого бота SQLite достаточно: один файл, без сервера, легко
    перенести или удалить. Lock нужен потому, что Telegram polling и FastAPI
    webhook работают параллельно в одном процессе.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ------------------------------------------------------------------ schema

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER,
                    username TEXT NOT NULL DEFAULT '',
                    full_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'new',
                    wallet_limit INTEGER,
                    min_sol REAL,
                    created_at INTEGER NOT NULL,
                    requested_at INTEGER,
                    decided_at INTEGER
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

            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(wallets)")}
            if not columns:
                self._create_wallets_table("wallets")
            elif "owner_id" not in columns:
                # Старая схема: address был UNIQUE на весь бот. Теперь один и тот
                # же адрес могут независимо отслеживать разные пользователи,
                # поэтому уникальность — (owner_id, address). SQLite не умеет
                # удалять UNIQUE, так что пересоздаём таблицу. owner_id = 0
                # потом заменяется на главного админа в claim_legacy_data().
                self._create_wallets_table("wallets_new")
                self._conn.execute(
                    """
                    INSERT INTO wallets_new(id, owner_id, address, label, enabled, min_sol, created_at)
                    SELECT id, 0, address, label, enabled, min_sol, created_at FROM wallets
                    """
                )
                self._conn.execute("DROP TABLE wallets")
                self._conn.execute("ALTER TABLE wallets_new RENAME TO wallets")

            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_wallets_owner ON wallets(owner_id)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_processed_created ON processed_transactions(created_at)"
            )

    def _create_wallets_table(self, name: str) -> None:
        self._conn.execute(
            f"""
            CREATE TABLE {name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                min_sol REAL,
                created_at INTEGER NOT NULL,
                UNIQUE(owner_id, address)
            )
            """
        )

    def claim_legacy_data(self, admin_id: int, legacy_global_min: float | None) -> int:
        """Переносим данные однопользовательской версии на главного админа.

        Возвращает количество кошельков, которые получили владельца.
        """
        with self._lock, self._conn:
            self._ensure_user_row(admin_id)
            self._conn.execute(
                "UPDATE users SET status = ? WHERE user_id = ?", (STATUS_APPROVED, admin_id)
            )
            cursor = self._conn.execute("UPDATE wallets SET owner_id = ? WHERE owner_id = 0", (admin_id,))
            moved = cursor.rowcount
            if legacy_global_min is not None:
                self._conn.execute(
                    "UPDATE users SET min_sol = ? WHERE user_id = ? AND min_sol IS NULL",
                    (legacy_global_min, admin_id),
                )
        return moved

    # ------------------------------------------------------------------- users

    def _ensure_user_row(self, user_id: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO users(user_id, created_at) VALUES (?, ?)",
            (user_id, int(time())),
        )

    def touch_user(self, user_id: int, chat_id: int | None, username: str, full_name: str) -> User:
        """Создаём пользователя при первом контакте и обновляем имя/чат."""
        with self._lock, self._conn:
            self._ensure_user_row(user_id)
            self._conn.execute(
                """
                UPDATE users
                SET username = ?, full_name = ?, chat_id = COALESCE(?, chat_id)
                WHERE user_id = ?
                """,
                (username or "", full_name or "", chat_id, user_id),
            )
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: int) -> User | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self, status: str | None = None) -> list[User]:
        query = "SELECT * FROM users"
        params: tuple[object, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        else:
            # Пользователи, которые только нажали /start, админу не интересны.
            query += " WHERE status != ?"
            params = (STATUS_NEW,)
        query += """
            ORDER BY CASE status
                WHEN 'pending' THEN 0 WHEN 'approved' THEN 1
                WHEN 'blocked' THEN 2 ELSE 3 END,
            COALESCE(requested_at, created_at) DESC
        """
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_user(row) for row in rows]

    def count_users(self, status: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM users WHERE status = ?", (status,)).fetchone()
        return int(row["n"])

    def set_user_status(self, user_id: int, status: str) -> None:
        now = int(time())
        with self._lock, self._conn:
            self._ensure_user_row(user_id)
            if status == STATUS_PENDING:
                self._conn.execute(
                    "UPDATE users SET status = ?, requested_at = ? WHERE user_id = ?",
                    (status, now, user_id),
                )
            else:
                self._conn.execute(
                    "UPDATE users SET status = ?, decided_at = ? WHERE user_id = ?",
                    (status, now, user_id),
                )

    def set_user_wallet_limit(self, user_id: int, limit: int | None) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE users SET wallet_limit = ? WHERE user_id = ?", (limit, user_id))

    def set_user_min_sol(self, user_id: int, min_sol: float | None) -> None:
        with self._lock, self._conn:
            self._ensure_user_row(user_id)
            self._conn.execute("UPDATE users SET min_sol = ? WHERE user_id = ?", (min_sol, user_id))

    def default_wallet_limit(self, fallback: int) -> int:
        raw = self.get_setting(DEFAULT_WALLET_LIMIT_KEY)
        try:
            return int(raw) if raw else fallback
        except ValueError:
            return fallback

    def effective_wallet_limit(self, user: User, fallback: int) -> int:
        return user.wallet_limit if user.wallet_limit is not None else self.default_wallet_limit(fallback)

    # ----------------------------------------------------------------- wallets

    def add_wallet(self, owner_id: int, address: str, label: str) -> Wallet:
        now = int(time())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO wallets(owner_id, address, label, enabled, created_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(owner_id, address) DO UPDATE SET label=excluded.label
                """,
                (owner_id, address, label, now),
            )
        wallet = self.get_owner_wallet_by_address(owner_id, address)
        assert wallet is not None
        return wallet

    def list_wallets(self, owner_id: int | None = None, enabled_only: bool = False) -> list[Wallet]:
        """Кошельки одного владельца (owner_id) или всех (None — только для админа)."""
        conditions: list[str] = []
        params: list[object] = []
        if owner_id is not None:
            conditions.append("w.owner_id = ?")
            params.append(owner_id)
        if enabled_only:
            conditions.append("w.enabled = 1")
        query = _WALLET_SELECT
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY w.id ASC"
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_wallet(row) for row in rows]

    def list_tracked_wallets(self) -> list[Wallet]:
        """Включённые кошельки одобренных пользователей — то, что реально слушаем."""
        query = _WALLET_SELECT + " WHERE w.enabled = 1 AND u.status = ? ORDER BY w.id ASC"
        with self._lock:
            rows = self._conn.execute(query, (STATUS_APPROVED,)).fetchall()
        return [self._row_to_wallet(row) for row in rows]

    def tracked_addresses(self) -> list[str]:
        """Уникальные адреса для Helius webhook."""
        return list(dict.fromkeys(wallet.address for wallet in self.list_tracked_wallets()))

    def count_wallets(self, owner_id: int) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM wallets WHERE owner_id = ?", (owner_id,)).fetchone()
        return int(row["n"])

    def get_wallet(self, wallet_id: int, owner_id: int | None = None) -> Wallet | None:
        """Кошелек по id. Если передан owner_id — только если он принадлежит владельцу.

        Это главная защита приватности: id кошелька приходит из callback_data,
        и подделать его в Telegram-клиенте несложно.
        """
        query = _WALLET_SELECT + " WHERE w.id = ?"
        params: tuple[object, ...] = (wallet_id,)
        if owner_id is not None:
            query += " AND w.owner_id = ?"
            params = (wallet_id, owner_id)
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        return self._row_to_wallet(row) if row else None

    def get_owner_wallet_by_address(self, owner_id: int, address: str) -> Wallet | None:
        with self._lock:
            row = self._conn.execute(
                _WALLET_SELECT + " WHERE w.owner_id = ? AND w.address = ?", (owner_id, address)
            ).fetchone()
        return self._row_to_wallet(row) if row else None

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

    # --------------------------------------------------------- legacy chats

    def list_notification_chats(self) -> list[int]:
        """Чаты старой однопользовательской схемы. Нужны только для миграции."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT chat_id FROM notification_chats WHERE enabled = 1 ORDER BY created_at ASC"
            ).fetchall()
        return [int(row["chat_id"]) for row in rows]

    # ---------------------------------------------------------------- settings

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def has_setting(self, key: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
        return row is not None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ----------------------------------------------------------- dedupe

    def mark_processed_once(self, signature: str, wallet_key: str, token_mint: str) -> bool:
        """Возвращает True только для новой связки transaction+wallet+token.

        Helius может повторить доставку webhook при сетевых сбоях. Эта таблица
        защищает Telegram от дублей. wallet_key — id записи кошелька, чтобы два
        пользователя с одинаковым адресом получили уведомление каждый.
        """
        key = f"{signature}:{wallet_key}:{token_mint}"
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """
                    INSERT INTO processed_transactions(key, signature, wallet_address, token_mint, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key, signature, wallet_key, token_mint, int(time())),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def prune_processed(self, older_than_seconds: int = 7 * 24 * 3600) -> int:
        """Удаляем старые отметки об обработке: Helius не повторяет доставку неделями."""
        cutoff = int(time()) - older_than_seconds
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM processed_transactions WHERE created_at < ?", (cutoff,))
        return cursor.rowcount

    # ---------------------------------------------------------------- mapping

    @staticmethod
    def _row_to_wallet(row: sqlite3.Row) -> Wallet:
        keys = row.keys()
        owner_min = row["owner_min_sol"] if "owner_min_sol" in keys else None
        owner_chat = row["owner_chat_id"] if "owner_chat_id" in keys else None
        return Wallet(
            id=int(row["id"]),
            address=str(row["address"]),
            label=str(row["label"]),
            enabled=bool(row["enabled"]),
            min_sol=float(row["min_sol"]) if row["min_sol"] is not None else None,
            created_at=int(row["created_at"]),
            owner_id=int(row["owner_id"]),
            owner_min_sol=float(owner_min) if owner_min is not None else None,
            owner_chat_id=int(owner_chat) if owner_chat is not None else None,
        )

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            user_id=int(row["user_id"]),
            chat_id=int(row["chat_id"]) if row["chat_id"] is not None else None,
            username=str(row["username"] or ""),
            full_name=str(row["full_name"] or ""),
            status=str(row["status"]),
            wallet_limit=int(row["wallet_limit"]) if row["wallet_limit"] is not None else None,
            min_sol=float(row["min_sol"]) if row["min_sol"] is not None else None,
            created_at=int(row["created_at"]),
            requested_at=int(row["requested_at"]) if row["requested_at"] is not None else None,
            decided_at=int(row["decided_at"]) if row["decided_at"] is not None else None,
        )
