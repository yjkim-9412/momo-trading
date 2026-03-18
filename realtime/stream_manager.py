"""WebSocket 연결 관리 - 동적 구독/해제, 재연결"""
import asyncio
from datetime import datetime, timedelta

from loguru import logger

from trading.kis_websocket import kis_websocket
from trading.market_profile import normalize_market, normalize_market_scope
from util.time_util import now_kst

_SUBSCRIBE_RETRY_COOLDOWN = timedelta(seconds=10)
_INITIAL_MESSAGE_GRACE = timedelta(seconds=20)
_MESSAGE_STALE_TIMEOUT = timedelta(seconds=90)
_DISCONNECTED_LISTENER_WAIT_SEC = 1.0


class StreamManager:
    """
    WebSocket 스트림 관리자
    - KIS 제한: 세션당 41종목
    - AI 선정 종목만 동적 구독/해제
    - 끊김 시 자동 재연결
    """

    def __init__(self):
        self._active_symbols: set[tuple[str, str]] = set()
        self._requested_symbols: dict[tuple[str, str], datetime] = {}
        self._desired_by_scope: dict[str, set[tuple[str, str]]] = {}
        self._desired_order_by_scope: dict[str, list[tuple[str, str]]] = {}
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._reconnect_event: asyncio.Event = asyncio.Event()
        self._last_connect_error: str | None = None
        self._last_connect_at: datetime | None = None
        self._last_message_at: datetime | None = None
        self._last_system_message_at: datetime | None = None
        self._last_business_error: str | None = None

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
        self._requested_symbols.clear()
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

    @staticmethod
    def _scope_markets(scope: str) -> set[str]:
        normalized_scope = normalize_market_scope(scope)
        if normalized_scope == "KRX":
            return {"KRX"}
        return {"NASDAQ", "NYSE", "AMEX"}

    def _sync_runtime_status_from_ws(self) -> None:
        requested_keys = kis_websocket.requested_subscription_keys
        confirmed_keys = kis_websocket.confirmed_subscription_keys
        self._active_symbols = set(confirmed_keys)
        self._requested_symbols = {
            key: requested_at
            for key, requested_at in self._requested_symbols.items()
            if key in requested_keys
        }
        default_requested_at = self._last_connect_at or now_kst()
        for key in requested_keys:
            self._requested_symbols.setdefault(key, default_requested_at)
        if kis_websocket.last_system_message_at is not None:
            self._last_system_message_at = kis_websocket.last_system_message_at
        if kis_websocket.last_business_error:
            self._last_business_error = kis_websocket.last_business_error

    async def _subscribe_symbols(self, symbols: list[tuple[str, str]]) -> None:
        """실제 웹소켓 구독 수행"""
        self._sync_runtime_status_from_ws()
        for symbol, market_code in self._normalize_symbols(symbols):
            key = (market_code, symbol.upper())
            if key in self._active_symbols:
                continue
            last_requested_at = self._requested_symbols.get(key)
            now = now_kst()
            if last_requested_at and now - last_requested_at < _SUBSCRIBE_RETRY_COOLDOWN:
                continue
            if len(self._requested_symbols) >= 41 and key not in self._requested_symbols:
                logger.warning("구독 한도 도달 (41종목), 일부 감시 종목은 대기")
                break
            success = await kis_websocket.subscribe(symbol, market_code)
            if success:
                self._requested_symbols[key] = now
                self._last_connect_error = None
                self._last_connect_at = now
            else:
                self._last_connect_error = self._last_connect_error or f"구독 요청 실패: {market_code}:{symbol.upper()}"
        self._sync_runtime_status_from_ws()

    async def _unsubscribe_symbols(self, symbols: list[tuple[str, str]]) -> None:
        """실제 웹소켓 구독 해제 수행"""
        for symbol, market_code in self._normalize_symbols(symbols):
            key = (market_code, symbol.upper())
            self._active_symbols.discard(key)
            self._requested_symbols.pop(key, None)
            await kis_websocket.unsubscribe(symbol, market_code)
        self._sync_runtime_status_from_ws()

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

    def _effective_desired_union_ordered(self) -> list[tuple[str, str]]:
        from scheduler.market_calendar import market_calendar

        ordered: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for scope, symbols in self._desired_order_by_scope.items():
            if not market_calendar.is_trading_hours(scope):
                continue
            for symbol, market_code in symbols:
                key = (market_code, symbol.upper())
                if key in seen:
                    continue
                seen.add(key)
                ordered.append((symbol, market_code))
        return ordered

    async def _maybe_disconnect_if_idle(self) -> None:
        self._sync_runtime_status_from_ws()
        if self._requested_symbols or self._active_symbols:
            return
        # 구독 0개여도 연결 유지 — disconnect는 앱 종료 시 main.py에서 처리
        self._last_business_error = None
        self._last_connect_error = None

    async def _deactivate_scope_subscriptions(self, scope: str) -> None:
        self._sync_runtime_status_from_ws()
        markets = self._scope_markets(scope)
        scope_keys = {
            key for key in (set(self._requested_symbols.keys()) | self._active_symbols)
            if key[0] in markets
        }
        if not scope_keys:
            await self._maybe_disconnect_if_idle()
            return
        to_remove = [(symbol, market) for (market, symbol) in scope_keys]
        await self._unsubscribe_symbols(to_remove)
        await self._maybe_disconnect_if_idle()

    def desired_keys(self, scope: str | None = None) -> set[tuple[str, str]]:
        if scope is None:
            return {
                key
                for keys in self._desired_by_scope.values()
                for key in keys
            }
        return set(self._desired_by_scope.get(normalize_market_scope(scope), set()))

    def requested_keys(self, scope: str | None = None) -> set[tuple[str, str]]:
        if scope is None:
            return set(self._requested_symbols.keys())
        markets = self._scope_markets(scope)
        return {
            key for key in self._requested_symbols
            if key[0] in markets
        }

    def active_keys(self, scope: str | None = None) -> set[tuple[str, str]]:
        if scope is None:
            return set(self._active_symbols)
        markets = self._scope_markets(scope)
        return {
            key for key in self._active_symbols
            if key[0] in markets
        }

    def note_message_received(self) -> None:
        self._last_message_at = now_kst()
        self._last_connect_error = None
        self._last_business_error = None

    def _health_snapshot(
        self,
        scope: str | None,
        desired_count: int,
        requested_count: int,
        active_count: int,
    ) -> tuple[str, str]:
        if not self._running:
            return "DISCONNECTED", "스트림 중지"

        if desired_count == 0:
            if kis_websocket.is_connected:
                return "CONNECTED", "감시 대상 없음"
            return "DISCONNECTED", "감시 대상 없음"

        if self._last_connect_error and not kis_websocket.is_connected:
            return "ERROR", self._last_connect_error

        if not kis_websocket.is_connected:
            return "DISCONNECTED", "소켓 미연결"

        if self._last_business_error and active_count == 0:
            return "ERROR", self._last_business_error

        if active_count == 0:
            if requested_count > 0:
                return "DEGRADED", f"구독 확인 대기 0/{min(desired_count, 41)}"
            return "ERROR", "구독 미확인"

        partial_reason = None
        target_count = min(desired_count, 41)
        if active_count < target_count:
            partial_reason = f"구독 일부 미확인 {active_count}/{target_count}"
            if self._last_business_error:
                partial_reason = self._last_business_error

        now = now_kst()
        if self._last_message_at is None:
            if self._last_connect_at and now - self._last_connect_at <= _INITIAL_MESSAGE_GRACE:
                if partial_reason:
                    return "DEGRADED", partial_reason
                return "CONNECTED", "초기 체결 대기"
            return "DEGRADED", partial_reason or "체결 수신 없음"

        if now - self._last_message_at > _MESSAGE_STALE_TIMEOUT:
            return "DEGRADED", "체결 수신 지연"

        if partial_reason:
            return "DEGRADED", partial_reason

        return "CONNECTED", "체결 수신 정상"

    def stream_status(self, scope: str | None = None) -> dict:
        self._sync_runtime_status_from_ws()
        desired_keys = self.desired_keys(scope)
        requested_keys = self.requested_keys(scope)
        active_keys = self.active_keys(scope)
        health, status_reason = self._health_snapshot(
            scope,
            len(desired_keys),
            len(requested_keys),
            len(active_keys),
        )
        return {
            "running": self._running,
            "connected": health == "CONNECTED",
            "health": health,
            "status_reason": status_reason,
            "desired_count": len(desired_keys),
            "requested_count": len(requested_keys),
            "active_count": len(active_keys),
            "subscription_count": len(active_keys),
            "subscription_limit": 41,
            "last_connect_error": self._last_connect_error,
            "last_business_error": self._last_business_error,
            "last_connect_at": self._last_connect_at.isoformat() if self._last_connect_at else None,
            "last_message_at": self._last_message_at.isoformat() if self._last_message_at else None,
            "last_system_message_at": self._last_system_message_at.isoformat() if self._last_system_message_at else None,
        }

    async def _ensure_connected(self) -> bool:
        desired = self._effective_desired_union_ordered()
        self._sync_runtime_status_from_ws()
        if kis_websocket.is_connected and not kis_websocket.fatal_error:
            self._last_connect_error = None
            return True
        if not desired:
            self._last_connect_error = None
            return True

        try:
            await kis_websocket.reset_runtime_state()
            self._active_symbols.clear()
            self._requested_symbols.clear()
            await kis_websocket.connect()
            self._last_connect_error = None
            self._last_business_error = None
            self._last_connect_at = now_kst()
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
        desired_order = self._effective_desired_union_ordered()
        desired_limited = desired_order[:41]
        desired_set = {(market, symbol.upper()) for symbol, market in desired_limited}
        current_set = set(self._requested_symbols.keys()) | self._active_symbols

        to_remove = [(symbol, market) for (market, symbol) in (current_set - desired_set)]
        if to_remove:
            await self._unsubscribe_symbols(to_remove)

        if desired_limited:
            await self._subscribe_symbols(desired_limited)
        else:
            await self._maybe_disconnect_if_idle()

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

        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_trading_hours(normalized_scope):
            await self._deactivate_scope_subscriptions(normalized_scope)
            logger.info("[{}] 장외시간 — WS 구독 보류 (desired {}종목 저장)", normalized_scope, len(normalized_symbols))
            return

        self._reconnect_event.set()
        if not self._running:
            if kis_websocket.is_connected or self.subscription_count > 0 or self.requested_count > 0:
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
            from scheduler.market_calendar import market_calendar
            if not market_calendar.is_trading_hours(normalized_scope):
                await self._deactivate_scope_subscriptions(normalized_scope)
                return
            self._reconnect_event.set()
            if not self._running:
                if kis_websocket.is_connected or self.subscription_count > 0 or self.requested_count > 0:
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
        all_keys = set(self._requested_symbols.keys()) | self._active_symbols
        for item in symbols:
            if isinstance(item, tuple):
                normalized.append((item[0], item[1]))
                continue
            match = next(
                ((symbol, market) for (market, symbol) in all_keys if symbol.upper() == item.upper()),
                None,
            )
            if match:
                normalized.append(match)
        await self._unsubscribe_symbols(normalized)

    async def _run_listener(self) -> None:
        """WebSocket 수신 루프 (재연결 포함)"""
        while self._running:
            if (
                not kis_websocket.is_connected
                and not self._requested_symbols
                and not self._active_symbols
            ):
                try:
                    await asyncio.wait_for(
                        self._reconnect_event.wait(),
                        timeout=_DISCONNECTED_LISTENER_WAIT_SEC,
                    )
                except asyncio.TimeoutError:
                    pass
                self._reconnect_event.clear()
                continue
            try:
                await kis_websocket.listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_connect_error = str(e)
                self._last_business_error = kis_websocket.last_business_error or self._last_business_error
                logger.error("WebSocket 리스너 오류: {}", str(e))
                if self._running:
                    await kis_websocket.reset_runtime_state()
                    self._active_symbols.clear()
                    self._requested_symbols.clear()
                    self._reconnect_event.set()
                    await asyncio.sleep(1)

    async def _run_supervisor(self) -> None:
        """초기 연결 실패/예상 밖 종료 후 주기적 복구"""
        while self._running:
            try:
                desired_count = len(self._effective_desired_union_ordered())
                needs_reconcile = desired_count > 0 and len(self._active_symbols) < min(desired_count, 41)
                if desired_count > 0 and (not kis_websocket.is_connected or kis_websocket.fatal_error):
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
        self._sync_runtime_status_from_ws()
        return len(self._active_symbols)

    @property
    def requested_count(self) -> int:
        self._sync_runtime_status_from_ws()
        return len(self._requested_symbols)

    @property
    def is_connected(self) -> bool:
        return self.stream_status().get("health") == "CONNECTED"

    @property
    def is_running(self) -> bool:
        return self._running


stream_manager = StreamManager()
