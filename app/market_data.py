from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

import httpx


USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KConKYX8r7XwN6VJZw7"
WSOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class TokenQuote:
    symbol: str
    price_usd: float | None


class DexScreenerClient:
    """Получаем символы и цены токенов одним бесплатным batch-запросом."""

    URL = "https://api.dexscreener.com/tokens/v1/solana/{mints}"
    CACHE_SECONDS = 3600
    MISSING_CACHE_SECONDS = 600
    KNOWN = {
        USDC_MINT: TokenQuote("USDC", 1.0),
        USDT_MINT: TokenQuote("USDT", 1.0),
    }
    SYMBOL_FALLBACKS = {WSOL_MINT: "SOL"}

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, TokenQuote]] = {}

    async def get_quotes(self, mints: list[str]) -> dict[str, TokenQuote]:
        now = monotonic()
        result = {mint: self.KNOWN[mint] for mint in mints if mint in self.KNOWN}
        missing: list[str] = []

        for mint in dict.fromkeys(mints):
            if mint in result:
                continue
            cached = self._cache.get(mint)
            if cached and cached[0] > now:
                result[mint] = cached[1]
            else:
                missing.append(mint)

        if missing:
            fetched = await self._fetch(missing[:30])
            for mint in missing:
                quote = fetched.get(mint) or TokenQuote(self.SYMBOL_FALLBACKS.get(mint, ""), None)
                ttl = self.CACHE_SECONDS if mint in fetched else self.MISSING_CACHE_SECONDS
                self._cache[mint] = (now + ttl, quote)
                result[mint] = quote

        return result

    async def _fetch(self, mints: list[str]) -> dict[str, TokenQuote]:
        """Выбираем для каждого mint пару с наибольшей USD-ликвидностью."""
        try:
            async with httpx.AsyncClient(timeout=4, follow_redirects=True) as client:
                response = await client.get(self.URL.format(mints=",".join(mints)))
            response.raise_for_status()
            pairs = response.json()
        except Exception:
            return {}

        best: dict[str, tuple[float, TokenQuote]] = {}
        for pair in pairs if isinstance(pairs, list) else []:
            if not isinstance(pair, dict):
                continue
            base = pair.get("baseToken") or {}
            mint = str(base.get("address") or "")
            if mint not in mints:
                continue
            liquidity = pair.get("liquidity") or {}
            liquidity_usd = _safe_float(liquidity.get("usd")) or 0.0
            quote = TokenQuote(
                symbol=str(base.get("symbol") or self.SYMBOL_FALLBACKS.get(mint, "")),
                price_usd=_safe_float(pair.get("priceUsd")),
            )
            if mint not in best or liquidity_usd > best[mint][0]:
                best[mint] = (liquidity_usd, quote)

        return {mint: item[1] for mint, item in best.items()}


def _safe_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
