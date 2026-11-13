from __future__ import annotations

import hashlib
from time import time
from typing import Any

import httpx

from app.config import Settings
from app.db import Database


class HeliusClient:
    """Мини-клиент Helius API.

    Здесь только то, что нужно боту: синхронизировать webhook и показать usage.
    Если позже захочется добавить backfill пропущенных транзакций, логично
    расширять именно этот класс.
    """

    WEBHOOKS_BASE_URL = "https://api-mainnet.helius-rpc.com/v0/webhooks"
    ADMIN_BASE_URL = "https://admin-api.helius.xyz/v0/admin"
    LAST_SYNCED_URL_KEY = "helius_synced_webhook_url"
    LAST_SYNCED_AT_KEY = "helius_last_sync_at"
    SYNCED_ADDRESSES_KEY = "helius_synced_addresses_fp"

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def configured_for_webhooks(self) -> bool:
        return bool(
            self.settings.helius_api_key
            and "PASTE_" not in self.settings.helius_api_key
            and self.settings.public_webhook_url
            and "PASTE_" not in self.settings.public_webhook_url
        )

    async def sync_webhook(self, addresses: list[str]) -> str:
        """Создаем или обновляем Helius webhook под текущий список кошельков.

        Helius принимает публичный HTTPS URL и список адресов. Когда список
        кошельков меняется, мы отправляем update, чтобы не держать лишние адреса.
        """
        if not self.settings.helius_auto_sync:
            return "Автосинхронизация Helius выключена."

        if not self.configured_for_webhooks():
            return "Helius webhook не синхронизирован: заполни HELIUS_API_KEY и PUBLIC_WEBHOOK_URL в .env."

        if not addresses:
            return "Нет включенных кошельков, webhook не обновлялся."

        webhook_id = self.settings.helius_webhook_id or self.db.get_setting("helius_webhook_id")
        body: dict[str, Any] = {
            "webhookURL": f"{self.settings.public_webhook_url}/helius/webhook",
            # ANY совместим и со старыми webhook, созданными через прежний API.
            "transactionTypes": ["ANY"],
            "accountAddresses": addresses,
            "webhookType": "enhanced",
            "txnStatus": "success",
        }
        if self.settings.helius_auth_header and "PASTE_" not in self.settings.helius_auth_header:
            body["authHeader"] = self.settings.helius_auth_header

        async with httpx.AsyncClient(timeout=20) as client:
            if webhook_id:
                response = await client.put(
                    f"{self.WEBHOOKS_BASE_URL}/{webhook_id}",
                    params={"api-key": self.settings.helius_api_key},
                    json=body,
                )
                action = "обновлен"
            else:
                response = await client.post(
                    self.WEBHOOKS_BASE_URL,
                    params={"api-key": self.settings.helius_api_key},
                    json=body,
                )
                action = "создан"

        if response.is_error:
            detail = response.text.strip()[:500]
            raise RuntimeError(f"Helius HTTP {response.status_code}: {detail or response.reason_phrase}")
        data = response.json()
        new_webhook_id = str(data.get("webhookID") or webhook_id or "")
        if new_webhook_id:
            self.db.set_setting("helius_webhook_id", new_webhook_id)

        reactivated = False
        if new_webhook_id and data.get("active") is False:
            async with httpx.AsyncClient(timeout=20) as client:
                toggle_response = await client.patch(
                    f"{self.WEBHOOKS_BASE_URL}/{new_webhook_id}",
                    params={"api-key": self.settings.helius_api_key},
                    json={"active": True},
                )
            if toggle_response.is_error:
                detail = toggle_response.text.strip()[:500]
                raise RuntimeError(
                    f"Helius activation HTTP {toggle_response.status_code}: "
                    f"{detail or toggle_response.reason_phrase}"
                )
            reactivated = True

        self.db.set_setting("helius_webhook_active", "1")
        self.db.set_setting(self.LAST_SYNCED_URL_KEY, self.webhook_url)
        self.db.set_setting(self.LAST_SYNCED_AT_KEY, str(int(time())))
        self.db.set_setting(self.SYNCED_ADDRESSES_KEY, _addresses_fingerprint(addresses))

        suffix = " Webhook был выключен и теперь снова включен." if reactivated else ""
        return f"Helius webhook {action}: {len(addresses)} адрес(ов).{suffix}"

    async def sync_tracked(self, force: bool = False) -> str:
        """Синхронизируем webhook со всеми отслеживаемыми адресами всех пользователей.

        Если набор адресов не изменился (например, второй пользователь добавил
        уже отслеживаемый адрес или переименовал кошелек), платный запрос к
        Helius не делаем.
        """
        addresses = self.db.tracked_addresses()
        if not force and self.db.get_setting(self.SYNCED_ADDRESSES_KEY) == _addresses_fingerprint(addresses):
            return "Список адресов для Helius не изменился; sync не нужен."
        return await self.sync_webhook(addresses)

    @property
    def webhook_url(self) -> str:
        """Полный адрес, на который Helius должен присылать события."""
        if not self.settings.public_webhook_url:
            return ""
        return f"{self.settings.public_webhook_url}/helius/webhook"

    async def sync_webhook_if_url_changed(self, addresses: list[str]) -> str:
        """Синхронизируем Helius при старте только после смены ngrok URL.

        Успешный URL хранится в SQLite. Поэтому перезапуск бота с той же
        ссылкой не делает лишний PUT-запрос и не расходует Helius credits.
        """
        if not self.configured_for_webhooks():
            return "Автопроверка Helius пропущена: API key или публичный URL не заполнен."

        previous_url = self.db.get_setting(self.LAST_SYNCED_URL_KEY)
        if previous_url == self.webhook_url:
            return "Helius уже использует текущую ngrok-ссылку; sync не требуется."

        return await self.sync_webhook(addresses)

    async def get_project_usage(self) -> dict[str, Any]:
        """Возвращает usage/credits по проекту Helius.

        Нужен HELIUS_PROJECT_ID. Если Helius поменяет ответ, команда /limit
        все равно покажет сырой смысловой минимум или понятную ошибку.
        """
        if not self.settings.helius_api_key or "PASTE_" in self.settings.helius_api_key:
            raise RuntimeError("Заполни HELIUS_API_KEY в .env")
        if not self.settings.helius_project_id or "PASTE_" in self.settings.helius_project_id:
            raise RuntimeError("Заполни HELIUS_PROJECT_ID в .env")

        url = f"{self.ADMIN_BASE_URL}/projects/{self.settings.helius_project_id}/usage"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers={"X-Api-Key": self.settings.helius_api_key})
        response.raise_for_status()
        return response.json()


def _addresses_fingerprint(addresses: list[str]) -> str:
    return hashlib.sha256(",".join(sorted(set(addresses))).encode()).hexdigest()
