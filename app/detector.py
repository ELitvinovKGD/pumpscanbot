from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db import Wallet


LAMPORTS_PER_SOL = 1_000_000_000

# Pump bonding curve и PumpSwap program ids. Если Pump изменит программы, список
# можно расширить без переписывания остальной логики.
PUMP_PROGRAM_IDS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
}


@dataclass(frozen=True)
class BuyEvent:
    wallet: Wallet
    signature: str
    token_mint: str
    token_amount: float
    sol_spent: float
    source: str
    timestamp: int | None


@dataclass(frozen=True)
class SellEvent:
    wallet: Wallet
    signature: str
    token_mint: str
    token_amount: float
    sol_received: float
    source: str
    timestamp: int | None


@dataclass(frozen=True)
class ActivityEvent:
    """Любая подтвержденная активность отслеживаемого кошелька."""

    wallet: Wallet
    signature: str
    event_type: str
    source: str
    description: str
    sol_change: float
    token_changes: tuple[tuple[str, float], ...]
    timestamp: int | None


def detect_activity_events(tx: dict[str, Any], wallets: list[Wallet]) -> list[ActivityEvent]:
    """Создаем одно итоговое событие на кошелек независимо от DEX/агрегатора.

    Мы смотрим на чистое изменение балансов кошелька. Поэтому промежуточные
    шаги маршрута Jupiter/Raydium/Orca не превращаются в отдельные оповещения.
    """
    signature = str(tx.get("signature") or "")
    if not signature or tx.get("transactionError"):
        return []

    native_transfers = tx.get("nativeTransfers") or []
    token_transfers = tx.get("tokenTransfers") or []
    account_data = tx.get("accountData") or []
    timestamp = tx.get("timestamp")
    events: list[ActivityEvent] = []

    for wallet in wallets:
        if not wallet.enabled or not _transaction_mentions_wallet(tx, wallet.address):
            continue

        sol_change = _net_sol_change(wallet.address, native_transfers, account_data)
        token_changes = _net_token_changes(wallet.address, token_transfers, account_data)
        events.append(
            ActivityEvent(
                wallet=wallet,
                signature=signature,
                event_type=str(tx.get("type") or "UNKNOWN"),
                source=str(tx.get("source") or "UNKNOWN"),
                description=str(tx.get("description") or ""),
                sol_change=sol_change,
                token_changes=tuple(sorted(token_changes.items())),
                timestamp=int(timestamp) if timestamp else None,
            )
        )

    return events


def detect_buy_events(
    tx: dict[str, Any],
    wallets: list[Wallet],
    global_min_sol: float,
) -> list[BuyEvent]:
    """Ищем покупки pump-токенов в одной Helius enhanced transaction.

    Логика намеренно простая и прозрачная:
    1. транзакция должна быть успешной;
    2. транзакция должна выглядеть как Pump/PumpSwap;
    3. отслеживаемый кошелек должен потратить SOL;
    4. тот же кошелек должен получить SPL-токен;
    5. сумма SOL должна пройти фильтр.

    Это не торговый движок, а alerting. Лучше пропустить сомнительную покупку,
    чем завалить Telegram мусорными уведомлениями.
    """
    signature = str(tx.get("signature") or "")
    if not signature:
        return []

    if tx.get("transactionError"):
        return []

    if not _looks_like_pump_transaction(tx):
        return []

    active_wallets = [wallet for wallet in wallets if wallet.enabled]
    if not active_wallets:
        return []

    native_transfers = tx.get("nativeTransfers") or []
    token_transfers = tx.get("tokenTransfers") or []
    account_data = tx.get("accountData") or []
    timestamp = tx.get("timestamp")
    source = str(tx.get("source") or tx.get("type") or "UNKNOWN")

    events: list[BuyEvent] = []
    for wallet in active_wallets:
        sol_spent = _sum_sol_spent(wallet.address, native_transfers, account_data)
        min_sol = wallet.min_sol if wallet.min_sol is not None else global_min_sol
        if sol_spent < min_sol:
            continue

        for token_mint, token_amount in _received_tokens(wallet.address, token_transfers, account_data).items():
            if not token_mint or token_amount <= 0:
                continue

            events.append(
                BuyEvent(
                    wallet=wallet,
                    signature=signature,
                    token_mint=token_mint,
                    token_amount=token_amount,
                    sol_spent=sol_spent,
                    source=source,
                    timestamp=int(timestamp) if timestamp else None,
                )
            )

    return events


def detect_sell_events(
    tx: dict[str, Any],
    wallets: list[Wallet],
    global_min_sol: float,
) -> list[SellEvent]:
    """Ищем продажи pump-токенов.

    Для твоего сценария достаточно простого сигнала:
    "кошелек Красный продал токен". Поэтому логика зеркальна покупке:
    кошелек получает SOL и отдает SPL-токен в Pump/PumpSwap-транзакции.

    Фильтр по сумме применяется к полученному SOL. То есть если глобальный
    минимум 0.1 SOL, продажа на 0.05 SOL не будет шуметь в Telegram.
    """
    signature = str(tx.get("signature") or "")
    if not signature:
        return []

    if tx.get("transactionError"):
        return []

    if not _looks_like_pump_transaction(tx):
        return []

    active_wallets = [wallet for wallet in wallets if wallet.enabled]
    if not active_wallets:
        return []

    native_transfers = tx.get("nativeTransfers") or []
    token_transfers = tx.get("tokenTransfers") or []
    account_data = tx.get("accountData") or []
    timestamp = tx.get("timestamp")
    source = str(tx.get("source") or tx.get("type") or "UNKNOWN")

    events: list[SellEvent] = []
    for wallet in active_wallets:
        sol_received = _sum_sol_received(wallet.address, native_transfers, account_data)
        min_sol = wallet.min_sol if wallet.min_sol is not None else global_min_sol
        if sol_received < min_sol:
            continue

        for token_mint, token_amount in _sent_tokens(wallet.address, token_transfers, account_data).items():
            if not token_mint or token_amount <= 0:
                continue

            events.append(
                SellEvent(
                    wallet=wallet,
                    signature=signature,
                    token_mint=token_mint,
                    token_amount=token_amount,
                    sol_received=sol_received,
                    source=source,
                    timestamp=int(timestamp) if timestamp else None,
                )
            )

    return events


def _sum_sol_spent(
    wallet_address: str,
    native_transfers: list[dict[str, Any]],
    account_data: list[dict[str, Any]],
) -> float:
    """Суммируем исходящие native SOL переводы кошелька.

    Сначала смотрим nativeTransfers. Если Helius не разложил swap на понятный
    SOL transfer, используем accountData.nativeBalanceChange как fallback.
    """
    lamports = 0
    for transfer in native_transfers:
        if str(transfer.get("fromUserAccount") or "") == wallet_address:
            lamports += int(transfer.get("amount") or 0)
    if lamports > 0:
        return lamports / LAMPORTS_PER_SOL

    native_change = _native_balance_change(wallet_address, account_data)
    if native_change < 0:
        return abs(native_change) / LAMPORTS_PER_SOL
    return 0.0


def _sum_sol_received(
    wallet_address: str,
    native_transfers: list[dict[str, Any]],
    account_data: list[dict[str, Any]],
) -> float:
    """Суммируем входящие SOL переводы на кошелек."""
    lamports = 0
    for transfer in native_transfers:
        if str(transfer.get("toUserAccount") or "") == wallet_address:
            lamports += int(transfer.get("amount") or 0)
    if lamports > 0:
        return lamports / LAMPORTS_PER_SOL

    native_change = _native_balance_change(wallet_address, account_data)
    if native_change > 0:
        return native_change / LAMPORTS_PER_SOL
    return 0.0


def _net_sol_change(
    wallet_address: str,
    native_transfers: list[dict[str, Any]],
    account_data: list[dict[str, Any]],
) -> float:
    """Чистое изменение SOL; для исходящих действий включает network fee."""
    native_change = _native_balance_change(wallet_address, account_data)
    if native_change:
        return native_change / LAMPORTS_PER_SOL

    received = 0
    sent = 0
    for transfer in native_transfers:
        amount = int(transfer.get("amount") or 0)
        if str(transfer.get("toUserAccount") or "") == wallet_address:
            received += amount
        if str(transfer.get("fromUserAccount") or "") == wallet_address:
            sent += amount
    return (received - sent) / LAMPORTS_PER_SOL


def _received_tokens(
    wallet_address: str,
    token_transfers: list[dict[str, Any]],
    account_data: list[dict[str, Any]],
) -> dict[str, float]:
    """Токены, которые кошелек получил в транзакции."""
    tokens: dict[str, float] = {}

    for transfer in token_transfers:
        if str(transfer.get("toUserAccount") or "") != wallet_address:
            continue
        if str(transfer.get("fromUserAccount") or "") == wallet_address:
            continue
        _add_token_amount(tokens, str(transfer.get("mint") or ""), _safe_float(transfer.get("tokenAmount")))

    for change in _token_balance_changes(wallet_address, account_data):
        amount = _token_change_amount(change)
        if amount > 0:
            _add_token_amount(tokens, str(change.get("mint") or ""), amount)

    return tokens


def _sent_tokens(
    wallet_address: str,
    token_transfers: list[dict[str, Any]],
    account_data: list[dict[str, Any]],
) -> dict[str, float]:
    """Токены, которые кошелек отправил/продал в транзакции."""
    tokens: dict[str, float] = {}

    for transfer in token_transfers:
        if str(transfer.get("fromUserAccount") or "") != wallet_address:
            continue
        if str(transfer.get("toUserAccount") or "") == wallet_address:
            continue
        _add_token_amount(tokens, str(transfer.get("mint") or ""), _safe_float(transfer.get("tokenAmount")))

    for change in _token_balance_changes(wallet_address, account_data):
        amount = _token_change_amount(change)
        if amount < 0:
            _add_token_amount(tokens, str(change.get("mint") or ""), abs(amount))

    return tokens


def _net_token_changes(
    wallet_address: str,
    token_transfers: list[dict[str, Any]],
    account_data: list[dict[str, Any]],
) -> dict[str, float]:
    """Итоговые изменения SPL-токенов, без промежуточных шагов маршрута."""
    result: dict[str, float] = {}
    balance_changes = _token_balance_changes(wallet_address, account_data)
    if balance_changes:
        for change in balance_changes:
            mint = str(change.get("mint") or "")
            amount = _token_change_amount(change)
            if mint and amount:
                result[mint] = result.get(mint, 0.0) + amount
        return {mint: amount for mint, amount in result.items() if abs(amount) > 1e-12}

    for transfer in token_transfers:
        mint = str(transfer.get("mint") or "")
        amount = _safe_float(transfer.get("tokenAmount"))
        if not mint or not amount:
            continue
        if str(transfer.get("toUserAccount") or "") == wallet_address:
            result[mint] = result.get(mint, 0.0) + amount
        if str(transfer.get("fromUserAccount") or "") == wallet_address:
            result[mint] = result.get(mint, 0.0) - amount
    return {mint: amount for mint, amount in result.items() if abs(amount) > 1e-12}


def _native_balance_change(wallet_address: str, account_data: list[dict[str, Any]]) -> int:
    for account in account_data:
        if str(account.get("account") or "") == wallet_address:
            return int(account.get("nativeBalanceChange") or 0)
    return 0


def _token_balance_changes(wallet_address: str, account_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for account in account_data:
        for change in account.get("tokenBalanceChanges") or []:
            if not isinstance(change, dict):
                continue
            owners = {
                str(change.get("userAccount") or ""),
                str(change.get("owner") or ""),
                str(account.get("account") or ""),
            }
            if wallet_address in owners:
                changes.append(change)
    return changes


def _token_change_amount(change: dict[str, Any]) -> float:
    raw = change.get("rawTokenAmount")
    if isinstance(raw, dict):
        amount = _safe_float(raw.get("tokenAmount"))
        decimals = int(raw.get("decimals") or 0)
        return amount / (10**decimals)
    return _safe_float(change.get("tokenAmount") or change.get("amount"))


def _add_token_amount(tokens: dict[str, float], mint: str, amount: float) -> None:
    if not mint or amount <= 0:
        return
    tokens[mint] = tokens.get(mint, 0.0) + amount


def _looks_like_pump_transaction(tx: dict[str, Any]) -> bool:
    """Проверяем, участвовала ли в транзакции одна из программ Pump.

    Enhanced webhook может прислать разные формы данных, поэтому проверка идет
    по нескольким местам: source/type и вложенным инструкциям.
    """
    source_text = f"{tx.get('source', '')} {tx.get('type', '')}".upper()
    if "PUMP" in source_text:
        return True

    if any(mint.lower().endswith("pump") for mint in _iter_token_mints(tx)):
        return True

    for instruction in _iter_instructions(tx):
        program_id = str(instruction.get("programId") or instruction.get("programIdIndex") or "")
        if program_id in PUMP_PROGRAM_IDS:
            return True

        accounts = instruction.get("accounts") or []
        if any(str(account) in PUMP_PROGRAM_IDS for account in accounts):
            return True

    return False


def summarize_for_debug(tx: dict[str, Any], wallets: list[Wallet]) -> str:
    """Короткое описание транзакции для режима диагностики.

    Его задача не красиво рассказать о сделке, а показать, дошел ли webhook и
    какие поля Helius прислал. Это помогает быстро понять, почему уведомление
    не распозналось как покупка/продажа.
    """
    native_transfers = tx.get("nativeTransfers") or []
    token_transfers = tx.get("tokenTransfers") or []
    account_data = tx.get("accountData") or []
    touched_wallets = []

    for wallet in wallets:
        if not _transaction_mentions_wallet(tx, wallet.address):
            continue
        spent = _sum_sol_spent(wallet.address, native_transfers, account_data)
        received = _sum_sol_received(wallet.address, native_transfers, account_data)
        got_tokens = len(_received_tokens(wallet.address, token_transfers, account_data))
        sent_tokens = len(_sent_tokens(wallet.address, token_transfers, account_data))
        touched_wallets.append(
            f"{wallet.effective_label}: -{spent:.4f} SOL, +{received:.4f} SOL, "
            f"tokens in/out {got_tokens}/{sent_tokens}"
        )

    touched_text = "\n".join(touched_wallets) if touched_wallets else "отслеживаемых кошельков в payload не видно"
    return (
        "🧪 <b>Debug webhook</b>\n\n"
        f"Signature: <code>{str(tx.get('signature') or '?')}</code>\n"
        f"Source/type: <code>{str(tx.get('source') or '?')} / {str(tx.get('type') or '?')}</code>\n"
        f"Pump-like: <b>{'yes' if _looks_like_pump_transaction(tx) else 'no'}</b>\n"
        f"nativeTransfers: <b>{len(native_transfers)}</b>\n"
        f"tokenTransfers: <b>{len(token_transfers)}</b>\n"
        f"accountData: <b>{len(account_data)}</b>\n\n"
        f"{touched_text}"
    )


def transaction_touches_wallets(tx: dict[str, Any], wallets: list[Wallet]) -> bool:
    return any(_transaction_mentions_wallet(tx, wallet.address) for wallet in wallets)


def _transaction_mentions_wallet(tx: dict[str, Any], wallet_address: str) -> bool:
    if wallet_address in str(tx):
        return True
    return False


def _iter_token_mints(tx: dict[str, Any]) -> list[str]:
    mints: list[str] = []

    for transfer in tx.get("tokenTransfers") or []:
        if isinstance(transfer, dict):
            mint = str(transfer.get("mint") or "")
            if mint:
                mints.append(mint)

    for account in tx.get("accountData") or []:
        if not isinstance(account, dict):
            continue
        for change in account.get("tokenBalanceChanges") or []:
            if isinstance(change, dict):
                mint = str(change.get("mint") or "")
                if mint:
                    mints.append(mint)

    return mints


def _iter_instructions(tx: dict[str, Any]) -> list[dict[str, Any]]:
    """Достаем инструкции из Helius payload, включая вложенные inner instructions."""
    result: list[dict[str, Any]] = []
    stack = list(tx.get("instructions") or [])
    while stack:
        instruction = stack.pop()
        if not isinstance(instruction, dict):
            continue
        result.append(instruction)
        inner = instruction.get("innerInstructions") or []
        stack.extend(item for item in inner if isinstance(item, dict))
    return result


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
