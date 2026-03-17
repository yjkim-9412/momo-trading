"""KIS Trading MCP 서버 HTTP/SSE 클라이언트"""
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from loguru import logger

from core.config import settings
from trading.market_profile import (
    is_crypto_market,
    is_domestic_market,
    kis_exchange_code,
    market_currency,
    normalize_market,
)
from trading.models import MCPResponse

# SSE 재연결 설정
_SSE_RECONNECT_DELAY = 2.0  # 재연결 대기 초
_SSE_MAX_RECONNECT_DELAY = 30.0  # 최대 재연결 대기 초
_SSE_MAX_RECONNECT_ATTEMPTS = 50  # 최대 재연결 시도 횟수

# KIS API rate limit: 모의투자 초당 ~10건 (공식 20건이지만 실제 더 엄격)
_RATE_LIMIT_PER_SEC = 8
_RATE_LIMIT_WINDOW = 1.0  # 초
_MAX_CONCURRENT_CALLS = 3  # 동시 MCP 호출 상한
_OVERSEAS_QUOTE_MIN_INTERVAL = 1.0  # 해외 시세는 더 보수적으로 직렬화
_OVERSEAS_QUOTE_MAX_RETRIES = 2
_OVERSEAS_BALANCE_MIN_INTERVAL = 1.0  # 해외 잔고도 계정 단위로 직렬화
_OVERSEAS_BALANCE_MAX_RETRIES = 2
_US_SCAN_RESULT_LIMIT = 30


class MCPClient:
    """KIS Trading MCP Docker 서버와 통신하는 SSE 클라이언트

    MCP SSE 프로토콜:
    1. GET /sse → 영구 SSE 스트림 (서버→클라이언트 메시지 수신)
    2. POST /messages/?session_id=xxx → 도구 호출 요청 (202 Accepted)
    3. 결과는 SSE 스트림의 'message' 이벤트로 수신

    SSE 연결이 끊기면 자동 재연결 + 대기 중인 요청을 즉시 실패 처리합니다.
    """

    def __init__(self):
        self._base_url = settings.KIS_MCP_URL.rstrip("/sse").rstrip("/")
        self._post_client: httpx.AsyncClient | None = None
        self._sse_client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._sse_task: asyncio.Task | None = None
        self._shutting_down = False
        self._reconnect_count = 0
        # Rate limiter: 초당 요청 타임스탬프 + 동시 호출 세마포어
        self._call_timestamps: list[float] = []
        self._rate_lock = asyncio.Lock()
        self._call_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CALLS)
        self._fx_cache: dict[str, tuple[float, float]] = {}
        self._overseas_quote_semaphore = asyncio.Semaphore(1)
        self._overseas_quote_rate_lock = asyncio.Lock()
        self._overseas_quote_last_call_at = 0.0
        self._overseas_balance_semaphore = asyncio.Semaphore(1)
        self._overseas_balance_rate_lock = asyncio.Lock()
        self._overseas_balance_last_call_at = 0.0

    @staticmethod
    def _extract_business_error(data: Any) -> tuple[str | None, str]:
        if not isinstance(data, dict):
            return None, ""
        rt_cd = str(data.get("rt_cd") or "").strip()
        if not rt_cd or rt_cd == "0":
            return None, ""
        error_msg = str(data.get("msg1") or data.get("msg_cd") or f"KIS 요청 실패 (rt_cd={rt_cd})")
        return rt_cd, error_msg

    @staticmethod
    def _summarize_overseas_balance_payload(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {"payload_type": type(data).__name__}

        output1 = data.get("output1")
        output2 = data.get("output2")
        output3 = data.get("output3")

        return {
            "success": data.get("success"),
            "rt_cd": data.get("rt_cd"),
            "msg1": data.get("msg1"),
            "output1_count": len(output1) if isinstance(output1, list) else int(bool(output1)),
            "output2_count": len(output2) if isinstance(output2, list) else int(isinstance(output2, dict)),
            "output3_keys": sorted(output3.keys())[:8] if isinstance(output3, dict) else [],
        }

    def _select_present_balance_currency_record(
        self,
        data: dict[str, Any] | None,
        market: str,
    ) -> tuple[str, dict[str, Any]]:
        market_code = normalize_market(market)
        target_currency = market_currency(market_code)
        summaries = self._extract_records(data or {}, "output2", "output1")

        for rec in summaries:
            if isinstance(rec, dict) and str(rec.get("crcy_cd") or "").upper() == target_currency:
                return target_currency, rec
        return target_currency, {}

    def _normalize_overseas_order_record(
        self,
        item: dict[str, Any],
        market: str,
        default_exchange_rate: float,
    ) -> dict[str, Any]:
        market_code = normalize_market(
            self._pick_first(item, "market", "ovrs_excg_cd", default=market),
            default=market,
        )
        currency = str(
            self._pick_first(item, "currency", "tr_crcy_cd", default=market_currency(market_code))
        )
        exchange_rate = self._to_float(
            self._pick_first(item, "exchange_rate_to_krw", "frst_bltn_exrt", "bass_exrt"),
            default_exchange_rate,
        )
        order_qty = self._to_int(
            self._pick_first(item, "order_qty", "ft_ord_qty", "ord_qty")
        )
        filled_qty = self._to_int(
            self._pick_first(
                item,
                "filled_qty",
                "ft_ccld_qty",
                "tot_ccld_qty",
                "ccld_qty",
            )
        )
        remaining_qty = self._to_int(
            self._pick_first(item, "remaining_qty", "nccs_qty", "rmn_qty")
        )
        order_price = self._to_float(
            self._pick_first(item, "order_price", "ft_ord_unpr3", "ord_unpr", "ovrs_ord_unpr")
        )
        filled_price = self._to_float(
            self._pick_first(item, "filled_price", "ft_ccld_unpr3", "avg_prvs", "ccld_pric")
        )
        side_code = str(self._pick_first(item, "side_code", "sll_buy_dvsn_cd", "side", default=""))
        side_label = "BUY" if side_code in ("02", "BUY", "매수") else "SELL" if side_code else ""

        return {
            **item,
            "order_id": self._pick_first(item, "order_id", "odno", "ODNO", default=""),
            "symbol": self._pick_first(item, "symbol", "pdno", "ovrs_pdno", default=""),
            "name": self._pick_first(item, "name", "prdt_name", "ovrs_item_name", default=""),
            "market": market_code,
            "currency": currency,
            "side_code": side_code,
            "side": side_label,
            "order_qty": order_qty,
            "filled_qty": filled_qty,
            "remaining_qty": remaining_qty,
            "order_price": order_price,
            "filled_price": filled_price,
            "filled_price_krw": filled_price * exchange_rate if currency != "KRW" else filled_price,
            "order_time": self._pick_first(item, "order_time", "ord_tmd", "thco_ord_tmd", default=""),
            "status": self._pick_first(item, "status", "prcs_stat_name", default=""),
            "reject_reason": self._pick_first(item, "reject_reason", "rjct_rson_name", "rjct_rson", default=""),
            "exchange_rate_to_krw": exchange_rate,
        }

    @property
    def is_connected(self) -> bool:
        return self._post_client is not None and self._session_id is not None

    async def connect(self) -> None:
        self._shutting_down = False
        self._reconnect_count = 0

        # POST 전용 클라이언트 (도구 호출용) — 30초 타임아웃
        self._post_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={"Host": "localhost:3000"},
        )
        # SSE 전용 클라이언트 — read timeout 없음 (장기 스트림)
        self._sse_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(None, connect=10.0),
            follow_redirects=True,
            headers={"Host": "localhost:3000"},
        )
        try:
            await self._start_sse()
            logger.info("MCP 서버 연결 성공: {}", self._base_url)
        except Exception as e:
            logger.error("MCP 서버 연결 실패: {}", str(e))
            raise

    async def disconnect(self) -> None:
        self._shutting_down = True
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sse_task = None
        for client in (self._post_client, self._sse_client):
            if client:
                await client.aclose()
        self._post_client = None
        self._sse_client = None
        self._fail_all_pending("MCP 연결 종료")
        self._session_id = None
        logger.info("MCP 서버 연결 종료")

    def _fail_all_pending(self, reason: str) -> None:
        """대기 중인 모든 Future를 즉시 실패 처리"""
        if not self._pending:
            return
        count = len(self._pending)
        for msg_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result({"error": {"message": reason}})
        self._pending.clear()
        if count:
            logger.warning("SSE 끊김 → 대기 요청 {}건 즉시 실패 처리: {}", count, reason)

    async def _start_sse(self) -> None:
        """SSE 연결 시작 — 세션 ID 획득 → 프로토콜 초기화 → 백그라운드 리스너"""
        ready = asyncio.Event()
        self._sse_task = asyncio.create_task(self._sse_loop(ready))
        # 세션 ID 획득까지 대기 (최대 10초)
        try:
            await asyncio.wait_for(ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("MCP SSE 세션 초기화 타임아웃")
            raise ConnectionError("MCP SSE 세션 초기화 타임아웃")

        # MCP 프로토콜 초기화 핸드셰이크
        await self._mcp_initialize()

    async def _mcp_initialize(self) -> None:
        """MCP 프로토콜 초기화 핸드셰이크"""
        # 1. initialize 요청
        init_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[init_id] = fut

        await self._post_client.post(self._session_id, json={
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "momo-trading", "version": "0.1.0"},
            },
        })

        try:
            await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(init_id, None)
            logger.warning("MCP initialize 응답 타임아웃")
            return

        # 2. initialized 알림 (응답 불필요)
        await self._post_client.post(self._session_id, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        logger.info("MCP 프로토콜 초기화 완료")

    async def _sse_loop(self, initial_ready: asyncio.Event) -> None:
        """SSE 연결 유지 루프 — 끊기면 자동 재연결

        흐름:
        1. SSE 스트림을 백그라운드 task로 시작
        2. ready 이벤트 대기 (세션 ID 수신)
        3. 초기화 후 스트림이 끊길 때까지 대기
        4. 끊기면 대기 요청 실패 처리 → 재연결
        """
        delay = _SSE_RECONNECT_DELAY
        is_first = True

        while not self._shutting_down:
            ready = initial_ready if is_first else asyncio.Event()

            if not is_first:
                # 재연결: SSE 클라이언트 재생성
                if self._sse_client:
                    try:
                        await self._sse_client.aclose()
                    except Exception:
                        pass
                self._sse_client = httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=httpx.Timeout(None, connect=10.0),
                    follow_redirects=True,
                    headers={"Host": "localhost:3000"},
                )

            # SSE 리스너를 백그라운드 task로 시작
            listen_task = asyncio.create_task(self._sse_listen_once(ready))

            if not is_first:
                # 재연결: 세션 ID 획득 대기 → 프로토콜 초기화
                try:
                    await asyncio.wait_for(ready.wait(), timeout=10.0)
                    await self._mcp_initialize()
                    self._reconnect_count = 0
                    delay = _SSE_RECONNECT_DELAY
                    logger.info("MCP SSE 재연결 성공 (세션: {})",
                                self._session_id[:20] if self._session_id else "?")
                except Exception as e:
                    logger.warning("MCP SSE 재연결 실패: {}", str(e))
                    listen_task.cancel()
                    try:
                        await listen_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    self._fail_all_pending("SSE 재연결 실패")
                    self._session_id = None
                    self._reconnect_count += 1
                    if self._reconnect_count > _SSE_MAX_RECONNECT_ATTEMPTS:
                        logger.error("MCP SSE 재연결 한도 초과")
                        break
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.5, _SSE_MAX_RECONNECT_DELAY)
                    continue

            # 스트림이 끊길 때까지 대기
            try:
                await listen_task
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("MCP SSE 스트림 종료: {}", str(e))

            if self._shutting_down:
                break

            # 끊김 → 대기 중인 요청 즉시 실패 처리
            self._fail_all_pending("SSE 연결 끊김, 재연결 중")
            self._session_id = None

            self._reconnect_count += 1
            if self._reconnect_count > _SSE_MAX_RECONNECT_ATTEMPTS:
                logger.error("MCP SSE 재연결 한도 초과 ({}회)", _SSE_MAX_RECONNECT_ATTEMPTS)
                break

            logger.info("MCP SSE 재연결 시도 ({}/{}) — {:.1f}초 대기",
                        self._reconnect_count, _SSE_MAX_RECONNECT_ATTEMPTS, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, _SSE_MAX_RECONNECT_DELAY)
            is_first = False

    async def _sse_listen_once(self, ready: asyncio.Event) -> None:
        """단일 SSE 연결 수신 — 연결이 끊기면 반환"""
        async with self._sse_client.stream("GET", "/sse") as response:
            event_type = ""
            data_buffer = ""
            async for line in response.aiter_lines():
                if self._shutting_down:
                    return
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_buffer = line[5:].strip()
                elif line == "" and data_buffer:
                    await self._handle_sse_event(event_type, data_buffer, ready)
                    event_type = ""
                    data_buffer = ""
                elif line.startswith(":"):
                    pass

    async def _handle_sse_event(
        self, event_type: str, data: str, ready: asyncio.Event
    ) -> None:
        """SSE 이벤트 처리"""
        if event_type == "endpoint" or "/messages" in data:
            # 세션 초기화 — data에 메시지 엔드포인트 경로
            if "/messages" in data:
                self._session_id = data
                logger.debug("MCP 세션 초기화 완료: {}", self._session_id)
                ready.set()
                return

        if event_type == "message":
            try:
                msg = json.loads(data)
                msg_id = msg.get("id")
                logger.debug("MCP SSE 메시지 수신: id={}, pending={}", msg_id, list(self._pending.keys()))
                if msg_id is not None and msg_id in self._pending:
                    fut = self._pending.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                elif msg_id is not None:
                    logger.warning("MCP SSE 메시지 id={}에 대한 대기 Future 없음", msg_id)
            except json.JSONDecodeError:
                logger.warning("MCP SSE 메시지 파싱 실패: {}", data[:200])
        else:
            # 알 수 없는 이벤트 타입 로깅
            if event_type and event_type != "endpoint":
                logger.debug("MCP SSE 이벤트: type={}, data={}", event_type, data[:100])

    async def _wait_for_session(self, timeout: float = 5.0) -> bool:
        """SSE 세션이 준비될 때까지 대기 (재연결 중일 수 있으므로)"""
        if self._session_id:
            return True
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self._session_id:
                return True
            if self._shutting_down:
                return False
            await asyncio.sleep(0.2)
        return self._session_id is not None

    async def _rate_limit(self) -> None:
        """KIS API 초당 요청 한도 준수 — 초과 시 대기"""
        async with self._rate_lock:
            now = time.monotonic()
            # 1초 이전 타임스탬프 제거
            self._call_timestamps = [
                t for t in self._call_timestamps
                if now - t < _RATE_LIMIT_WINDOW
            ]
            if len(self._call_timestamps) >= _RATE_LIMIT_PER_SEC:
                # 가장 오래된 요청이 1초 지날 때까지 대기
                wait = _RATE_LIMIT_WINDOW - (now - self._call_timestamps[0]) + 0.05
                if wait > 0:
                    logger.debug("KIS rate limit 대기: {:.2f}초", wait)
                    await asyncio.sleep(wait)
            self._call_timestamps.append(time.monotonic())

    async def _rate_limit_overseas_quote(self) -> None:
        """해외 시세 REST 호출은 계정 단위 한도가 엄격해 보수적으로 직렬화"""
        async with self._overseas_quote_rate_lock:
            now = time.monotonic()
            wait = _OVERSEAS_QUOTE_MIN_INTERVAL - (now - self._overseas_quote_last_call_at)
            if wait > 0:
                logger.debug("해외 시세 rate limit 대기: {:.2f}초", wait)
                await asyncio.sleep(wait)
            self._overseas_quote_last_call_at = time.monotonic()

    async def _rate_limit_overseas_balance(self) -> None:
        """해외 잔고 REST 호출은 계정 단위 burst를 피하도록 직렬화한다."""
        async with self._overseas_balance_rate_lock:
            now = time.monotonic()
            wait = _OVERSEAS_BALANCE_MIN_INTERVAL - (now - self._overseas_balance_last_call_at)
            if wait > 0:
                logger.debug("해외 잔고 rate limit 대기: {:.2f}초", wait)
                await asyncio.sleep(wait)
            self._overseas_balance_last_call_at = time.monotonic()

    async def _call_overseas_quote(
        self,
        request_name: str,
        request_factory: Callable[[], Awaitable[dict[str, Any]]],
        _retry: int = 0,
    ) -> MCPResponse:
        """해외 시세 REST 호출 공통 게이트: 직렬화 + 간격 제한 + rate-limit 재시도"""
        async with self._overseas_quote_semaphore:
            await self._rate_limit_overseas_quote()
            try:
                result = await request_factory()
            except Exception as e:
                logger.error("해외 시세 호출 오류 ({}): {}", request_name, str(e))
                return MCPResponse(success=False, error=str(e))

        if not isinstance(result, dict):
            return MCPResponse(success=False, error=f"잘못된 해외 시세 응답: {request_name}")

        rt_cd, error_msg = self._extract_business_error(result)
        if rt_cd is not None:
            if "초당 거래건수" in error_msg and _retry < _OVERSEAS_QUOTE_MAX_RETRIES:
                wait = max(_OVERSEAS_QUOTE_MIN_INTERVAL, 1.0 + _retry * 0.5)
                logger.warning(
                    "해외 시세 rate limit ({}) → {:.1f}초 대기 후 재시도 ({}/{})",
                    request_name,
                    wait,
                    _retry + 1,
                    _OVERSEAS_QUOTE_MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                return await self._call_overseas_quote(
                    request_name,
                    request_factory,
                    _retry=_retry + 1,
                )
            return MCPResponse(success=False, error=error_msg[:200], data=result)

        return MCPResponse(
            success=result.get("success", False),
            data=result,
            error=result.get("error"),
        )

    async def _call_overseas_balance(
        self,
        request_name: str,
        request_factory: Callable[[], Awaitable[dict[str, Any]]],
        _retry: int = 0,
    ) -> MCPResponse:
        """해외 잔고 REST 호출 공통 게이트: 직렬화 + 간격 제한 + rate-limit 재시도"""
        async with self._overseas_balance_semaphore:
            await self._rate_limit_overseas_balance()
            try:
                result = await request_factory()
            except Exception as e:
                logger.error("해외 잔고 호출 오류 ({}): {}", request_name, str(e))
                return MCPResponse(success=False, error=str(e))

        if not isinstance(result, dict):
            return MCPResponse(success=False, error=f"잘못된 해외 잔고 응답: {request_name}")

        rt_cd, business_error = self._extract_business_error(result)
        error_msg = business_error
        if rt_cd is None and not result.get("success", False):
            error_msg = str(result.get("error") or result.get("msg1") or f"해외 잔고 조회 실패: {request_name}")

        if error_msg:
            logger.warning(
                "해외 잔고 응답 오류 ({}): {} | {}",
                request_name,
                error_msg,
                self._summarize_overseas_balance_payload(result),
            )
            if "초당 거래건수" in error_msg and _retry < _OVERSEAS_BALANCE_MAX_RETRIES:
                wait = max(_OVERSEAS_BALANCE_MIN_INTERVAL, 1.0 + _retry * 0.5)
                logger.warning(
                    "해외 잔고 rate limit ({}) → {:.1f}초 대기 후 재시도 ({}/{})",
                    request_name,
                    wait,
                    _retry + 1,
                    _OVERSEAS_BALANCE_MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                return await self._call_overseas_balance(
                    request_name,
                    request_factory,
                    _retry=_retry + 1,
                )
            return MCPResponse(success=False, error=error_msg[:200], data=result)

        return MCPResponse(
            success=result.get("success", False),
            data=result,
            error=result.get("error"),
        )

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None,
        _retry: int = 0,
    ) -> MCPResponse:
        """MCP 도구 호출 — 세마포어 + rate limit으로 초당 한도 준수"""
        if not self._post_client:
            return MCPResponse(success=False, error="MCP 클라이언트가 연결되지 않았습니다")

        # SSE 재연결 중이면 잠시 대기
        if not self._session_id:
            if not await self._wait_for_session(timeout=10.0):
                return MCPResponse(success=False, error="MCP SSE 세션 없음 (재연결 실패)")

        # 동시 호출 제한 + rate limit
        async with self._call_semaphore:
            await self._rate_limit()
            return await self._call_tool_inner(tool_name, arguments, _retry)

    async def _call_tool_inner(
        self, tool_name: str, arguments: dict[str, Any] | None,
        _retry: int,
    ) -> MCPResponse:
        """실제 MCP 도구 호출 (세마포어 내부)"""
        msg_id = self._next_id
        self._next_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut

        try:
            response = await self._post_client.post(self._session_id, json=payload)
            if response.status_code not in (200, 202):
                self._pending.pop(msg_id, None)
                return MCPResponse(
                    success=False, error=f"HTTP {response.status_code}"
                )

            # SSE 스트림에서 결과 대기 (최대 30초)
            result = await asyncio.wait_for(fut, timeout=30.0)

            if "error" in result:
                error_msg = result["error"].get("message", "MCP 도구 호출 실패")
                return await self._maybe_retry_rate_limit(
                    error_msg, tool_name, arguments, _retry)

            content = result.get("result", {}).get("content", [])
            is_error = result.get("result", {}).get("isError", False)
            data = {}
            for item in content:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if is_error:
                        return await self._maybe_retry_rate_limit(
                            text[:300], tool_name, arguments, _retry)
                    try:
                        data = json.loads(text)
                    except (json.JSONDecodeError, KeyError):
                        data = {"text": text}
                    break

            if not data:
                logger.warning("MCP 도구 응답 content 비어있음: {}", str(result)[:300])
                return MCPResponse(success=False, error=f"빈 응답: {tool_name}", data={})

            rt_cd, error_msg = self._extract_business_error(data)
            if rt_cd is not None:
                logger.warning("KIS business error ({}): rt_cd={}, msg={}", tool_name, rt_cd, error_msg)
                return await self._maybe_retry_rate_limit(
                    error_msg,
                    tool_name,
                    arguments,
                    _retry,
                    data=data,
                )

            return MCPResponse(success=True, data=data)

        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            logger.error("MCP 도구 호출 타임아웃: {}", tool_name)
            return MCPResponse(success=False, error=f"타임아웃: {tool_name}")
        except httpx.ConnectError:
            self._pending.pop(msg_id, None)
            logger.error("MCP 서버 연결 불가: {}", self._base_url)
            return MCPResponse(success=False, error="MCP 서버 연결 불가")
        except Exception as e:
            self._pending.pop(msg_id, None)
            logger.error("MCP 도구 호출 오류 ({}): {}", tool_name, str(e))
            return MCPResponse(success=False, error=str(e))

    async def _maybe_retry_rate_limit(
        self, error_msg: str, tool_name: str,
        arguments: dict[str, Any] | None, _retry: int,
        data: dict[str, Any] | None = None,
    ) -> MCPResponse:
        """rate limit 에러면 1초 대기 후 재시도, 아니면 그대로 실패"""
        if "초당 거래건수" in error_msg and _retry < 2:
            wait = 1.0 + _retry * 0.5
            logger.warning("KIS rate limit ({}) → {:.1f}초 대기 후 재시도 ({}/2)",
                           tool_name, wait, _retry + 1)
            await asyncio.sleep(wait)
            await self._rate_limit()
            return await self._call_tool_inner(tool_name, arguments, _retry + 1)
        return MCPResponse(success=False, error=error_msg[:200], data=data)

    async def list_tools(self) -> list[dict]:
        """사용 가능한 MCP 도구 목록 조회"""
        if not self._post_client or not self._session_id:
            return []

        msg_id = self._next_id
        self._next_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/list",
        }

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[msg_id] = fut

        try:
            await self._post_client.post(self._session_id, json=payload)
            result = await asyncio.wait_for(fut, timeout=10.0)
            return result.get("result", {}).get("tools", [])
        except Exception as e:
            self._pending.pop(msg_id, None)
            logger.error("MCP 도구 목록 조회 실패: {}", str(e))
            return []

    # === KIS 값 변환 헬퍼 (KIS API는 모든 숫자를 문자열로 반환) ===

    @staticmethod
    def _to_float(val, default: float = 0.0) -> float:
        """KIS 문자열 값 → float 변환 ("72300" → 72300.0, "" → 0.0)"""
        if val is None or val == "":
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _to_int(val, default: int = 0) -> int:
        """KIS 문자열 값 → int 변환"""
        if val is None or val == "":
            return default
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _pick_first(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
        """응답 딕셔너리에서 첫 유효값 반환"""
        for key in keys:
            value = data.get(key)
            if value not in (None, "", []):
                return value
        return default

    @staticmethod
    def _extract_records(data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
        """응답 내 배열/단일 레코드 정규화"""
        for key in keys:
            records = data.get(key)
            if isinstance(records, list):
                return [item for item in records if isinstance(item, dict)]
            if isinstance(records, dict):
                return [records]
        return []

    @staticmethod
    def _first_record(value: Any) -> dict[str, Any]:
        """dict 또는 dict 배열에서 첫 레코드 반환"""
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
        return {}

    @staticmethod
    def _sort_series_records(records: list[dict[str, Any]], *keys: str) -> list[dict[str, Any]]:
        """시계열 레코드를 과거→현재 순으로 정렬"""
        if not records:
            return []

        def sort_key(item: dict[str, Any]) -> tuple[int, str]:
            for key in keys:
                value = item.get(key)
                if value not in (None, ""):
                    return (0, str(value))
            return (1, "")

        return sorted(records, key=sort_key)

    async def call_any_tool(
        self, tool_calls: list[tuple[str, dict[str, Any]]]
    ) -> MCPResponse:
        """가능한 도구를 순차 시도하여 첫 성공 응답 반환"""
        errors: list[str] = []
        for tool_name, arguments in tool_calls:
            response = await self.call_tool(tool_name, arguments)
            if response.success:
                return response
            if response.error:
                errors.append(f"{tool_name}: {response.error}")
        return MCPResponse(
            success=False,
            error=" / ".join(errors[:3]) or "사용 가능한 MCP 도구를 찾지 못했습니다",
        )

    def _runtime_env_value(self) -> str:
        return "demo" if settings.is_paper_trading else "real"

    async def _get_exchange_rate_to_krw(self, market: str) -> float:
        """시장 통화를 KRW로 환산하는 환율 조회"""
        market_code = normalize_market(market)
        if market_currency(market_code) == "KRW":
            return 1.0

        now = time.monotonic()
        cached = self._fx_cache.get(market_code)
        if cached and now - cached[1] < 300:
            return cached[0]

        try:
            from trading.kis_api import get_overseas_present_balance

            response = await self._call_overseas_balance(
                f"{market_code}:fx-present-balance",
                lambda: get_overseas_present_balance(market_code),
            )
            if response.success and response.data:
                _, summary = self._select_present_balance_currency_record(response.data, market_code)
                rate = self._to_float(self._pick_first(
                    summary,
                    "frst_bltn_exrt",
                    "bass_exrt",
                    "exchange_rate_to_krw",
                ), 0.0)
                if rate > 0:
                    self._fx_cache[market_code] = (rate, now)
                    return rate
                logger.warning(
                    "[{}] 환율 조회 응답에서 {} 통화 환율을 찾지 못함 | {}",
                    market_code,
                    market_currency(market_code),
                    self._summarize_overseas_balance_payload(response.data),
                )
        except Exception as e:
            logger.debug("환율 조회 실패 ({}): {}", market_code, str(e))

        return cached[0] if cached else 1.0

    async def _build_watchlist_scan(self, market: str) -> list[dict[str, Any]]:
        """미국장 랭킹 API 폴백용 감시종목 스캔"""
        watchlist = settings.us_watchlist_symbols
        if not watchlist:
            return []

        responses = await asyncio.gather(
            *[self.get_current_price(symbol, market=market) for symbol in watchlist],
            return_exceptions=True,
        )

        stocks: list[dict[str, Any]] = []
        for symbol, response in zip(watchlist, responses, strict=False):
            if isinstance(response, Exception) or not getattr(response, "success", False):
                continue
            data = response.data or {}
            price = self._to_float(data.get("price", data.get("current_price", 0)))
            if price <= 0:
                continue
            stocks.append({
                "symbol": symbol,
                "name": data.get("name", symbol),
                "market": normalize_market(data.get("market", market), default=market),
                "currency": data.get("currency", market_currency(market)),
                "price": price,
                "current_price": price,
                "change": self._to_float(data.get("change", 0)),
                "change_rate": self._to_float(data.get("change_rate", 0)),
                "volume": self._to_int(data.get("volume", 0)),
                "scan_source": "WATCHLIST",
            })
        return stocks

    @staticmethod
    def _merge_discovery_and_watchlist(
        discovery_stocks: list[dict[str, Any]],
        watchlist_stocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """동적 발굴 + 워치리스트 결과 병합 (심볼 기준 중복 제거)"""
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for stock in discovery_stocks:
            sym = stock.get("symbol", "")
            if sym and sym not in seen:
                seen.add(sym)
                merged.append(stock)
        for stock in watchlist_stocks:
            sym = stock.get("symbol", "")
            if sym and sym not in seen:
                seen.add(sym)
                merged.append(stock)
        return merged

    @staticmethod
    def _count_non_watchlist_symbols(
        stocks: list[dict[str, Any]],
        watchlist_stocks: list[dict[str, Any]],
    ) -> int:
        watchlist_symbols = {
            str(stock.get("symbol", "")).upper()
            for stock in watchlist_stocks
            if stock.get("symbol")
        }
        return sum(
            1
            for stock in stocks
            if str(stock.get("symbol", "")).upper() not in watchlist_symbols
        )

    def _log_us_rank_summary(
        self,
        market: str,
        rank_name: str,
        discovery_stocks: list[dict[str, Any]],
        watchlist_stocks: list[dict[str, Any]],
        returned_stocks: list[dict[str, Any]],
        *,
        mode: str,
    ) -> None:
        logger.info(
            "[{}] 미국장 {} 스캔: mode={}, discovery {}건, fallback seed {}건, returned {}건",
            market,
            rank_name,
            mode,
            len(discovery_stocks),
            len(watchlist_stocks),
            len(returned_stocks),
        )

    async def _rank_us_stocks(
        self,
        market: str,
        rank_name: str,
        discovery_stocks: list[dict[str, Any]],
        *,
        sort_key: str,
        reverse: bool,
    ) -> list[dict[str, Any]]:
        """미국장 랭킹 결과를 discovery 우선, fallback seed 보조로 정렬한다."""
        watchlist_stocks: list[dict[str, Any]] = []
        mode = "DISCOVERY"
        ranked_source = list(discovery_stocks)

        if not settings.US_DYNAMIC_DISCOVERY_ENABLED:
            watchlist_stocks = await self._build_watchlist_scan(market)
            ranked_source = list(watchlist_stocks)
            mode = "SEED_ONLY"
        elif not ranked_source:
            watchlist_stocks = await self._build_watchlist_scan(market)
            ranked_source = list(watchlist_stocks)
            mode = "FALLBACK_SEED"

        ranked_source.sort(key=lambda item: item.get(sort_key, 0), reverse=reverse)
        ranked = ranked_source[:_US_SCAN_RESULT_LIMIT]
        self._log_us_rank_summary(
            market,
            rank_name,
            discovery_stocks,
            watchlist_stocks,
            ranked,
            mode=mode,
        )
        return ranked

    def _normalize_stock_item(self, item: dict[str, Any], market: str) -> dict[str, Any]:
        """시장 스캔용 종목 데이터 정규화"""
        price_val = (
            self._to_float(self._pick_first(
                item,
                "stck_prpr",
                "last",
                "price",
                "ovrs_nmix_prpr",
                "clos",
                "base",
            ))
        )
        change_val = self._to_float(self._pick_first(
            item,
            "prdy_vrss",
            "diff",
            "t_xsgn",
            "ovrs_nmix_prdy_vrss",
            "change",
        ))
        change_rate_val = self._to_float(self._pick_first(
            item,
            "prdy_ctrt",
            "rate",
            "t_rate",
            "change_rate",
            "ovrs_nmix_prdy_ctrt",
        ))
        volume_val = self._to_int(self._pick_first(
            item,
            "acml_vol",
            "tvol",
            "volume",
            "avol",
            "ovrs_vol",
        ))
        symbol = str(self._pick_first(
            item,
            "symbol",
            "code",
            "symb",
            "pdno",
            "mksc_shrn_iscd",
            "stck_shrn_iscd",
            "ovrs_pdno",
            "rsym",
            default="",
        ))
        name = str(self._pick_first(
            item,
            "name",
            "hts_kor_isnm",
            "ovrs_item_name",
            "prdt_name",
            "item_name",
            default=symbol,
        ))
        item_market = str(self._pick_first(
            item,
            "market",
            "ovrs_excg_cd",
            default=market,
        ))

        return {
            **item,
            "symbol": symbol,
            "name": name,
            "market": normalize_market(item_market, default=market),
            "currency": market_currency(market),
            "price": price_val,
            "current_price": price_val,
            "change": change_val,
            "change_rate": change_rate_val,
            "volume": volume_val,
            "scan_source": str(item.get("scan_source", "DISCOVERY")).upper() or "DISCOVERY",
        }

    # === 편의 메서드: KIS MCP 도구 래퍼 ===

    async def get_current_price(self, symbol: str, market: str = "KRX") -> MCPResponse:
        """현재가 조회 (KIS 원본 키 → 정규화)"""
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            from trading.bithumb_client import bithumb_client

            return await bithumb_client.get_current_price(symbol, market=market_code)
        if is_domestic_market(market_code):
            from trading.kis_api import get_domestic_price

            result = await get_domestic_price(symbol)
            resp = MCPResponse(
                success=result.get("rt_cd") == "0",
                data=result,
                error=result.get("msg1") if result.get("rt_cd") != "0" else None,
            )
        else:
            from trading.kis_api import get_overseas_price

            resp = await self._call_overseas_quote(
                f"{market_code}:{symbol}:price",
                lambda: get_overseas_price(symbol, market_code),
            )
        if resp.success and resp.data:
            d = resp.data
            source = d.get("output", d) if isinstance(d.get("output"), dict) else d
            exchange_rate = await self._get_exchange_rate_to_krw(market_code)
            name = str(self._pick_first(
                source,
                "name",
                "hts_kor_isnm",
                "ovrs_item_name",
                "prdt_name",
                "item_name",
                default=symbol,
            ))
            price_val = (
                self._to_float(self._pick_first(
                    source,
                    "stck_prpr",
                    "last",
                    "price",
                    "ovrs_nmix_prpr",
                    "last_price",
                    "clos",
                ))
            )
            resp.data = {
                **d,
                "market": market_code,
                "name": name,
                "category": str(self._pick_first(source, "category", "prdt_type", default="")),
                "currency": market_currency(market_code),
                "exchange_rate_to_krw": exchange_rate,
                "price": price_val,
                "current_price": price_val,
                "price_krw": price_val * exchange_rate,
                "change": self._to_float(self._pick_first(
                    source, "prdy_vrss", "diff", "t_xsgn", "ovrs_nmix_prdy_vrss",
                )),
                "change_rate": self._to_float(self._pick_first(
                    source, "prdy_ctrt", "rate", "t_rate", "ovrs_nmix_prdy_ctrt",
                )),
                "volume": self._to_int(self._pick_first(
                    source, "acml_vol", "tvol", "ovrs_vol",
                )),
                "per": source.get("per", d.get("per", "N/A")),
                "pbr": source.get("pbr", d.get("pbr", "N/A")),
            }
        return resp

    async def get_account_balance(self, market: str = "KRX") -> MCPResponse:
        """계좌 잔고 조회"""
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            from trading.bithumb_client import bithumb_client

            return await bithumb_client.get_account_balance(market=market_code)
        if is_domestic_market(market_code):
            from trading.kis_api import get_domestic_balance

            result = await get_domestic_balance()
            return MCPResponse(
                success=result.get("rt_cd") == "0",
                data=result,
                error=result.get("msg1") if result.get("rt_cd") != "0" else None,
            )
        from trading.kis_api import get_overseas_balance, get_overseas_present_balance

        summary_response = await self._call_overseas_balance(
            f"{market_code}:inquire-present-balance",
            lambda: get_overseas_present_balance(market_code),
        )
        if not summary_response.success:
            error_message = (
                "inquire-present-balance 실패: "
                f"{summary_response.error or 'unknown'}"
            )
            logger.warning(
                "[{}] {} | {}",
                market_code,
                error_message,
                self._summarize_overseas_balance_payload(summary_response.data),
            )
            return MCPResponse(
                success=False,
                error=error_message,
                data={"summary": summary_response.data},
            )

        summary = summary_response.data or {}

        holdings_response = await self._call_overseas_balance(
            f"{market_code}:inquire-balance",
            lambda: get_overseas_balance(market_code),
        )
        if not holdings_response.success:
            error_message = (
                "inquire-balance 실패: "
                f"{holdings_response.error or 'unknown'}"
            )
            logger.warning(
                "[{}] {} | {}",
                market_code,
                error_message,
                self._summarize_overseas_balance_payload(holdings_response.data),
            )
            return MCPResponse(
                success=False,
                error=error_message,
                data={"summary": summary, "holdings": holdings_response.data},
            )

        holdings = holdings_response.data or {}

        # output2는 통화별 리스트 → 대상 통화(USD 등) 레코드를 찾아 정규화
        target_currency, currency_record = self._select_present_balance_currency_record(summary, market_code)

        output3 = summary.get("output3") or {}
        total_asset_value = None
        if isinstance(output3, dict):
            total_asset_value = self._pick_first(output3, "tot_asst_amt")

        incomplete_reasons: list[str] = []
        if not currency_record:
            incomplete_reasons.append(f"{target_currency} 통화 요약 없음")
        if not isinstance(output3, dict) or total_asset_value in (None, ""):
            incomplete_reasons.append("총자산 요약(output3.tot_asst_amt) 없음")

        if incomplete_reasons:
            error_message = "해외 잔고 응답 불완전: " + ", ".join(incomplete_reasons)
            logger.warning(
                "[{}] {} | summary={} holdings_count={}",
                market_code,
                error_message,
                self._summarize_overseas_balance_payload(summary),
                len(holdings.get("output1", [])) if isinstance(holdings.get("output1"), list) else 0,
            )
            return MCPResponse(
                success=False,
                error=error_message,
                data={"summary": summary, "holdings": holdings},
            )

        fx_rate = self._to_float(self._pick_first(
            currency_record,
            "frst_bltn_exrt",
            "bass_exrt",
        ), 1.0)

        # _parse_balance가 기대하는 단일 요약 레코드로 정규화
        normalized_summary = {
            "frcr_dncl_amt_2": currency_record.get("frcr_dncl_amt_2", "0"),
            "frcr_ord_psbl_amt1": currency_record.get("frcr_drwg_psbl_amt_1", "0"),
            "ovrs_stck_evlu_amt": currency_record.get("frcr_evlu_amt2", "0"),
            "tot_asst_amt": output3.get("tot_asst_amt", "0"),
            "tot_evlu_pfls_amt": output3.get("tot_evlu_pfls_amt", "0"),
            "evlu_pfls_rt": output3.get("evlu_erng_rt1", "0"),
            "frst_bltn_exrt": str(fx_rate),
        }

        combined = {
            "output1": holdings.get("output1", []),
            "output2": [normalized_summary],
            "market": market_code,
            "currency": target_currency,
            "exchange_rate_to_krw": fx_rate,
        }
        return MCPResponse(success=True, data=combined)

    async def get_orderable_amount(
        self,
        symbol: str,
        price: float,
        market: str = "NASDAQ",
    ) -> MCPResponse:
        """미국장 종목별 매수가능금액 조회"""
        market_code = normalize_market(market)
        if is_domestic_market(market_code):
            return MCPResponse(success=False, error="국내 시장은 종목별 주문가능금액 조회를 사용하지 않습니다")

        from trading.kis_api import get_overseas_psamount

        response = await self._call_overseas_balance(
            f"{market_code}:{symbol}:inquire-psamount",
            lambda: get_overseas_psamount(symbol, price, market_code),
        )
        if not response.success:
            return response

        data = response.data or {}
        source = self._first_record(data.get("output"))
        if not source:
            return MCPResponse(success=False, error="해외 매수가능금액 응답이 비어 있습니다", data=data)

        exchange_rate = self._to_float(
            self._pick_first(source, "exrt", default=None),
            0.0,
        )
        if exchange_rate <= 0:
            exchange_rate = await self._get_exchange_rate_to_krw(market_code)

        raw_frcr_ord_psbl_amt1 = self._to_float(source.get("frcr_ord_psbl_amt1"), 0.0)
        raw_ord_psbl_frcr_amt = self._to_float(source.get("ord_psbl_frcr_amt"), 0.0)
        foreign_orderable = raw_frcr_ord_psbl_amt1
        if foreign_orderable <= 0:
            foreign_orderable = raw_ord_psbl_frcr_amt

        raw_orderable_krw = self._to_float(source.get("ovrs_ord_psbl_amt"), 0.0)
        orderable_amount_krw = raw_orderable_krw
        if foreign_orderable > 0 and exchange_rate > 0:
            orderable_amount_krw = foreign_orderable * exchange_rate

        raw_ovrs_max_ord_psbl_qty = self._to_int(source.get("ovrs_max_ord_psbl_qty"), 0)
        raw_ord_psbl_qty = self._to_int(source.get("ord_psbl_qty"), 0)
        orderable_qty = raw_ovrs_max_ord_psbl_qty
        if orderable_qty <= 0:
            orderable_qty = raw_ord_psbl_qty
        if orderable_qty <= 0:
            orderable_qty = self._to_int(source.get("max_ord_psbl_qty"), 0)

        response.data = {
            **data,
            "market": market_code,
            "symbol": symbol,
            "currency": market_currency(market_code),
            "exchange_rate_to_krw": exchange_rate,
            "orderable_amount_source": "INQUIRE_PSAMOUNT",
            "orderable_amount_foreign": foreign_orderable,
            "orderable_amount_krw": round(orderable_amount_krw, 4),
            "orderable_qty": orderable_qty,
            "raw_ord_psbl_frcr_amt": raw_ord_psbl_frcr_amt,
            "raw_frcr_ord_psbl_amt1": raw_frcr_ord_psbl_amt1,
            "raw_ovrs_ord_psbl_amt": raw_orderable_krw,
            "raw_ord_psbl_qty": raw_ord_psbl_qty,
            "raw_ovrs_max_ord_psbl_qty": raw_ovrs_max_ord_psbl_qty,
        }
        return response

    async def get_daily_price(
        self, symbol: str, period: str = "D", count: int = 30, market: str = "KRX"
    ) -> MCPResponse:
        """일봉 데이터 조회 (KIS 원본 키 → 정규화)"""
        from datetime import datetime, timedelta

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")
        market_code = normalize_market(market)

        if is_crypto_market(market_code):
            from trading.bithumb_client import bithumb_client

            resp = await bithumb_client.get_daily_price(
                symbol, market=market_code, period=period, count=count,
            )
            if resp.success and resp.data:
                # bithumb_client는 "candles" 키로 반환 → "prices"로도 매핑
                resp.data["prices"] = resp.data.get("candles", [])
            return resp

        if is_domestic_market(market_code):
            from trading.kis_api import get_domestic_daily_price

            result = await get_domestic_daily_price(symbol)
            resp = MCPResponse(
                success=result.get("rt_cd") == "0",
                data=result,
                error=result.get("msg1") if result.get("rt_cd") != "0" else None,
            )
        else:
            from trading.kis_api import get_overseas_daily_price

            resp = await self._call_overseas_quote(
                f"{market_code}:{symbol}:daily",
                lambda: get_overseas_daily_price(symbol, market_code),
            )

        if resp.success and resp.data:
            raw_items = (
                self._extract_records(resp.data, "output2", "output", "prices")
                or self._extract_records(resp.data, "output1")
            )
            if isinstance(raw_items, list) and raw_items:
                prices = []
                for item in raw_items:
                    prices.append({
                        "date": self._pick_first(
                            item,
                            "stck_bsop_date",
                            "xymd",
                            "date",
                            default="",
                        ),
                        "open": self._to_float(self._pick_first(item, "stck_oprc", "open", "open_price")),
                        "high": self._to_float(self._pick_first(item, "stck_hgpr", "high", "high_price")),
                        "low": self._to_float(self._pick_first(item, "stck_lwpr", "low", "low_price")),
                        "close": self._to_float(self._pick_first(
                            item, "stck_clpr", "clos", "close", "ovrs_nmix_prpr",
                        )),
                        "volume": self._to_int(self._pick_first(item, "acml_vol", "tvol", "volume")),
                        "change": self._to_float(self._pick_first(item, "prdy_vrss", "diff")),
                        "change_rate": self._to_float(self._pick_first(item, "prdy_ctrt", "rate")),
                    })
                resp.data["prices"] = self._sort_series_records(prices, "date")
            else:
                logger.warning("[{}] 일봉 데이터 키 누락 — keys: {}", symbol,
                               list(resp.data.keys())[:10])
        return resp

    async def place_order(
        self, symbol: str, side: str, quantity: float,
        price: float | None = None, market: str = "KRX"
    ) -> MCPResponse:
        """주문 실행 (KIS 원본 키 → 정규화)"""
        order_type = "buy" if side == "BUY" else "sell"
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            from trading.bithumb_client import bithumb_client

            return await bithumb_client.place_order(
                symbol=symbol, side=side, quantity=quantity,
                price=price, market=market_code,
            )
        if is_domestic_market(market_code):
            from trading.kis_api import place_domestic_order

            result = await place_domestic_order(
                symbol=symbol,
                side=side,
                quantity=int(quantity),
                price=price,
            )
            resp = MCPResponse(
                success=result.get("rt_cd") == "0",
                data=result,
                error=result.get("msg1") if result.get("rt_cd") != "0" else None,
            )
        else:
            from trading.kis_api import place_overseas_order

            result = await place_overseas_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                market=market_code,
            )
            resp = MCPResponse(
                success=result.get("success", False),
                data=result,
                error=result.get("error"),
            )
        if resp.success and resp.data:
            d = resp.data
            if d.get("rt_cd") == "1":
                error_msg = d.get("msg1", "KIS 주문 실패")
                logger.warning("KIS 주문 거부: {}", error_msg)
                resp.success = False
                resp.error = error_msg
                return resp
            output = self._first_record(d.get("output")) or self._first_record(d.get("output1"))
            order_id = (
                d.get("ODNO") or d.get("odno")
                or d.get("ORDNO") or d.get("ordno")
                or output.get("ODNO") or output.get("odno")
                or d.get("order_id", "")
            )
            if not order_id:
                logger.warning("주문 응답에서 주문번호 미발견, 원본: {}", str(d)[:500])
            exchange_rate = await self._get_exchange_rate_to_krw(market_code)
            filled_price = self._to_float(
                d.get("exec_prc") or output.get("exec_prc")
                or d.get("filled_price")
            )
            resp.data = {
                **d,
                "order_id": order_id,
                "market": market_code,
                "currency": market_currency(market_code),
                "filled_quantity": self._to_int(
                    d.get("exec_qty") or output.get("exec_qty")
                    or d.get("filled_quantity")
                ),
                "filled_price": filled_price,
                "filled_price_krw": filled_price * exchange_rate,
                "exchange_rate_to_krw": exchange_rate,
            }
        return resp

    async def get_order(self, order_id: str, market: str = "KRX") -> MCPResponse:
        """단건 주문 조회"""
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            from trading.bithumb_client import bithumb_client

            return await bithumb_client.get_order(order_id=order_id, market=market_code)
        return MCPResponse(success=False, error=f"단건 주문 조회 미지원 시장: {market_code}")

    async def get_volume_rank(self, market: str = "KRX") -> MCPResponse:
        """거래량 상위 종목 조회"""
        from trading.kis_api import get_volume_rank

        market_code = normalize_market(market)
        if not is_domestic_market(market_code):
            discovery_stocks: list[dict[str, Any]] = []

            if settings.US_DYNAMIC_DISCOVERY_ENABLED:
                from trading.kis_api import get_overseas_volume_surge, get_overseas_trade_growth

                exchange = kis_exchange_code(market_code)
                result = await get_overseas_volume_surge(exchange)
                if not result.get("success"):
                    result = await get_overseas_trade_growth(exchange)
                if result.get("success"):
                    items = (
                        self._extract_records(result, "output1", "output", "dataframe1")
                        or self._extract_records(result, "output2", "dataframe2")
                    )
                    discovery_stocks = [self._normalize_stock_item(item, market_code) for item in items]

            ranked = await self._rank_us_stocks(
                market_code,
                "거래량순위",
                discovery_stocks,
                sort_key="volume",
                reverse=True,
            )
            return MCPResponse(success=bool(ranked), data={"stocks": ranked})

        market_code = "J" if market_code in ("KOSPI", "KOSDAQ", "KRX") else market_code
        try:
            result = await get_volume_rank(market=market_code)
            return MCPResponse(success=result.get("success", False), data=result)
        except Exception as e:
            logger.error("거래량순위 조회 실패: {}", str(e))
            return MCPResponse(success=False, error=str(e))

    async def get_minute_price(
        self, symbol: str, period: str = "5", market: str = "KRX"
    ) -> MCPResponse:
        """분봉 데이터 조회"""
        from trading.kis_api import get_minute_chart

        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            from trading.bithumb_client import bithumb_client

            resp = await bithumb_client.get_minute_price(
                symbol, market=market_code, interval=int(period), count=60,
            )
            if resp.success and resp.data:
                # bithumb_client는 "candles" 키로 반환 → "prices"로도 매핑
                candles = resp.data.get("candles", [])
                prices = [
                    {
                        "time": c.get("date", ""),
                        "open": c.get("open", 0),
                        "high": c.get("high", 0),
                        "low": c.get("low", 0),
                        "close": c.get("close", 0),
                        "volume": c.get("volume", 0),
                    }
                    for c in candles
                ]
                resp.data["prices"] = prices
            return resp

        if not is_domestic_market(market_code):
            from trading.kis_api import get_overseas_minute_chart

            response = await self._call_overseas_quote(
                f"{market_code}:{symbol}:minute:{period}",
                lambda: get_overseas_minute_chart(symbol, market_code, period=period),
            )
            if response.success and response.data:
                items = (
                    self._extract_records(response.data, "output2", "dataframe2")
                    or self._extract_records(response.data, "output1", "dataframe1")
                )
                prices = [
                    {
                        "time": self._pick_first(item, "xymd", "time", default=""),
                        "open": self._to_float(self._pick_first(item, "open", "stck_oprc")),
                        "high": self._to_float(self._pick_first(item, "high", "stck_hgpr")),
                        "low": self._to_float(self._pick_first(item, "low", "stck_lwpr")),
                        "close": self._to_float(self._pick_first(item, "clos", "close", "stck_prpr")),
                        "volume": self._to_int(self._pick_first(item, "tvol", "volume", "cntg_vol")),
                    }
                    for item in items
                ]
                response.data = {
                    **response.data,
                    "prices": self._sort_series_records(prices, "time"),
                }
            return response

        try:
            result = await get_minute_chart(symbol, period)
            return MCPResponse(success=result.get("success", False), data=result)
        except Exception as e:
            logger.error("분봉 조회 실패 ({}): {}", symbol, str(e))
            return MCPResponse(success=False, error=str(e))

    async def get_fluctuation_rank(self, market: str = "KRX", sort: str = "top") -> MCPResponse:
        """등락률 상위/하위 종목 조회"""
        from trading.kis_api import get_fluctuation_rank

        market_code = normalize_market(market)
        if not is_domestic_market(market_code):
            discovery_stocks: list[dict[str, Any]] = []

            if settings.US_DYNAMIC_DISCOVERY_ENABLED:
                from trading.kis_api import get_overseas_price_fluct

                exchange = kis_exchange_code(market_code)
                result = await get_overseas_price_fluct(exchange)
                if result.get("success"):
                    items = (
                        self._extract_records(result, "output1", "output", "dataframe1")
                        or self._extract_records(result, "output2", "dataframe2")
                    )
                    discovery_stocks = [self._normalize_stock_item(item, market_code) for item in items]

            ranked = await self._rank_us_stocks(
                market_code,
                "등락률상위" if sort != "bottom" else "등락률하위",
                discovery_stocks,
                sort_key="change_rate",
                reverse=(sort != "bottom"),
            )
            return MCPResponse(success=bool(ranked), data={"stocks": ranked})

        market_code = "J" if market_code in ("KOSPI", "KOSDAQ", "KRX") else market_code
        try:
            result = await get_fluctuation_rank(sort=sort, market=market_code)
            return MCPResponse(success=result.get("success", False), data=result)
        except Exception as e:
            logger.error("등락률순위 조회 실패: {}", str(e))
            return MCPResponse(success=False, error=str(e))

    async def get_stock_ask(self, symbol: str, market: str = "KRX") -> MCPResponse:
        """호가 조회"""
        market_code = normalize_market(market)
        if is_domestic_market(market_code):
            from trading.kis_api import get_domestic_asking_price

            result = await get_domestic_asking_price(symbol)
            return MCPResponse(
                success=result.get("rt_cd") == "0",
                data=result,
                error=result.get("msg1") if result.get("rt_cd") != "0" else None,
            )
        from trading.kis_api import get_overseas_asking_price

        result = await get_overseas_asking_price(symbol, kis_exchange_code(market_code))
        return MCPResponse(
            success=result.get("rt_cd") == "0",
            data=result,
            error=result.get("msg1") if result.get("rt_cd") != "0" else None,
        )

    async def get_order_list(self, market: str = "KRX") -> MCPResponse:
        """주문 체결/미체결 내역 조회"""
        from datetime import datetime

        today = datetime.now().strftime("%Y%m%d")
        market_code = normalize_market(market)

        if is_crypto_market(market_code):
            # 빗썸은 별도 미체결 내역 API가 없으므로 빈 리스트 반환
            return MCPResponse(success=True, data={"output": []})

        if is_domestic_market(market_code):
            from trading.kis_api import get_domestic_order_list

            result = await get_domestic_order_list(today, today)
            return MCPResponse(
                success=result.get("rt_cd") == "0",
                data=result,
                error=result.get("msg1") if result.get("rt_cd") != "0" else None,
            )

        from trading.kis_api import get_overseas_order_list

        result = await get_overseas_order_list(market_code)
        response = MCPResponse(
            success=result.get("success", False),
            data=result,
            error=result.get("error"),
        )
        if response.success and response.data:
            records = (
                self._extract_records(response.data, "output", "output1", "dataframe", "dataframe1")
                or self._extract_records(response.data, "output2", "dataframe2")
            )
            exchange_rate = self._to_float(
                response.data.get("exchange_rate_to_krw"),
                0.0,
            )
            if exchange_rate <= 0:
                exchange_rate = await self._get_exchange_rate_to_krw(market_code)
            response.data = {
                **response.data,
                "output": [
                    self._normalize_overseas_order_record(record, market_code, exchange_rate)
                    for record in records
                    if isinstance(record, dict)
                ],
                "exchange_rate_to_krw": exchange_rate,
            }
        return response


# 싱글톤 MCP 클라이언트
mcp_client = MCPClient()
