"""WebSocket 연결 관리 - 동적 구독/해제, 재연결"""
import asyncio
from datetime import datetime

from loguru import logger

from trading.kis_websocket import kis_websocket
from trading.market_profile import normalize_market, normalize_market_scope
from util.time_util import now_kst


class StreamManager:
    """
    WebSocket 스트림 관리자
    - KIS 제한: 세션당 41종목
    - AI 선정 종목만 동적 구독/해제
    - 끊김 시 자동 재연결
    """

    def __init__(self):
        self._active_symbols: dict[tuple[str, str], tuple[str, str]] = {}
        self._desired_by_scope: dict[str, set[tuple[str, str]]] = {}
        self._desired_order_by_scope: dict[str, list[tuple[str, str]]] = {}
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._reconnect_event: asyncio.Event = asyncio.Event()
        self._last_connect_error: str | None = None
        self._last_connect_at: datetime | None = None
        self._last_message_at: datetime | None = None

    async def start(self) -> None:
        """스트림 관리 시작"""
        if self._running:
            logger.debug("스트림 매니저 이미 시작됨 — 중복 시작 스킵")
            return
        self._running = True
        self._listen_task = asyncio.create_task(self._run_listener())
        self._supervisor_task = asyncio.create_task(self._run_supervisor())
        await self._ensure_connected()
        logger.info("스트림 매니저 시작")

    async def stop(self) -> None:
        """스트림 관리 중지"""
        self._running = False
        self._reconnect_event.set()
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
        await kis_websocket.disconnect()
        self._active_symbols.clear()
        logger.info("스트림 매니저 중지")

    @staticmethod
    def _normalize_symbols(symbols: list[tuple[str, str]]) -> list[tuple[str, str]]:
        normalized: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for symbol, market in symbols:
            market_code = normalize_market(market)
            key = (market_code, symbol.upper())
            if key in seen:
                continue
            seen.add(key)
            normalized.append((symbol, market_code))
        return normalized

    async def _subscribe_symbols(self, symbols: list[tuple[str, str]]) -> None:
        """실제 웹소켓 구독 수행"""
        for symbol, market_code in self._normalize_symbols(symbols):
            key = (market_code, symbol.upper())
            if key in self._active_symbols:
                continue
            if len(self._active_symbols) >= 41:
                logger.warning("구독 한도 도달 (41종목), 일부 감시 종목은 대기")
                break
            success = await kis_websocket.subscribe(symbol, market_code)
            if success:
                self._active_symbols[key] = (symbol, market_code)
                self._last_connect_error = None
                self._last_connect_at = now_kst()
            else:
                self._last_connect_error = self._last_connect_error or f"구독 실패: {market_code}:{symbol.upper()}"

    async def _unsubscribe_symbols(self, symbols: list[tuple[str, str]]) -> None:
        """실제 웹소켓 구독 해제 수행"""
        for symbol, market_code in self._normalize_symbols(symbols):
            key = (market_code, symbol.upper())
            self._active_symbols.pop(key, None)
            await kis_websocket.unsubscribe(symbol, market_code)

    def _desired_union_ordered(self) -> list[tuple[str, str]]:
        ordered: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for symbols in self._desired_order_by_scope.values():
            for symbol, market_code in symbols:
                key = (market_code, symbol.upper())
                if key in seen:
                    continue
                seen.add(key)
                ordered.append((symbol, market_code))
        return ordered

    @staticmethod
    def _scope_markets(scope: str) -> set[str]:
        normalized_scope = normalize_market_scope(scope)
        if normalized_scope == "KRX":
            return {"KRX"}
        return {"NASDAQ", "NYSE", "AMEX"}

    def desired_keys(self, scope: str | None = None) -> set[tuple[str, str]]:
        if scope is None:
            return {
                key
                for keys in self._desired_by_scope.values()
                for key in keys
            }
        return set(self._desired_by_scope.get(normalize_market_scope(scope), set()))

    def active_keys(self, scope: str | None = None) -> set[tuple[str, str]]:
        if scope is None:
            return set(self._active_symbols.keys())
        markets = self._scope_markets(scope)
        return {
            key for key in self._active_symbols
            if key[0] in markets
        }

    def note_message_received(self) -> None:
        self._last_message_at = now_kst()

    def stream_status(self, scope: str | None = None) -> dict:
        desired_keys = self.desired_keys(scope)
        active_keys = self.active_keys(scope)
        return {
            "running": self._running,
            "connected": self.is_connected,
            "desired_count": len(desired_keys),
            "active_count": len(active_keys),
            "subscription_count": len(active_keys),
            "subscription_limit": 41,
            "last_connect_error": self._last_connect_error,
            "last_connect_at": self._last_connect_at.isoformat() if self._last_connect_at else None,
            "last_message_at": self._last_message_at.isoformat() if self._last_message_at else None,
        }

    async def _ensure_connected(self) -> bool:
        desired = self._desired_union_ordered()
        if kis_websocket.is_connected:
            self._last_connect_error = None
            return True

        try:
            await kis_websocket.reset_runtime_state()
            self._active_symbols.clear()
            await kis_websocket.connect()
            self._last_connect_error = None
            if desired:
                await self._reconcile_subscriptions()
                return kis_websocket.is_connected
            return True
        except Exception as e:
            error_text = str(e)
            if error_text != self._last_connect_error:
                logger.warning("WebSocket 연결 실패 (나중에 재시도): {}", error_text)
            self._last_connect_error = error_text
            return False

    async def _reconcile_subscriptions(self) -> None:
        desired_order = self._desired_union_ordered()
        desired_limited = desired_order[:41]
        desired_set = {(market, symbol.upper()) for symbol, market in desired_limited}
        current_set = set(self._active_symbols.keys())

        to_remove = [(symbol, market) for (market, symbol) in (current_set - desired_set)]
        if to_remove:
            await self._unsubscribe_symbols(to_remove)

        to_add = [
            (symbol, market)
            for symbol, market in desired_limited
            if (market, symbol.upper()) not in current_set
        ]
        if to_add:
            await self._subscribe_symbols(to_add)

        skipped = len(desired_order) - len(desired_limited)
        if skipped > 0:
            logger.warning("웹소켓 desired 구독 {}건이 상한(41)으로 보류됨", skipped)

    async def replace_market_subscriptions(self, scope: str, symbols: list[tuple[str, str]]) -> None:
        """특정 market scope의 감시 종목 전체를 교체"""
        normalized_scope = normalize_market_scope(scope)
        normalized_symbols = self._normalize_symbols(symbols)
        self._desired_by_scope[normalized_scope] = {
            (market_code, symbol.upper())
            for symbol, market_code in normalized_symbols
        }
        self._desired_order_by_scope[normalized_scope] = normalized_symbols
        self._reconnect_event.set()
        if not self._running:
            if kis_websocket.is_connected or self.subscription_count > 0:
                await self._reconcile_subscriptions()
            return
        if not await self._ensure_connected():
            return
        await self._reconcile_subscriptions()

    async def ensure_symbol(self, scope: str, symbol: str, market: str) -> None:
        """특정 market scope에 감시 종목 1개를 추가"""
        normalized_scope = normalize_market_scope(scope)
        normalized_symbols = self._desired_order_by_scope.setdefault(normalized_scope, [])
        normalized_set = self._desired_by_scope.setdefault(normalized_scope, set())
        market_code = normalize_market(market)
        key = (market_code, symbol.upper())
        if key not in normalized_set:
            normalized_set.add(key)
            normalized_symbols.append((symbol, market_code))
            self._reconnect_event.set()
            if not self._running:
                if kis_websocket.is_connected or self.subscription_count > 0:
                    await self._reconcile_subscriptions()
                return
            if not await self._ensure_connected():
                return
            await self._reconcile_subscriptions()

    async def subscribe_symbols(self, symbols: list[tuple[str, str]]) -> None:
        """하위호환용 직접 구독"""
        await self._subscribe_symbols(symbols)

    async def unsubscribe_symbols(self, symbols: list[str | tuple[str, str]]) -> None:
        """하위호환용 직접 해제"""
        normalized: list[tuple[str, str]] = []
        for item in symbols:
            if isinstance(item, tuple):
                normalized.append((item[0], item[1]))
                continue
            match = next(
                ((symbol, market) for (market, symbol), (symbol, market) in self._active_symbols.items() if symbol.upper() == item.upper()),
                None,
            )
            if match:
                normalized.append(match)
        await self._unsubscribe_symbols(normalized)

    async def _run_listener(self) -> None:
        """WebSocket 수신 루프 (재연결 포함)"""
        while self._running:
            try:
                await kis_websocket.listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WebSocket 리스너 오류: {}", str(e))
                if self._running:
                    await kis_websocket.reset_runtime_state()
                    self._active_symbols.clear()
                    self._reconnect_event.set()
                    await asyncio.sleep(1)

    async def _run_supervisor(self) -> None:
        """초기 연결 실패/예상 밖 종료 후 주기적 복구"""
        while self._running:
            try:
                desired_count = len(self._desired_union_ordered())
                needs_reconcile = desired_count > 0 and len(self._active_symbols) < min(desired_count, 41)
                if desired_count > 0 and not kis_websocket.is_connected:
                    await self._ensure_connected()
                elif needs_reconcile:
                    await self._reconcile_subscriptions()
                try:
                    await asyncio.wait_for(self._reconnect_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                self._reconnect_event.clear()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("WebSocket supervisor 오류: {}", str(e))
                await asyncio.sleep(5)

    @property
    def subscription_count(self) -> int:
        return kis_websocket.subscription_count

    @property
    def is_connected(self) -> bool:
        return kis_websocket.is_connected

    @property
    def is_running(self) -> bool:
        return self._running


stream_manager = StreamManager()
