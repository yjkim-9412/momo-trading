"""빗썸 Public/Private WebSocket 클라이언트."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from uuid import uuid4

import websockets
from loguru import logger

from core.config import settings
from trading.bithumb_client import (
    bithumb_client,
    _symbol_from_market_code,
    _to_float,
)
from util.time_util import now_kst

_PUBLIC_RECONNECT_DELAY_SECONDS = 2.0
_PRIVATE_RECONNECT_DELAY_SECONDS = 3.0
_RECENT_VOLUME_WINDOW_SECONDS = 60.0


def _ws_is_closed(ws) -> bool:
    if ws is None:
        return True
    if hasattr(ws, "closed"):
        return ws.closed
    return ws.close_code is not None


def _normalize_change_rate_percent(value: Any) -> float:
    rate = _to_float(value)
    if rate != 0.0 and abs(rate) <= 1.0:
        return rate * 100.0
    return rate


def _normalize_side(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw in {"BID", "BUY"}:
        return "BUY"
    if raw in {"ASK", "SELL"}:
        return "SELL"
    return raw


def _parse_ws_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if numeric <= 0:
        return None
    if numeric > 10_000_000_000:
        numeric /= 1000.0
    try:
        return datetime.fromtimestamp(numeric, tz=timezone.utc).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


class BithumbWebSocket:
    """빗썸 실시간 스트리밍 런타임."""

    def __init__(self) -> None:
        self._running = False
        self._public_ws = None
        self._private_ws = None
        self._public_task: asyncio.Task | None = None
        self._private_task: asyncio.Task | None = None
        self._public_symbols: list[str] = []
        self._public_lock = asyncio.Lock()
        self._private_lock = asyncio.Lock()
        self._on_public_callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None
        self._on_order_callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None
        self._on_asset_callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None
        self._last_public_connect_error: str | None = None
        self._last_private_error: str | None = None
        self._last_public_connect_at: datetime | None = None
        self._last_private_connect_at: datetime | None = None
        self._last_public_message_at: datetime | None = None
        self._last_order_message_at: datetime | None = None
        self._last_asset_message_at: datetime | None = None
        self._ticker_cache: dict[str, dict[str, Any]] = {}
        self._recent_trade_volume: dict[str, deque[tuple[float, float]]] = defaultdict(deque)

    def set_on_public(
        self,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        self._on_public_callback = callback

    def set_on_order(
        self,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        self._on_order_callback = callback

    def set_on_asset(
        self,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        self._on_asset_callback = callback

    async def start(self) -> None:
        if self._running:
            logger.debug("빗썸 WebSocket 이미 시작됨")
            return
        self._running = True
        self._public_task = asyncio.create_task(self._run_public_loop())
        self._private_task = asyncio.create_task(self._run_private_loop())
        logger.info("빗썸 WebSocket 런타임 시작")

    async def stop(self) -> None:
        self._running = False
        for task in (self._public_task, self._private_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_public_ws()
        await self._close_private_ws()
        self._ticker_cache.clear()
        self._recent_trade_volume.clear()
        logger.info("빗썸 WebSocket 런타임 중지")

    async def set_public_symbols(self, symbols: list[str]) -> None:
        normalized: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            sym = str(symbol or "").upper().strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            normalized.append(sym)

        if normalized == self._public_symbols:
            return

        self._public_symbols = normalized
        if not self._running:
            return
        await self._close_public_ws()

    @property
    def public_symbols(self) -> list[str]:
        return list(self._public_symbols)

    @property
    def has_private_credentials(self) -> bool:
        return bool(settings.BITHUMB_API_KEY and settings.BITHUMB_API_SECRET)

    @property
    def public_connected(self) -> bool:
        return self._running and not _ws_is_closed(self._public_ws)

    @property
    def private_connected(self) -> bool:
        return self._running and not _ws_is_closed(self._private_ws)

    def active_keys(self) -> set[tuple[str, str]]:
        if not self.public_connected:
            return set()
        return {("BITHUMB", symbol) for symbol in self._public_symbols}

    def public_status(self) -> dict[str, Any]:
        desired_count = len(self._public_symbols)
        active_count = len(self.active_keys())
        return {
            "running": self._running,
            "connected": self.public_connected,
            "desired_count": desired_count,
            "active_count": active_count,
            "subscription_count": active_count,
            "subscription_limit": desired_count,
            "last_connect_error": self._last_public_connect_error,
            "last_connect_at": self._last_public_connect_at.isoformat() if self._last_public_connect_at else None,
            "last_message_at": self._last_public_message_at.isoformat() if self._last_public_message_at else None,
        }

    def private_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "connected": self.private_connected,
            "configured": self.has_private_credentials,
            "last_error": self._last_private_error,
            "last_connect_at": self._last_private_connect_at.isoformat() if self._last_private_connect_at else None,
            "last_order_message_at": self._last_order_message_at.isoformat() if self._last_order_message_at else None,
            "last_asset_message_at": self._last_asset_message_at.isoformat() if self._last_asset_message_at else None,
        }

    async def _run_public_loop(self) -> None:
        while self._running:
            if not self._public_symbols:
                await asyncio.sleep(1.0)
                continue

            try:
                if not await self._ensure_public_connection():
                    await asyncio.sleep(_PUBLIC_RECONNECT_DELAY_SECONDS)
                    continue
                ws = self._public_ws
                if ws is None:
                    await asyncio.sleep(_PUBLIC_RECONNECT_DELAY_SECONDS)
                    continue
                async for raw_msg in ws:
                    if not self._running:
                        break
                    self._last_public_message_at = now_kst()
                    await self._handle_public_message(raw_msg)
                if self._running:
                    raise ConnectionError("빗썸 Public WebSocket listener ended")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_public_connect_error = str(e)
                logger.warning("빗썸 Public WebSocket 오류: {}", str(e))
            finally:
                await self._close_public_ws()
                if self._running:
                    await asyncio.sleep(_PUBLIC_RECONNECT_DELAY_SECONDS)

    async def _run_private_loop(self) -> None:
        while self._running:
            if not self.has_private_credentials:
                self._last_private_error = "BITHUMB API credentials missing"
                await asyncio.sleep(10.0)
                continue

            try:
                if not await self._ensure_private_connection():
                    await asyncio.sleep(_PRIVATE_RECONNECT_DELAY_SECONDS)
                    continue
                ws = self._private_ws
                if ws is None:
                    await asyncio.sleep(_PRIVATE_RECONNECT_DELAY_SECONDS)
                    continue
                async for raw_msg in ws:
                    if not self._running:
                        break
                    await self._handle_private_message(raw_msg)
                if self._running:
                    raise ConnectionError("빗썸 Private WebSocket listener ended")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_private_error = str(e)
                logger.warning("빗썸 Private WebSocket 오류: {}", str(e))
            finally:
                await self._close_private_ws()
                if self._running:
                    await asyncio.sleep(_PRIVATE_RECONNECT_DELAY_SECONDS)

    async def _ensure_public_connection(self) -> bool:
        if self.public_connected:
            return True
        if not self._public_symbols:
            return False

        async with self._public_lock:
            if self.public_connected:
                return True

            request_payload = [
                {"ticket": f"momo-public-{uuid4().hex[:8]}"},
                {"type": "ticker", "codes": [f"KRW-{symbol}" for symbol in self._public_symbols]},
                {"type": "trade", "codes": [f"KRW-{symbol}" for symbol in self._public_symbols]},
                {"format": "DEFAULT"},
            ]
            ws = await websockets.connect(
                settings.BITHUMB_WS_URL_PUBLIC,
                ping_interval=30,
                ping_timeout=30,
                close_timeout=10,
                max_queue=128,
            )
            await ws.send(json.dumps(request_payload, ensure_ascii=False))
            self._public_ws = ws
            self._last_public_connect_error = None
            self._last_public_connect_at = now_kst()
            logger.info("빗썸 Public WebSocket 연결: {}종목", len(self._public_symbols))
            return True

    async def _ensure_private_connection(self) -> bool:
        if self.private_connected:
            return True
        if not self.has_private_credentials:
            return False

        async with self._private_lock:
            if self.private_connected:
                return True

            token = bithumb_client._build_jwt()
            request_payload = [
                {"ticket": f"momo-private-{uuid4().hex[:8]}"},
                {"type": "myOrder", "codes": []},
                {"type": "myAsset"},
                {"format": "DEFAULT"},
            ]
            ws = await websockets.connect(
                settings.BITHUMB_WS_URL_PRIVATE,
                additional_headers={"Authorization": f"Bearer {token}"},
                ping_interval=30,
                ping_timeout=30,
                close_timeout=10,
                max_queue=128,
            )
            await ws.send(json.dumps(request_payload, ensure_ascii=False))
            self._private_ws = ws
            self._last_private_error = None
            self._last_private_connect_at = now_kst()
            logger.info("빗썸 Private WebSocket 연결")
            return True

    async def _close_public_ws(self) -> None:
        async with self._public_lock:
            ws = self._public_ws
            self._public_ws = None
            if ws and not _ws_is_closed(ws):
                try:
                    await ws.close()
                except Exception:
                    pass

    async def _close_private_ws(self) -> None:
        async with self._private_lock:
            ws = self._private_ws
            self._private_ws = None
            if ws and not _ws_is_closed(ws):
                try:
                    await ws.close()
                except Exception:
                    pass

    async def _handle_public_message(self, raw_msg: str) -> None:
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.debug("빗썸 Public JSON 파싱 오류: {}", raw_msg[:200])
            return

        if str(data.get("status") or "").upper() == "UP":
            return

        payload_type = str(data.get("type") or "").strip().lower()
        if payload_type == "ticker":
            normalized = self._normalize_ticker(data)
        elif payload_type == "trade":
            normalized = self._normalize_trade(data)
        else:
            return

        if normalized and self._on_public_callback:
            await self._on_public_callback(normalized)

    async def _handle_private_message(self, raw_msg: str) -> None:
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            logger.debug("빗썸 Private JSON 파싱 오류: {}", raw_msg[:200])
            return

        if str(data.get("status") or "").upper() == "UP":
            return

        payload_type = str(data.get("type") or "").strip().lower()
        if payload_type == "myorder":
            normalized = self._normalize_my_order(data)
            if normalized and self._on_order_callback:
                self._last_order_message_at = now_kst()
                await self._on_order_callback(normalized)
            return
        if payload_type == "myasset":
            normalized = self._normalize_my_asset(data)
            if normalized and self._on_asset_callback:
                self._last_asset_message_at = now_kst()
                await self._on_asset_callback(normalized)

    def _normalize_ticker(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        market_pair = str(payload.get("code") or "").upper()
        symbol = _symbol_from_market_code(market_pair)
        price = _to_float(payload.get("trade_price"))
        if not symbol or price <= 0:
            return None

        normalized = {
            "symbol": symbol,
            "name": symbol,
            "market": "BITHUMB",
            "market_pair": market_pair,
            "price": price,
            "change_rate": _normalize_change_rate_percent(
                payload.get("signed_change_rate", payload.get("change_rate"))
            ),
            "change": _to_float(payload.get("signed_change_price", payload.get("change_price"))),
            "volume": _to_float(payload.get("acc_trade_volume_24h")),
            "trade_value": _to_float(payload.get("acc_trade_price_24h")),
            "trade_volume": _to_float(payload.get("trade_volume")),
            "source": "bithumb_ws",
            "ws_type": "ticker",
            "stream_type": str(payload.get("stream_type") or ""),
            "timestamp": payload.get("timestamp"),
        }
        self._ticker_cache[symbol] = normalized
        return normalized

    def _normalize_trade(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        market_pair = str(payload.get("code") or "").upper()
        symbol = _symbol_from_market_code(market_pair)
        trade_price = _to_float(payload.get("trade_price"))
        trade_volume = abs(_to_float(payload.get("trade_volume")))
        if not symbol or trade_price <= 0:
            return None

        now_ts = asyncio.get_running_loop().time()
        history = self._recent_trade_volume[symbol]
        history.append((now_ts, trade_volume))
        while history and (now_ts - history[0][0]) > _RECENT_VOLUME_WINDOW_SECONDS:
            history.popleft()

        cached = self._ticker_cache.get(symbol, {})
        return {
            "symbol": symbol,
            "name": cached.get("name", symbol),
            "market": "BITHUMB",
            "market_pair": market_pair,
            "price": trade_price,
            "change_rate": _to_float(cached.get("change_rate")),
            "change": _to_float(cached.get("change")),
            "volume": float(sum(volume for _, volume in history)),
            "trade_value": _to_float(payload.get("trade_price")) * trade_volume,
            "trade_volume": trade_volume,
            "source": "bithumb_ws",
            "ws_type": "trade",
            "stream_type": str(payload.get("stream_type") or ""),
            "timestamp": payload.get("timestamp"),
        }

    @staticmethod
    def _normalize_my_order(payload: dict[str, Any]) -> dict[str, Any] | None:
        market_pair = str(payload.get("code") or "").upper()
        symbol = _symbol_from_market_code(market_pair)
        order_id = str(payload.get("uuid") or "").strip()
        if not symbol or not order_id:
            return None

        order_qty = _to_float(payload.get("volume"))
        executed_volume = _to_float(payload.get("executed_volume"))
        remaining_volume = _to_float(payload.get("remaining_volume"))
        state = str(payload.get("state") or "").strip().lower()
        status = "SUBMITTED"
        if state == "done" or (executed_volume > 0 and remaining_volume <= 0):
            status = "FILLED"
        elif state == "cancel":
            status = "CANCELED"
        elif executed_volume > 0:
            status = "PARTIAL"
        elif state in {"trade", "wait"}:
            status = "OPEN" if state == "trade" else "SUBMITTED"

        order_type = str(payload.get("order_type") or "").lower()
        executed_funds = _to_float(payload.get("executed_funds"))
        filled_price = 0.0
        if executed_volume > 0:
            if executed_funds > 0:
                filled_price = executed_funds / executed_volume
            else:
                filled_price = _to_float(payload.get("price"))
        requested_amount_krw = 0.0
        if order_type == "price":
            requested_amount_krw = _to_float(payload.get("price"))
        elif _normalize_side(payload.get("ask_bid")) == "BUY":
            requested_amount_krw = _to_float(payload.get("price")) * order_qty

        submitted_at = _parse_ws_datetime(payload.get("order_timestamp") or payload.get("timestamp"))
        filled_at = _parse_ws_datetime(payload.get("trade_timestamp") or payload.get("timestamp"))
        if status not in {"FILLED", "PARTIAL"}:
            filled_at = None

        return {
            "order_id": order_id,
            "client_order_id": str(payload.get("client_order_id") or ""),
            "market": "BITHUMB",
            "market_pair": market_pair,
            "symbol": symbol,
            "name": symbol,
            "side": _normalize_side(payload.get("ask_bid")),
            "status": status,
            "state": state.upper(),
            "order_type": order_type.upper(),
            "ord_type": order_type,
            "order_price": _to_float(payload.get("price")),
            "price": _to_float(payload.get("price")),
            "order_qty": order_qty,
            "volume": order_qty,
            "filled_qty": executed_volume,
            "filled_quantity": executed_volume,
            "remaining_qty": remaining_volume,
            "remaining_volume": remaining_volume,
            "requested_amount_krw": requested_amount_krw,
            "filled_price": filled_price,
            "currency": "KRW",
            "exchange_rate_to_krw": 1.0,
            "reserved_fee": _to_float(payload.get("reserved_fee")),
            "remaining_fee": _to_float(payload.get("remaining_fee")),
            "paid_fee": _to_float(payload.get("paid_fee")),
            "executed_funds": executed_funds,
            "trades_count": int(payload.get("trades_count") or 0),
            "submitted_at": submitted_at,
            "filled_at": filled_at,
            "source": "bithumb_ws",
            "stream_type": str(payload.get("stream_type") or ""),
            "timestamp": payload.get("timestamp"),
            "raw": payload,
        }

    @staticmethod
    def _normalize_my_asset(payload: dict[str, Any]) -> dict[str, Any] | None:
        assets = payload.get("assets")
        if not isinstance(assets, list):
            return None

        normalized_assets = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            currency = str(asset.get("currency") or "").upper()
            if not currency:
                continue
            normalized_assets.append({
                "currency": currency,
                "balance": _to_float(asset.get("balance")),
                "locked": _to_float(asset.get("locked")),
            })

        return {
            "assets": normalized_assets,
            "asset_timestamp": payload.get("asset_timestamp"),
            "timestamp": payload.get("timestamp"),
            "stream_type": str(payload.get("stream_type") or ""),
            "source": "bithumb_ws",
        }


bithumb_websocket = BithumbWebSocket()
