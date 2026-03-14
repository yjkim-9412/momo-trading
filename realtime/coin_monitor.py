"""코인 실시간 모니터."""
from __future__ import annotations

import asyncio

from loguru import logger

from agent.decision_maker import decision_maker
from realtime.coin_stream_manager import coin_stream_manager
from realtime.event_detector import event_detector
from trading.account_manager import account_manager
from trading.bithumb_websocket import bithumb_websocket


class CoinRealtimeMonitor:
    """빗썸 WebSocket → 이벤트 감지/주문·자산 동기화."""

    _ACCOUNT_CHANGE_DEBOUNCE_SECONDS = 0.3

    def __init__(self) -> None:
        self._running = False
        self._account_change_task: asyncio.Task | None = None
        self._pending_account_change: dict[str, object] = {
            "reasons": set(),
            "symbols": set(),
            "changed_currencies": set(),
        }

    async def start(self) -> None:
        if self._running:
            logger.debug("코인 실시간 모니터 이미 시작됨")
            return
        bithumb_websocket.set_on_public(self._on_price_update)
        bithumb_websocket.set_on_order(self._on_order_update)
        bithumb_websocket.set_on_asset(self._on_asset_update)
        await coin_stream_manager.start()
        self._running = True
        logger.info("코인 실시간 모니터 시작")

    async def stop(self) -> None:
        self._running = False
        if self._account_change_task is not None:
            self._account_change_task.cancel()
            try:
                await self._account_change_task
            except asyncio.CancelledError:
                pass
            self._account_change_task = None
        self._pending_account_change = {
            "reasons": set(),
            "symbols": set(),
            "changed_currencies": set(),
        }
        await coin_stream_manager.stop()
        logger.info("코인 실시간 모니터 중지")

    async def _on_price_update(self, data: dict) -> None:
        await event_detector.on_price_update(data)

    async def _on_order_update(self, data: dict) -> None:
        change = await decision_maker.sync_coin_ws_order(data)
        if change:
            await self._schedule_account_change(
                reason=str(change.get("reason") or "order_update"),
                symbol=str(change.get("symbol") or ""),
                order_id=str(change.get("order_id") or ""),
                status=str(change.get("status") or ""),
            )

    async def _on_asset_update(self, data: dict) -> None:
        account_manager.invalidate_cache()
        changed_currencies = [
            str(asset.get("currency") or "").upper()
            for asset in (data.get("assets") or [])
            if isinstance(asset, dict) and asset.get("currency")
        ]
        await self._schedule_account_change(
            reason="asset_update",
            asset_timestamp=str(data.get("asset_timestamp") or ""),
            changed_currencies=changed_currencies,
        )

    async def _schedule_account_change(
        self,
        *,
        reason: str,
        symbol: str = "",
        order_id: str = "",
        status: str = "",
        asset_timestamp: str = "",
        changed_currencies: list[str] | None = None,
    ) -> None:
        """계좌 갱신 신호를 짧게 모아서 SSE로 보낸다."""
        pending = self._pending_account_change
        reasons = pending.setdefault("reasons", set())
        symbols = pending.setdefault("symbols", set())
        currencies = pending.setdefault("changed_currencies", set())
        if isinstance(reasons, set):
            reasons.add(reason)
        if symbol and isinstance(symbols, set):
            symbols.add(symbol)
        if changed_currencies and isinstance(currencies, set):
            currencies.update(changed_currencies)
        if order_id:
            pending["order_id"] = order_id
        if status:
            pending["status"] = status
        if asset_timestamp:
            pending["asset_timestamp"] = asset_timestamp

        if self._account_change_task is None or self._account_change_task.done():
            self._account_change_task = asyncio.create_task(self._flush_account_change())

    async def _flush_account_change(self) -> None:
        try:
            await asyncio.sleep(self._ACCOUNT_CHANGE_DEBOUNCE_SECONDS)
            payload = self._build_account_change_payload()
            if payload is None:
                return

            from api.routes.admin_coin import coin_sse_manager

            await coin_sse_manager.broadcast(payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[CoinWS] account_changed 브로드캐스트 실패: {}", str(e))
        finally:
            self._pending_account_change = {
                "reasons": set(),
                "symbols": set(),
                "changed_currencies": set(),
            }
            self._account_change_task = None

    def _build_account_change_payload(self) -> dict[str, object] | None:
        reasons = self._pending_account_change.get("reasons", set())
        if not reasons:
            return None

        symbols = self._pending_account_change.get("symbols", set())
        currencies = self._pending_account_change.get("changed_currencies", set())
        return {
            "type": "account_changed",
            "reason": ",".join(sorted(reasons)),
            "symbols": sorted(symbols) if isinstance(symbols, set) else [],
            "changed_currencies": sorted(currencies) if isinstance(currencies, set) else [],
            "order_id": self._pending_account_change.get("order_id"),
            "status": self._pending_account_change.get("status"),
            "asset_timestamp": self._pending_account_change.get("asset_timestamp"),
        }

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return coin_stream_manager.stream_status().get("connected", False)


coin_realtime_monitor = CoinRealtimeMonitor()
