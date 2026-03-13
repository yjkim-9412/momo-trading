"""KIS WebSocket 실시간 시세 스트리밍"""
import asyncio
import json
from typing import Any, Callable, Coroutine

import websockets
from loguru import logger

from core.config import settings
from trading.market_profile import build_ws_key, is_domestic_market, normalize_market


_WS_RECORD_SIZES: dict[str, tuple[int, ...]] = {
    "H0STCNT0": (46,),
    "HDFSCNT0": (26, 25),
}


def _ws_is_closed(ws) -> bool:
    """websockets 버전 호환 연결 상태 확인 (13+ 에서 .closed 제거됨)"""
    if ws is None:
        return True
    if hasattr(ws, "closed"):
        return ws.closed
    # websockets 14+ ClientConnection: close_code가 None이면 아직 연결 중
    return ws.close_code is not None


class KISWebSocket:
    """한국투자증권 WebSocket 실시간 시세 클라이언트"""

    def __init__(self):
        self._ws_domestic = None
        self._ws_overseas = None
        self._running = False
        self._subscriptions: set[str] = set()
        self._on_price_callback: Callable[[dict], Coroutine[Any, Any, None]] | None = None
        self._reconnect_delay = 5
        self._approval_key: str | None = None

    def set_on_price(self, callback: Callable[[dict], Coroutine[Any, Any, None]]) -> None:
        self._on_price_callback = callback

    async def connect(self) -> None:
        """WebSocket 연결 시작"""
        self._running = True
        self._approval_key = None
        await self._get_approval_key()
        if not self._approval_key:
            self._running = False
            raise ConnectionError("WebSocket approval key 발급 실패")
        logger.info("KIS WebSocket 연결 시작")

    async def disconnect(self) -> None:
        """WebSocket 연결 종료"""
        self._running = False
        for ws in (self._ws_domestic, self._ws_overseas):
            if ws:
                await ws.close()
        self._ws_domestic = None
        self._ws_overseas = None
        self._subscriptions.clear()
        logger.info("KIS WebSocket 연결 종료")

    async def _get_approval_key(self) -> None:
        """WebSocket 접속 키 발급"""
        import httpx

        base_url = (
            "https://openapivts.koreainvestment.com:29443"
            if settings.is_paper_trading
            else "https://openapi.koreainvestment.com:9443"
        )
        if settings.is_paper_trading:
            app_key = settings.KIS_PAPER_APP_KEY or settings.KIS_APP_KEY
            app_secret = settings.KIS_PAPER_APP_SECRET or settings.KIS_APP_SECRET
        else:
            app_key = settings.KIS_APP_KEY
            app_secret = settings.KIS_APP_SECRET
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8",
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{base_url}/oauth2/Approval",
                    headers=headers,
                    json={
                        "grant_type": "client_credentials",
                        "appkey": app_key,
                        "secretkey": app_secret,
                    },
                )
                if resp.status_code == 200:
                    self._approval_key = resp.json().get("approval_key")
                    logger.debug("WebSocket approval key 발급 완료")
                else:
                    logger.warning("WebSocket approval key 발급 실패: HTTP {} - {}", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("WebSocket approval key 발급 실패: {}", str(e))

    async def subscribe(self, symbol: str, market: str = "KRX") -> bool:
        """종목 실시간 시세 구독"""
        if len(self._subscriptions) >= 41:
            logger.warning("WebSocket 구독 한도 초과 (최대 41종목)")
            return False

        market_code = normalize_market(market)
        key = f"{market_code}:{symbol.upper()}"
        if key in self._subscriptions:
            return True

        ws = await self._get_ws(market_code)
        if not ws:
            return False

        try:
            sub_msg = self._build_subscribe_msg(symbol, market_code)
            await ws.send(json.dumps(sub_msg))
            self._subscriptions.add(key)
            logger.debug("종목 구독: {}", key)
            return True
        except Exception as e:
            logger.error("구독 실패: {} - {}", key, str(e))
            return False

    async def unsubscribe(self, symbol: str, market: str = "KRX") -> None:
        """종목 구독 해제"""
        market_code = normalize_market(market)
        key = f"{market_code}:{symbol.upper()}"
        if key not in self._subscriptions:
            return

        ws = await self._get_ws(market_code)
        if ws:
            try:
                unsub_msg = self._build_unsubscribe_msg(symbol, market_code)
                await ws.send(json.dumps(unsub_msg))
            except Exception:
                pass
        self._subscriptions.discard(key)
        logger.debug("종목 구독 해제: {}", key)

    async def _get_ws(self, market: str):
        """시장별 WebSocket 연결 반환 (없으면 생성)"""
        market_code = normalize_market(market)
        if is_domestic_market(market_code):
            if not self._ws_domestic or _ws_is_closed(self._ws_domestic):
                try:
                    self._ws_domestic = await websockets.connect(
                        settings.KIS_WS_URL_DOMESTIC
                    )
                except Exception as e:
                    logger.error("국내 WebSocket 연결 실패: {}", str(e))
                    return None
            return self._ws_domestic
        else:
            if not self._ws_overseas or _ws_is_closed(self._ws_overseas):
                try:
                    self._ws_overseas = await websockets.connect(
                        settings.KIS_WS_URL_OVERSEAS
                    )
                except Exception as e:
                    logger.error("해외 WebSocket 연결 실패: {}", str(e))
                    return None
            return self._ws_overseas

    def _build_subscribe_msg(self, symbol: str, market: str) -> dict:
        market_code = normalize_market(market)
        return {
            "header": {
                "approval_key": self._approval_key or "",
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNT0" if is_domestic_market(market_code) else "HDFSCNT0",
                    "tr_key": build_ws_key(symbol, market_code),
                }
            },
        }

    def _build_unsubscribe_msg(self, symbol: str, market: str) -> dict:
        msg = self._build_subscribe_msg(symbol, market)
        msg["header"]["tr_type"] = "2"
        return msg

    async def listen(self) -> None:
        """WebSocket 메시지 수신 루프"""
        while self._running:
            tasks = []
            if self._ws_domestic and not _ws_is_closed(self._ws_domestic):
                tasks.append(asyncio.create_task(self._listen_ws(self._ws_domestic, "domestic")))
            if self._ws_overseas and not _ws_is_closed(self._ws_overseas):
                tasks.append(asyncio.create_task(self._listen_ws(self._ws_overseas, "overseas")))

            if not tasks:
                await asyncio.sleep(1)
                continue

            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()

    async def _listen_ws(self, ws, ws_type: str) -> None:
        """개별 WebSocket 메시지 수신"""
        try:
            async for raw_msg in ws:
                if not self._running:
                    break
                await self._handle_message(raw_msg, ws_type, ws)
        except websockets.ConnectionClosed:
            logger.warning("{} WebSocket 연결 끊김, 재연결 시도...", ws_type)
            if self._running:
                await asyncio.sleep(self._reconnect_delay)
        except Exception as e:
            logger.error("{} WebSocket 오류: {}", ws_type, str(e))

    async def _handle_message(self, raw_msg: str, ws_type: str, ws) -> None:
        """수신된 메시지 처리"""
        # PINGPONG 응답
        if raw_msg == "PINGPONG":
            await ws.send("PINGPONG")
            return

        try:
            # KIS WebSocket은 '|' 구분자로 데이터를 보냄
            if "|" in raw_msg:
                parts = raw_msg.split("|")
                if len(parts) >= 4:
                    tr_id = parts[1]
                    data_count = int(parts[2])
                    data_str = parts[3]
                    price_rows = self._parse_price_rows(tr_id, data_str, data_count)
                    if price_rows and self._on_price_callback:
                        for price_data in price_rows:
                            await self._on_price_callback(price_data)
            else:
                # JSON 형태 응답 (구독 확인 등)
                data = json.loads(raw_msg)
                header = data.get("header", {})
                if header.get("tr_id") == "PINGPONG":
                    await ws.send(raw_msg)
        except Exception as e:
            logger.debug("메시지 파싱 오류 (무시): {}", str(e))

    def _parse_price_rows(self, tr_id: str, data_str: str, data_count: int) -> list[dict]:
        """TR별 실시간 payload를 레코드 단위로 분리 후 파싱"""
        records = self._split_records(tr_id, data_str, data_count)
        parsed_rows: list[dict] = []
        for fields in records:
            try:
                parsed = self._parse_price_fields(tr_id, fields)
            except Exception as e:
                logger.debug("실시간 레코드 파싱 오류 (무시): tr_id={} err={}", tr_id, str(e))
                continue
            if parsed:
                parsed_rows.append(parsed)
        return parsed_rows

    def _split_records(self, tr_id: str, data_str: str, data_count: int) -> list[list[str]]:
        """TR별 payload를 data_count 기준 개별 레코드로 분리"""
        if data_count <= 0:
            return []

        record_sizes = _WS_RECORD_SIZES.get(tr_id)
        if not record_sizes:
            return []

        fields = data_str.split("^")
        total_fields = len(fields)
        for record_size in record_sizes:
            if total_fields == record_size * data_count:
                return [
                    fields[index:index + record_size]
                    for index in range(0, total_fields, record_size)
                ]

        logger.debug(
            "실시간 payload 필드 수 불일치: tr_id={} count={} fields={} candidates={}",
            tr_id,
            data_count,
            total_fields,
            ",".join(str(size) for size in record_sizes),
        )
        return []

    def _parse_price_fields(self, tr_id: str, fields: list[str]) -> dict | None:
        """단일 레코드 필드 파싱"""
        if tr_id == "H0STCNT0":  # 국내 실시간 체결
            return {
                "market": "KRX",
                "symbol": fields[0],
                "time": fields[1],
                "price": self._to_float(fields[2]),
                "change": self._to_float(fields[4]),
                "change_rate": self._to_float(fields[5]),
                "volume": self._to_int(fields[12]),
                "cumulative_volume": self._to_int(fields[13]),
            }

        if tr_id == "HDFSCNT0":  # 해외 실시간 체결
            raw_symbol = fields[0]
            market, derived_symbol, session = self._parse_overseas_symbol(raw_symbol)

            if len(fields) == 26:
                symbol = fields[1].strip().upper() or derived_symbol
                return {
                    "market": market,
                    "symbol": symbol,
                    "session": session,
                    "currency": "USD",
                    "time": fields[7],
                    "price": self._to_float(fields[11]),
                    "change": self._to_float(fields[13]),
                    "change_rate": self._to_float(fields[14]),
                    "volume": self._to_int(fields[19]),
                    "cumulative_volume": self._to_int(fields[20]),
                }

            if len(fields) == 25:
                return {
                    "market": market,
                    "symbol": derived_symbol,
                    "session": session,
                    "currency": "USD",
                    "time": fields[6],
                    "price": self._to_float(fields[10]),
                    "change": self._to_float(fields[12]),
                    "change_rate": self._to_float(fields[13]),
                    "volume": self._to_int(fields[18]),
                    "cumulative_volume": self._to_int(fields[19]),
                }

        return None

    @staticmethod
    def _parse_overseas_symbol(raw_symbol: str) -> tuple[str, str, str]:
        token = (raw_symbol or "").strip().upper()
        prefixes = {
            "DNAS": ("NASDAQ", token[4:], "US_DELAYED"),
            "DNYS": ("NYSE", token[4:], "US_DELAYED"),
            "DAMS": ("AMEX", token[4:], "US_DELAYED"),
            "RBAQ": ("NASDAQ", token[4:], "US_DAYTIME"),
            "RBAY": ("NYSE", token[4:], "US_DAYTIME"),
            "RBAA": ("AMEX", token[4:], "US_DAYTIME"),
        }
        for prefix, parsed in prefixes.items():
            if token.startswith(prefix):
                return parsed
        return "NASDAQ", token, ""

    @staticmethod
    def _to_float(value: str) -> float:
        normalized = (value or "").strip()
        if not normalized:
            return 0.0
        return float(normalized)

    @staticmethod
    def _to_int(value: str) -> int:
        normalized = (value or "").strip()
        if not normalized:
            return 0
        return int(float(normalized))

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)

    @property
    def is_connected(self) -> bool:
        domestic_ok = self._ws_domestic and not _ws_is_closed(self._ws_domestic)
        overseas_ok = self._ws_overseas and not _ws_is_closed(self._ws_overseas)
        return bool(domestic_ok or overseas_ok)


# 싱글톤
kis_websocket = KISWebSocket()
