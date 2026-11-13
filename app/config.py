from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _split_ints(raw_value: str) -> tuple[int, ...]:
    """Парсим строку вида '123,456' в кортеж id с сохранением порядка.

    Первый id в списке считается главным админом: ему при миграции достаются
    кошельки, которые были добавлены до появления многопользовательского режима.
    """
    result: list[int] = []
    for part in raw_value.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value not in result:
            result.append(value)
    return tuple(result)


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    """Все настройки приложения в одном месте.

    Значения читаются из .env, чтобы ключи не лежали в коде и случайно не попали
    в git. Файл .env.example показывает, какие поля нужно заполнить.
    """

    telegram_bot_token: str
    telegram_admin_user_ids: tuple[int, ...]
    helius_api_key: str
    helius_project_id: str
    helius_webhook_id: str
    public_webhook_url: str
    helius_auth_header: str
    helius_auto_sync: bool
    host: str
    port: int
    default_min_sol: float
    database_path: Path
    helius_usage_warn_percent: int
    helius_usage_check_interval_minutes: int
    default_wallet_limit: int = 5

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()

        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_admin_user_ids=_split_ints(os.getenv("TELEGRAM_ADMIN_USER_IDS", "")),
            helius_api_key=os.getenv("HELIUS_API_KEY", "").strip(),
            helius_project_id=os.getenv("HELIUS_PROJECT_ID", "").strip(),
            helius_webhook_id=os.getenv("HELIUS_WEBHOOK_ID", "").strip(),
            public_webhook_url=os.getenv("PUBLIC_WEBHOOK_URL", "").strip().rstrip("/"),
            helius_auth_header=os.getenv("HELIUS_AUTH_HEADER", "").strip(),
            helius_auto_sync=_get_bool("HELIUS_AUTO_SYNC", True),
            host=os.getenv("HOST", "127.0.0.1").strip(),
            port=int(os.getenv("PORT", "8080")),
            default_min_sol=float(os.getenv("DEFAULT_MIN_SOL", "0.1")),
            database_path=Path(os.getenv("DATABASE_PATH", "data/pumpscanbot.sqlite3")),
            helius_usage_warn_percent=int(os.getenv("HELIUS_USAGE_WARN_PERCENT", "10")),
            helius_usage_check_interval_minutes=int(os.getenv("HELIUS_USAGE_CHECK_INTERVAL_MINUTES", "360")),
            default_wallet_limit=int(os.getenv("DEFAULT_WALLET_LIMIT", "5")),
        )

    @property
    def primary_admin_id(self) -> int | None:
        return self.telegram_admin_user_ids[0] if self.telegram_admin_user_ids else None

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.telegram_admin_user_ids

    def validate_for_startup(self) -> None:
        """Проверяем только обязательные поля.

        Helius поля могут быть пустыми во время первого локального запуска, но
        без Telegram token бот не сможет стартовать вообще.
        """
        if not self.telegram_bot_token or "PASTE_" in self.telegram_bot_token:
            raise RuntimeError("Заполни TELEGRAM_BOT_TOKEN в .env")
        if not self.telegram_admin_user_ids:
            # Без админа некому одобрять заявки, а раньше пустой список означал
            # «бот открыт всем». Явно запрещаем такой запуск.
            raise RuntimeError(
                "Заполни TELEGRAM_ADMIN_USER_IDS в .env: свой Telegram user id "
                "(узнать можно у @userinfobot). Без админа некому одобрять заявки."
            )
