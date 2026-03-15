"""코인 WebSocket 구독 상태 관리자."""
from __future__ import annotations

from loguru import logger

from trading.bithumb_websocket import bithumb_websocket
from trading.market_profile import MARKET_SCOPE_CRYPTO, is_crypto_market, normalize_market, normalize_market_scope


class CoinStreamManager:
    """코인 감시 종목 desired/active 상태를 관리한다."""

    def __init__(self) -> None:
        self._desired_by_scope: dict[str, list[tuple[str, str]]] = {}
        self._running = False

    async def start(self) -> None:
        if self._running:
            logger.debug("코인 스트림 매니저 이미 시작됨")
            return
        self._running = True
        await bithumb_websocket.start()
        await self._sync_public_symbols()
        logger.info("코인 스트림 매니저 시작")

    async def stop(self) -> None:
        self._running = False
        await bithumb_websocket.stop()
        logger.info("코인 스트림 매니저 중지")

    def desired_keys(self, scope: str | None = None) -> set[tuple[str, str]]:
        if scope is None:
            return {
                (market_code, symbol.upper())
                for symbols in self._desired_by_scope.values()
                for symbol, market_code in symbols
            }
        normalized_scope = normalize_market_scope(scope)
        return {
            (market_code, symbol.upper())
            for symbol, market_code in self._desired_by_scope.get(normalized_scope, [])
        }

    def active_keys(self, scope: str | None = None) -> set[tuple[str, str]]:
        if scope is not None and normalize_market_scope(scope) != MARKET_SCOPE_CRYPTO:
            return set()
        return bithumb_websocket.active_keys()

    async def replace_market_subscriptions(self, market: str, symbols: list[tuple[str, str]]) -> None:
        market_code = normalize_market(market)
        if not is_crypto_market(market_code):
            return
        normalized_scope = normalize_market_scope(market_code)
        ordered: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for symbol, item_market in symbols:
            symbol_text = str(symbol or "").upper().strip()
            item_market_code = normalize_market(item_market or market_code)
            key = (item_market_code, symbol_text)
            if not symbol_text or not is_crypto_market(item_market_code) or key in seen:
                continue
            seen.add(key)
            ordered.append((symbol_text, item_market_code))
        self._desired_by_scope[normalized_scope] = ordered
        await self._sync_public_symbols()

    async def ensure_symbol(self, scope: str, symbol: str, market: str) -> None:
        market_code = normalize_market(market)
        if not is_crypto_market(market_code):
            return
        normalized_scope = normalize_market_scope(scope)
        ordered = self._desired_by_scope.setdefault(normalized_scope, [])
        candidate = (str(symbol or "").upper().strip(), market_code)
        if not candidate[0]:
            return
        if candidate not in ordered:
            ordered.append(candidate)
            await self._sync_public_symbols()

    def stream_status(self, scope: str | None = None) -> dict:
        public_status = bithumb_websocket.public_status()
        desired_count = len(self.desired_keys(scope))
        active_count = len(self.active_keys(scope))
        return {
            **public_status,
            "running": self._running,
            "desired_count": desired_count,
            "active_count": active_count,
            "subscription_count": active_count,
            "subscription_limit": desired_count,
        }

    def private_sync_status(self) -> dict:
        return {
            **bithumb_websocket.private_status(),
            "running": self._running,
        }

    async def _sync_public_symbols(self) -> None:
        ordered: list[str] = []
        seen: set[str] = set()
        for symbols in self._desired_by_scope.values():
            for symbol, market_code in symbols:
                if not is_crypto_market(market_code):
                    continue
                symbol_text = str(symbol or "").upper().strip()
                if not symbol_text or symbol_text in seen:
                    continue
                seen.add(symbol_text)
                ordered.append(symbol_text)
        await bithumb_websocket.set_public_symbols(ordered)


coin_stream_manager = CoinStreamManager()
