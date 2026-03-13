"""WebSocket 연결 관리 - 동적 구독/해제, 재연결"""
import asyncio

from loguru import logger

from trading.kis_websocket import kis_websocket
from trading.market_profile import normalize_market, normalize_market_scope


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

    async def start(self) -> None:
        """스트림 관리 시작"""
        self._running = True
        try:
            await kis_websocket.connect()
            self._listen_task = asyncio.create_task(self._run_listener())
            logger.info("스트림 매니저 시작")
        except Exception as e:
            self._running = False
            logger.warning("WebSocket 연결 실패 (나중에 재시도): {}", str(e))
            raise

    async def stop(self) -> None:
        """스트림 관리 중지"""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        await kis_websocket.disconnect()
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
                    logger.info("5초 후 재연결 시도...")
                    await asyncio.sleep(5)
                    try:
                        await kis_websocket.connect()
                        # 기존 구독 복원
                        self._active_symbols.clear()
                        await self._reconcile_subscriptions()
                    except Exception as re:
                        logger.error("재연결 실패: {}", str(re))

    @property
    def subscription_count(self) -> int:
        return kis_websocket.subscription_count

    @property
    def is_connected(self) -> bool:
        return kis_websocket.is_connected


stream_manager = StreamManager()
