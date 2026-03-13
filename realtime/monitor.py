"""실시간 모니터 - WebSocket으로 시세 수신 + 이벤트 감지"""
import asyncio

from loguru import logger

from realtime.event_detector import event_detector
from realtime.stream_manager import stream_manager
from trading.kis_websocket import kis_websocket


class RealtimeMonitor:
    """
    실시간 시세 모니터
    - WebSocket에서 가격 데이터 수신
    - EventDetector로 이벤트 감지
    - Agent 트리거 연결
    """

    def __init__(self):
        self._running = False

    async def start(self) -> None:
        """실시간 모니터링 시작"""
        kis_websocket.set_on_price(self._on_price_update)
        try:
            await stream_manager.start()
        except Exception:
            self._running = False
            raise
        self._running = True
        logger.info("실시간 모니터 시작")

    async def stop(self) -> None:
        """실시간 모니터링 중지"""
        self._running = False
        await stream_manager.stop()
        logger.info("실시간 모니터 중지")

    async def _on_price_update(self, data: dict) -> None:
        """WebSocket에서 가격 데이터 수신 시 호출"""
        await event_detector.on_price_update(data)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return stream_manager.is_connected


realtime_monitor = RealtimeMonitor()
