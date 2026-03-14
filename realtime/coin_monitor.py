"""코인 실시간 모니터."""
from __future__ import annotations

from loguru import logger

from agent.decision_maker import decision_maker
from realtime.coin_stream_manager import coin_stream_manager
from realtime.event_detector import event_detector
from trading.account_manager import account_manager
from trading.bithumb_websocket import bithumb_websocket


class CoinRealtimeMonitor:
    """빗썸 WebSocket → 이벤트 감지/주문·자산 동기화."""

    def __init__(self) -> None:
        self._running = False

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
        await coin_stream_manager.stop()
        logger.info("코인 실시간 모니터 중지")

    async def _on_price_update(self, data: dict) -> None:
        await event_detector.on_price_update(data)

    async def _on_order_update(self, data: dict) -> None:
        await decision_maker.sync_coin_ws_order(data)

    async def _on_asset_update(self, data: dict) -> None:
        del data
        account_manager.invalidate_cache()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return coin_stream_manager.stream_status().get("connected", False)


coin_realtime_monitor = CoinRealtimeMonitor()
