"""빗썸 거래소 REST API 클라이언트.

BrokerClient + MarketDataProvider Protocol을 만족하는 빗썸 v2.1 구현체.
인증: JWT Bearer (PyJWT + HMAC256), 해시: SHA-512.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from loguru import logger

from core.config import settings
from trading.models import MCPResponse

# ---------------------------------------------------------------------------
# Rate limit 설정
# ---------------------------------------------------------------------------
_PUBLIC_RATE_LIMIT_PER_SEC = 10
_PRIVATE_RATE_LIMIT_PER_SEC = 5
_RATE_LIMIT_WINDOW = 1.0  # 초
_MAX_CANDLE_COUNT = 200  # 빗썸 캔들 API 최대 조회 건수
_TRADEABLE_SYMBOL_CACHE_TTL = 300.0  # 초

# 분봉 단위 매핑: 내부 interval(분) → 빗썸 API path unit
_MINUTE_UNIT_MAP: dict[int, int] = {
    1: 1, 3: 3, 5: 5, 10: 10, 15: 15, 30: 30, 60: 60,
}

# 일/주/월봉 period 매핑
_PERIOD_PATH_MAP: dict[str, str] = {
    "D": "days",
    "W": "weeks",
    "M": "months",
}


def _to_market_code(symbol: str) -> str:
    """내부 심볼 ``"BTC"`` → 빗썸 마켓 코드 ``"KRW-BTC"``."""
    sym = symbol.upper().strip()
    if sym.startswith("KRW-"):
        return sym
    return f"KRW-{sym}"


def _symbol_from_market_code(market_code: str) -> str:
    """빗썸 마켓 코드 ``"KRW-BTC"`` → 내부 심볼 ``"BTC"``."""
    if "-" in market_code:
        return market_code.split("-", 1)[1].upper()
    return market_code.upper()


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """안전한 Decimal 변환."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    """안전한 float 변환."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════════════════
# BithumbClient
# ═══════════════════════════════════════════════════════════════════════════

class BithumbClient:
    """빗썸 거래소 REST API 클라이언트.

    ``BrokerClient`` + ``MarketDataProvider`` Protocol을 만족한다.
    """

    BASE_URL = "https://api.bithumb.com"

    # ------------------------------------------------------------------
    # 초기화
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._api_key: str = settings.BITHUMB_API_KEY
        self._api_secret: str = settings.BITHUMB_API_SECRET

        self._client: httpx.AsyncClient | None = None
        self._tradable_krw_symbols_cache: set[str] = set()
        self._tradable_krw_symbols_cached_at: float | None = None

        # Rate limiter: 슬라이딩 윈도우 타임스탬프 + 세마포어
        self._public_timestamps: list[float] = []
        self._public_lock = asyncio.Lock()
        self._public_semaphore = asyncio.Semaphore(_PUBLIC_RATE_LIMIT_PER_SEC)

        self._private_timestamps: list[float] = []
        self._private_lock = asyncio.Lock()
        self._private_semaphore = asyncio.Semaphore(_PRIVATE_RATE_LIMIT_PER_SEC)

    # ------------------------------------------------------------------
    # HTTP 클라이언트 수명
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={"accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # JWT 인증
    # ------------------------------------------------------------------

    def _build_jwt(self, query_string: str = "") -> str:
        """Private API 호출용 JWT 토큰 생성.

        - 파라미터가 있으면 SHA-512 해시를 포함한다.
        - Algorithm: HS256 (HMAC-SHA256) — 빗썸 공식 사양.
        """
        payload: dict[str, Any] = {
            "access_key": self._api_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": round(time.time() * 1000),
        }
        if query_string:
            query_hash = hashlib.sha512(query_string.encode("utf-8")).hexdigest()
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"

        return jwt.encode(payload, self._api_secret, algorithm="HS256")

    # ------------------------------------------------------------------
    # Rate limiting (MCPClient _call_timestamps 패턴)
    # ------------------------------------------------------------------

    async def _rate_limit_public(self) -> None:
        async with self._public_lock:
            now = time.monotonic()
            self._public_timestamps = [
                t for t in self._public_timestamps
                if now - t < _RATE_LIMIT_WINDOW
            ]
            if len(self._public_timestamps) >= _PUBLIC_RATE_LIMIT_PER_SEC:
                wait = _RATE_LIMIT_WINDOW - (now - self._public_timestamps[0]) + 0.05
                if wait > 0:
                    logger.debug("빗썸 public rate limit 대기: {:.2f}초", wait)
                    await asyncio.sleep(wait)
            self._public_timestamps.append(time.monotonic())

    async def _rate_limit_private(self) -> None:
        async with self._private_lock:
            now = time.monotonic()
            self._private_timestamps = [
                t for t in self._private_timestamps
                if now - t < _RATE_LIMIT_WINDOW
            ]
            if len(self._private_timestamps) >= _PRIVATE_RATE_LIMIT_PER_SEC:
                wait = _RATE_LIMIT_WINDOW - (now - self._private_timestamps[0]) + 0.05
                if wait > 0:
                    logger.debug("빗썸 private rate limit 대기: {:.2f}초", wait)
                    await asyncio.sleep(wait)
            self._private_timestamps.append(time.monotonic())

    # ------------------------------------------------------------------
    # 저수준 HTTP 호출
    # ------------------------------------------------------------------

    @staticmethod
    def _response_preview(text: str, limit: int = 200) -> str:
        """응답 본문 일부를 한 줄로 축약"""
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[:limit] + "..."

    async def _public_get(self, path: str, params: dict[str, Any] | None = None) -> MCPResponse:
        """Public GET 요청 (인증 불필요)."""
        async with self._public_semaphore:
            await self._rate_limit_public()
            try:
                client = await self._ensure_client()
                resp = await client.get(path, params=params)
                content_type = resp.headers.get("content-type", "")
                body_preview = self._response_preview(resp.text)
                if resp.status_code >= 400:
                    error = (
                        f"HTTP {resp.status_code} | content-type={content_type or '-'}"
                        f" | body={body_preview or '<empty>'}"
                    )
                    logger.error(
                        "빗썸 public GET 오류 [{}]: params={} {}",
                        path,
                        params or {},
                        error,
                    )
                    return MCPResponse(
                        success=False,
                        error=error,
                        data={
                            "status_code": resp.status_code,
                            "content_type": content_type or None,
                            "body_preview": body_preview or None,
                        },
                    )
                try:
                    data = resp.json()
                except ValueError as exc:
                    error = (
                        f"non_json_response: HTTP {resp.status_code}"
                        f" | content-type={content_type or '-'}"
                        f" | body={body_preview or '<empty>'}"
                    )
                    logger.error(
                        "빗썸 public GET 오류 [{}]: params={} {} ({})",
                        path,
                        params or {},
                        error,
                        exc,
                    )
                    return MCPResponse(
                        success=False,
                        error=error,
                        data={
                            "status_code": resp.status_code,
                            "content_type": content_type or None,
                            "body_preview": body_preview or None,
                        },
                    )
                return self._parse_response(data)
            except Exception as e:
                logger.error("빗썸 public GET 오류 [{}]: params={} {}", path, params or {}, e)
                return MCPResponse(success=False, error=str(e))

    async def _private_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> MCPResponse:
        """Private API 요청 (JWT 인증)."""
        async with self._private_semaphore:
            await self._rate_limit_private()
            try:
                # query_string: GET/DELETE 파라미터 또는 POST body 기준
                if body:
                    query_string = urlencode(body)
                elif params:
                    query_string = urlencode(params)
                else:
                    query_string = ""

                token = self._build_jwt(query_string)
                headers = {"Authorization": f"Bearer {token}"}

                client = await self._ensure_client()

                if method == "GET":
                    resp = await client.get(path, params=params, headers=headers)
                elif method == "POST":
                    headers["Content-Type"] = "application/json"
                    resp = await client.post(path, json=body, headers=headers)
                elif method == "DELETE":
                    resp = await client.delete(path, params=params, headers=headers)
                else:
                    return MCPResponse(success=False, error=f"지원하지 않는 HTTP 메서드: {method}")

                data = resp.json()
                return self._parse_response(data)
            except Exception as e:
                logger.error("빗썸 private {} 오류 [{}]: {}", method, path, e)
                return MCPResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # 응답 파싱
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(data: Any) -> MCPResponse:
        """빗썸 API 응답을 MCPResponse로 변환.

        빗썸 v2 API 규칙:
        - 성공: 배열 또는 dict(error 키 없음) 반환
        - 실패: ``{"error": {"name": 400, "message": "..."}}``
        """
        # 에러 응답 체크
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            name = err.get("name", "") if isinstance(err, dict) else ""
            return MCPResponse(
                success=False,
                error=f"[{name}] {msg}" if name else msg,
                data=data,
            )

        # 정상 응답: 배열이면 {"items": [...]} 으로 래핑
        if isinstance(data, list):
            return MCPResponse(success=True, data={"items": data})
        if isinstance(data, dict):
            return MCPResponse(success=True, data=data)

        return MCPResponse(success=False, error=f"예상치 못한 응답 형식: {type(data).__name__}")

    # ------------------------------------------------------------------
    # 캔들 응답 정규화
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_candle(raw: dict[str, Any]) -> dict[str, Any]:
        """빗썸 캔들 객체를 프로젝트 표준 dict로 변환."""
        return {
            "date": raw.get("candle_date_time_kst", raw.get("candle_date_time_utc", "")),
            "open": _to_float(raw.get("opening_price")),
            "high": _to_float(raw.get("high_price")),
            "low": _to_float(raw.get("low_price")),
            "close": _to_float(raw.get("trade_price")),
            "volume": _to_float(raw.get("candle_acc_trade_volume")),
            "trade_value": _to_float(raw.get("candle_acc_trade_price")),
            "timestamp": raw.get("timestamp"),
        }

    @staticmethod
    def _normalize_ticker(raw: dict[str, Any]) -> dict[str, Any]:
        """빗썸 ticker 객체를 프로젝트 표준 dict로 변환."""
        change_direction = str(raw.get("change", "") or "").upper()
        change_price = _to_float(raw.get("change_price"))
        signed_change_price = _to_float(raw.get("signed_change_price"))
        if signed_change_price == 0.0 and change_price > 0:
            if change_direction == "FALL":
                signed_change_price = -change_price
            else:
                signed_change_price = change_price

        return {
            "market": raw.get("market", ""),
            "symbol": _symbol_from_market_code(raw.get("market", "")),
            "price": _to_float(raw.get("trade_price")),
            "opening_price": _to_float(raw.get("opening_price")),
            "high_price": _to_float(raw.get("high_price")),
            "low_price": _to_float(raw.get("low_price")),
            "prev_closing_price": _to_float(raw.get("prev_closing_price")),
            "change": signed_change_price,
            "change_direction": change_direction,
            "change_price": change_price,
            "signed_change_price": signed_change_price,
            "change_rate": _to_float(raw.get("signed_change_rate")),
            "volume": _to_float(raw.get("acc_trade_volume_24h")),
            "trade_value": _to_float(raw.get("acc_trade_price_24h")),
            "trade_volume": _to_float(raw.get("trade_volume")),
            "highest_52_week_price": _to_float(raw.get("highest_52_week_price")),
            "lowest_52_week_price": _to_float(raw.get("lowest_52_week_price")),
            "timestamp": raw.get("timestamp"),
        }

    # ===================================================================
    # 내부 헬퍼
    # ===================================================================

    async def _fetch_coin_prices(self, symbols: list[str]) -> dict[str, float]:
        """여러 코인의 현재가를 벌크 조회 → {symbol: price} dict"""
        if not symbols:
            return {}
        market_codes = ",".join(_to_market_code(s) for s in symbols)
        resp = await self._public_get("/v1/ticker", params={"markets": market_codes})
        if not resp.success or not resp.data:
            return {}
        prices: dict[str, float] = {}
        for item in resp.data.get("items", []):
            raw_market = str(item.get("market", ""))
            symbol = _symbol_from_market_code(raw_market)
            price = _to_float(item.get("trade_price"))
            if symbol and price > 0:
                prices[symbol] = price
        return prices

    def _get_cached_tradable_krw_symbols(self, *, allow_stale: bool = False) -> set[str] | None:
        """캐시된 KRW 거래 가능 심볼 집합을 반환한다."""
        if not self._tradable_krw_symbols_cache:
            return None
        if allow_stale or self._tradable_krw_symbols_cached_at is None:
            return set(self._tradable_krw_symbols_cache)

        age = time.monotonic() - self._tradable_krw_symbols_cached_at
        if age <= _TRADEABLE_SYMBOL_CACHE_TTL:
            return set(self._tradable_krw_symbols_cache)
        return None

    def _set_tradable_krw_symbols_cache(self, symbols: set[str]) -> None:
        """KRW 거래 가능 심볼 캐시를 갱신한다."""
        self._tradable_krw_symbols_cache = set(symbols)
        self._tradable_krw_symbols_cached_at = time.monotonic()

    async def _get_tradable_krw_symbols(self) -> tuple[set[str] | None, str]:
        """실제 KRW 마켓에서 거래 가능한 심볼 집합을 반환한다."""
        cached = self._get_cached_tradable_krw_symbols()
        if cached is not None:
            return cached, "cache"

        markets_resp = await self._public_get("/v1/market/all")
        if markets_resp.success and markets_resp.data:
            symbols = {
                _symbol_from_market_code(str(item.get("market", "")))
                for item in markets_resp.data.get("items", [])
                if isinstance(item, dict) and str(item.get("market", "")).startswith("KRW-")
            }
            if symbols:
                self._set_tradable_krw_symbols_cache(symbols)
                return symbols, "live"

        stale_cached = self._get_cached_tradable_krw_symbols(allow_stale=True)
        if stale_cached is not None:
            logger.warning(
                "빗썸 KRW 마켓 코드 조회 실패 → 캐시 사용: {}",
                markets_resp.error or "unknown error",
            )
            return stale_cached, "stale_cache"

        logger.warning(
            "빗썸 KRW 마켓 코드 조회 실패 → degraded holdings filter 사용: {}",
            markets_resp.error or "unknown error",
        )
        return None, "degraded"

    async def _prepare_tradeable_account_assets(
        self,
        items: list[dict[str, Any]],
        *,
        log_exclusions: bool = False,
    ) -> tuple[Decimal, Decimal, list[dict[str, Any]], dict[str, float]]:
        """계좌 응답에서 KRW 잔고와 거래 가능 코인 보유분만 추린다."""
        krw_balance = Decimal("0")
        krw_locked = Decimal("0")
        raw_coin_data: list[dict[str, Any]] = []

        for item in items:
            currency = str(item.get("currency", "")).upper()
            balance = _to_decimal(item.get("balance"))
            locked = _to_decimal(item.get("locked"))
            avg_buy_price = _to_decimal(item.get("avg_buy_price"))

            if currency == "KRW":
                krw_balance = balance
                krw_locked = locked
                continue

            total_qty = balance + locked
            if total_qty <= 0:
                continue

            raw_coin_data.append({
                "currency": currency,
                "balance": balance,
                "locked": locked,
                "total_qty": total_qty,
                "avg_buy_price": avg_buy_price,
            })

        if not raw_coin_data:
            return krw_balance, krw_locked, [], {}

        tradable_symbols, source = await self._get_tradable_krw_symbols()
        filtered_coin_data: list[dict[str, Any]] = []
        excluded_coin_data: list[dict[str, Any]] = []
        prices: dict[str, float] = {}

        if tradable_symbols is not None:
            for coin in raw_coin_data:
                if coin["currency"] in tradable_symbols:
                    filtered_coin_data.append(coin)
                else:
                    excluded_coin_data.append({**coin, "reason": "no_krw_market"})

            if filtered_coin_data:
                prices = await self._fetch_coin_prices([coin["currency"] for coin in filtered_coin_data])
        else:
            prices = await self._fetch_coin_prices([coin["currency"] for coin in raw_coin_data])
            for coin in raw_coin_data:
                current_price = prices.get(coin["currency"], 0.0)
                if current_price > 0 or coin["avg_buy_price"] > 0:
                    filtered_coin_data.append(coin)
                else:
                    excluded_coin_data.append({**coin, "reason": "no_price_and_zero_cost"})

            filtered_symbols = {coin["currency"] for coin in filtered_coin_data}
            prices = {
                symbol: price
                for symbol, price in prices.items()
                if symbol in filtered_symbols
            }

        if excluded_coin_data and log_exclusions:
            preview = ", ".join(
                f"{coin['currency']} qty={float(coin['total_qty']):g} avg={float(coin['avg_buy_price']):g} reason={coin['reason']}"
                for coin in excluded_coin_data[:5]
            )
            suffix = ""
            if len(excluded_coin_data) > 5:
                suffix = f" (+{len(excluded_coin_data) - 5} more)"
            logger.warning(
                "빗썸 비거래성 자산 제외 (source={}): {}{}",
                source,
                preview,
                suffix,
            )

        return krw_balance, krw_locked, filtered_coin_data, prices

    # ===================================================================
    # BrokerClient Protocol 구현
    # ===================================================================

    async def get_current_price(self, symbol: str, market: str = "") -> MCPResponse:
        """현재가 조회 — ``GET /v1/ticker?markets=KRW-{symbol}``"""
        market_code = _to_market_code(symbol)
        resp = await self._public_get("/v1/ticker", params={"markets": market_code})
        if not resp.success or not resp.data:
            return resp

        items = resp.data.get("items", [])
        if not items:
            return MCPResponse(success=False, error=f"현재가 데이터 없음: {symbol}")

        normalized = self._normalize_ticker(items[0])
        return MCPResponse(success=True, data=normalized)

    async def get_daily_price(
        self,
        symbol: str,
        market: str = "",
        *,
        period: str = "D",
        count: int = 60,
    ) -> MCPResponse:
        """일봉/주봉/월봉 조회 — ``GET /v1/candles/{days|weeks|months}``"""
        path_suffix = _PERIOD_PATH_MAP.get(period.upper(), "days")
        market_code = _to_market_code(symbol)
        effective_count = min(count, _MAX_CANDLE_COUNT)

        resp = await self._public_get(
            f"/v1/candles/{path_suffix}",
            params={"market": market_code, "count": effective_count},
        )
        if not resp.success or not resp.data:
            return resp

        raw_items = resp.data.get("items", [])
        # 빗썸 캔들 응답은 최신순 → oldest-first 정렬
        candles = [self._normalize_candle(c) for c in reversed(raw_items)]
        return MCPResponse(success=True, data={"candles": candles, "count": len(candles)})

    async def get_minute_price(
        self,
        symbol: str,
        market: str = "",
        *,
        interval: int = 5,
        count: int = 60,
    ) -> MCPResponse:
        """분봉 조회 — ``GET /v1/candles/minutes/{unit}``"""
        unit = _MINUTE_UNIT_MAP.get(interval, interval)
        market_code = _to_market_code(symbol)
        effective_count = min(count, _MAX_CANDLE_COUNT)

        resp = await self._public_get(
            f"/v1/candles/minutes/{unit}",
            params={"market": market_code, "count": effective_count},
        )
        if not resp.success or not resp.data:
            return resp

        raw_items = resp.data.get("items", [])
        # 최신순 → oldest-first 정렬
        candles = [self._normalize_candle(c) for c in reversed(raw_items)]
        return MCPResponse(success=True, data={"candles": candles, "count": len(candles)})

    async def get_account_balance(self, market: str = "") -> MCPResponse:
        """계좌 잔고 조회 — ``GET /v1/accounts``

        빗썸은 전체 계좌를 한 번에 반환한다.
        KRW balance + 모든 코인 평가액을 합산하여 표준 형태로 정규화.
        """
        resp = await self._private_request("GET", "/v1/accounts")
        if not resp.success or not resp.data:
            return resp

        items: list[dict[str, Any]] = resp.data.get("items", [])
        if not items:
            return MCPResponse(success=False, error="계좌 데이터 없음")

        krw_balance, krw_locked, coin_data, prices = await self._prepare_tradeable_account_assets(
            items,
            log_exclusions=True,
        )
        total_coin_value = Decimal("0")
        total_pnl = Decimal("0")
        holdings: list[dict[str, Any]] = []

        for cd in coin_data:
            sym = cd["currency"]
            current_price = Decimal(str(prices.get(sym, 0)))
            if current_price > 0:
                coin_value = cd["total_qty"] * current_price
            else:
                coin_value = cd["total_qty"] * cd["avg_buy_price"]  # 현재가 조회 실패 시 폴백

            total_coin_value += coin_value
            cost_basis = cd["total_qty"] * cd["avg_buy_price"]
            total_pnl += coin_value - cost_basis

            holdings.append({
                "currency": sym,
                "balance": float(cd["balance"]),
                "locked": float(cd["locked"]),
                "avg_buy_price": float(cd["avg_buy_price"]),
                "current_price": float(current_price),
                "coin_value_krw": float(coin_value),
            })

        total_asset = krw_balance + krw_locked + total_coin_value
        cash = krw_balance
        cost_total = total_asset - total_pnl
        pnl_rate = float(total_pnl / cost_total * 100) if cost_total > 0 else 0.0

        return MCPResponse(
            success=True,
            data={
                "total_asset": float(total_asset),
                "cash": float(cash),
                "locked_krw": float(krw_locked),
                "stock_value": float(total_coin_value),
                "total_pnl": float(total_pnl),
                "total_pnl_rate": pnl_rate,
                "market": "BITHUMB",
                "currency": "KRW",
                "holdings_count": len(holdings),
                "holdings_summary": holdings,
            },
        )

    async def get_holdings(self, market: str = "") -> MCPResponse:
        """보유 종목 목록 — ``GET /v1/accounts`` 에서 코인만 추출 + 벌크 현재가 조회."""
        resp = await self._private_request("GET", "/v1/accounts")
        if not resp.success or not resp.data:
            return resp

        items: list[dict[str, Any]] = resp.data.get("items", [])
        _, _, coin_list, prices = await self._prepare_tradeable_account_assets(
            items,
            log_exclusions=True,
        )

        # 3단계: PnL 계산
        holdings: list[dict[str, Any]] = []
        for cd in coin_list:
            sym = cd["currency"]
            qty = float(cd["total_qty"])
            avg = float(cd["avg_buy_price"])
            cur_price = prices.get(sym, 0.0)
            pnl = (cur_price - avg) * qty if cur_price > 0 and avg > 0 else 0.0
            pnl_rate = ((cur_price / avg) - 1) * 100 if avg > 0 and cur_price > 0 else 0.0

            holdings.append({
                "symbol": sym,
                "name": sym,
                "market": "BITHUMB",
                "currency": "KRW",
                "quantity": qty,
                "available_quantity": float(cd["balance"]),
                "locked_quantity": float(cd["locked"]),
                "avg_buy_price": avg,
                "current_price": cur_price,
                "pnl": round(pnl, 2),
                "pnl_rate": round(pnl_rate, 2),
                "exchange_rate_to_krw": 1.0,
            })

        return MCPResponse(
            success=True,
            data={"holdings": holdings, "count": len(holdings)},
        )

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None = None,
        market: str = "",
    ) -> MCPResponse:
        """주문 실행 — ``POST /v1/orders``

        Parameters
        ----------
        side : "BUY" 또는 "SELL"
        price : None이면 시장가 주문
        """
        market_code = _to_market_code(symbol)
        bithumb_side = "bid" if side.upper() == "BUY" else "ask"

        body: dict[str, Any] = {
            "market": market_code,
            "side": bithumb_side,
        }

        if price is not None:
            # 지정가 주문
            body["ord_type"] = "limit"
            body["volume"] = str(Decimal(str(quantity)))
            body["price"] = str(Decimal(str(price)).quantize(Decimal("1"), rounding=ROUND_DOWN))
        else:
            # 시장가 주문
            if bithumb_side == "bid":
                # 시장가 매수: ord_type="price", price=총 투자금액(KRW)
                body["ord_type"] = "price"
                body["price"] = str(Decimal(str(quantity)))  # quantity를 총 금액으로 사용
            else:
                # 시장가 매도: ord_type="market", volume=수량
                body["ord_type"] = "market"
                body["volume"] = str(Decimal(str(quantity)))

        logger.info(
            "빗썸 주문 요청: {} {} {} (price={})",
            market_code,
            side,
            quantity,
            price,
        )

        resp = await self._private_request("POST", "/v1/orders", body=body)
        if not resp.success:
            logger.warning("빗썸 주문 실패: {}", resp.error)
            return resp

        data = resp.data or {}
        return MCPResponse(
            success=True,
            data={
                "order_id": data.get("uuid", ""),
                "side": side.upper(),
                "market": "BITHUMB",
                "market_pair": market_code,
                "ord_type": data.get("ord_type", ""),
                "price": data.get("price", ""),
                "state": data.get("state", ""),
                "volume": data.get("volume", ""),
                "remaining_volume": data.get("remaining_volume", ""),
                "executed_volume": data.get("executed_volume", ""),
                "trades_count": data.get("trades_count", 0),
                "created_at": data.get("created_at", ""),
            },
        )

    @staticmethod
    def _normalize_order_payload(data: dict[str, Any]) -> dict[str, Any]:
        """빗썸 주문 응답을 공통 order dict로 정규화"""
        market_pair = str(data.get("market", "") or "")
        symbol = _symbol_from_market_code(market_pair) if market_pair else ""
        order_price = _to_float(data.get("price"))
        ord_type = str(data.get("ord_type", "") or "").lower()
        filled_qty = _to_float(data.get("executed_volume"))
        remaining_qty = _to_float(data.get("remaining_volume"))
        order_qty = _to_float(data.get("volume"))
        normalized_side = str(data.get("side", "") or "").lower()

        return {
            "order_id": data.get("uuid", ""),
            "client_order_id": data.get("client_order_id", ""),
            "market": "BITHUMB",
            "market_pair": market_pair,
            "symbol": symbol,
            "name": symbol,
            "side": "BUY" if normalized_side == "bid" else "SELL" if normalized_side == "ask" else normalized_side.upper(),
            "status": str(data.get("state", "") or ""),
            "state": str(data.get("state", "") or ""),
            "ord_type": ord_type,
            "order_price": order_price,
            "price": order_price,
            "order_qty": order_qty,
            "volume": order_qty,
            "filled_qty": filled_qty,
            "filled_quantity": filled_qty,
            "executed_volume": filled_qty,
            "remaining_qty": remaining_qty,
            "remaining_volume": remaining_qty,
            "filled_price": order_price if ord_type == "limit" else 0.0,
            "currency": "KRW",
            "exchange_rate_to_krw": 1.0,
            "paid_fee": _to_float(data.get("paid_fee")),
            "reserved_fee": _to_float(data.get("reserved_fee")),
            "remaining_fee": _to_float(data.get("remaining_fee")),
            "locked": _to_float(data.get("locked")),
            "trades_count": int(data.get("trades_count") or 0),
            "created_at": data.get("created_at", ""),
        }

    async def cancel_order(self, order_id: str, market: str = "", **kwargs: Any) -> MCPResponse:
        """주문 취소 — ``DELETE /v1/order?uuid={order_id}``"""
        resp = await self._private_request(
            "DELETE",
            "/v1/order",
            params={"uuid": order_id},
        )
        if not resp.success:
            logger.warning("빗썸 주문 취소 실패 ({}): {}", order_id, resp.error)
            return resp

        data = resp.data or {}
        return MCPResponse(
            success=True,
            data={
                "order_id": data.get("uuid", order_id),
                "state": data.get("state", ""),
                "side": data.get("side", ""),
                "market": "BITHUMB",
                "market_pair": data.get("market", ""),
                "remaining_volume": data.get("remaining_volume", ""),
                "executed_volume": data.get("executed_volume", ""),
            },
        )

    async def get_order(self, order_id: str, market: str = "", **kwargs: Any) -> MCPResponse:
        """개별 주문 조회 — ``GET /v1/order?uuid={order_id}``"""
        resp = await self._private_request(
            "GET",
            "/v1/order",
            params={"uuid": order_id},
        )
        if not resp.success:
            logger.warning("빗썸 개별 주문 조회 실패 ({}): {}", order_id, resp.error)
            return resp

        data = resp.data or {}
        return MCPResponse(
            success=True,
            data=self._normalize_order_payload(data),
        )

    async def get_orderable_amount(
        self,
        symbol: str,
        price: float,
        market: str = "",
    ) -> MCPResponse:
        """주문 가능 금액/수량 — ``GET /v1/orders/chance``"""
        market_code = _to_market_code(symbol)
        resp = await self._private_request(
            "GET",
            "/v1/orders/chance",
            params={"market": market_code},
        )
        if not resp.success or not resp.data:
            return resp

        data = resp.data
        bid_account = data.get("bid_account", {})
        ask_account = data.get("ask_account", {})
        bid_fee = _to_decimal(data.get("bid_fee", "0"))

        available_krw = _to_decimal(bid_account.get("balance", "0"))
        available_coin = _to_decimal(ask_account.get("balance", "0"))

        # 매수 가능 수량 = KRW 잔고 / (가격 * (1 + 수수료율))
        d_price = _to_decimal(price) if price > 0 else Decimal("1")
        fee_multiplier = Decimal("1") + bid_fee
        orderable_qty = (available_krw / (d_price * fee_multiplier)).quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN,
        )

        return MCPResponse(
            success=True,
            data={
                "symbol": symbol,
                "market": market_code,
                "available_krw": float(available_krw),
                "available_coin": float(available_coin),
                "orderable_quantity": float(orderable_qty),
                "price": price,
                "bid_fee_rate": float(bid_fee),
                "ask_fee_rate": _to_float(data.get("ask_fee")),
            },
        )

    # ===================================================================
    # MarketDataProvider Protocol 구현
    # ===================================================================

    async def get_market_overview(self, market: str = "") -> MCPResponse:
        """전체 시장 종목 요약 — KRW 마켓 전체 ticker 조회.

        1) ``GET /v1/market/all`` 로 KRW 마켓 코드 목록 확보
        2) ``GET /v1/ticker?markets=KRW-BTC,KRW-ETH,...`` 로 일괄 현재가 조회
        """
        # 1단계: 마켓 코드 조회
        markets_resp = await self._public_get("/v1/market/all")
        if not markets_resp.success or not markets_resp.data:
            return markets_resp

        all_markets = markets_resp.data.get("items", [])
        krw_codes = [
            m["market"]
            for m in all_markets
            if isinstance(m, dict) and str(m.get("market", "")).startswith("KRW-")
        ]
        if not krw_codes:
            return MCPResponse(success=False, error="KRW 마켓 코드 없음")

        # 2단계: 전체 ticker 일괄 조회
        ticker_resp = await self._public_get(
            "/v1/ticker",
            params={"markets": ",".join(krw_codes)},
        )
        if not ticker_resp.success or not ticker_resp.data:
            return ticker_resp

        raw_items = ticker_resp.data.get("items", [])
        overview = [self._normalize_ticker(t) for t in raw_items if isinstance(t, dict)]

        # 마켓 코드 → 한글명/영문명 매핑
        name_map: dict[str, dict[str, str]] = {}
        for m in all_markets:
            if isinstance(m, dict):
                name_map[m.get("market", "")] = {
                    "korean_name": m.get("korean_name", ""),
                    "english_name": m.get("english_name", ""),
                }

        for item in overview:
            names = name_map.get(item.get("market", ""), {})
            item["korean_name"] = names.get("korean_name", "")
            item["english_name"] = names.get("english_name", "")

        return MCPResponse(
            success=True,
            data={"items": overview, "count": len(overview)},
        )

    async def get_volume_rank(
        self,
        market: str = "",
        *,
        overview_data: MCPResponse | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MCPResponse:
        """거래대금 상위 종목 — overview 데이터에서 trade_value 내림차순 정렬."""
        limit = int(kwargs.get("limit", 30))
        overview = overview_data if overview_data is not None else await self.get_market_overview(market)
        if isinstance(overview, dict):
            overview = MCPResponse(success=True, data=overview)
        if not overview.success or not overview.data:
            return overview

        items = overview.data.get("items", [])
        sorted_items = sorted(items, key=lambda x: x.get("trade_value", 0), reverse=True)
        top = sorted_items[:limit]

        return MCPResponse(
            success=True,
            data={"items": top, "count": len(top), "sort_by": "trade_value"},
        )

    async def get_surge_data(
        self,
        market: str = "",
        *,
        overview_data: MCPResponse | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MCPResponse:
        """급등/급락 종목 — overview 데이터에서 change_rate 절대값 내림차순 정렬."""
        limit = int(kwargs.get("limit", 30))
        overview = overview_data if overview_data is not None else await self.get_market_overview(market)
        if isinstance(overview, dict):
            overview = MCPResponse(success=True, data=overview)
        if not overview.success or not overview.data:
            return overview

        items = overview.data.get("items", [])
        sorted_items = sorted(
            items,
            key=lambda x: abs(x.get("change_rate", 0)),
            reverse=True,
        )
        top = sorted_items[:limit]

        return MCPResponse(
            success=True,
            data={"items": top, "count": len(top), "sort_by": "change_rate_abs"},
        )


# ═══════════════════════════════════════════════════════════════════════════
# 싱글톤 인스턴스
# ═══════════════════════════════════════════════════════════════════════════

bithumb_client = BithumbClient()
