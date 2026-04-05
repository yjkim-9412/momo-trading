import unittest
from unittest.mock import AsyncMock, patch

import pandas as pd

from agent.market_scanner import MarketScanner
from agent.trading_agent import TradingAgent
from analysis.technical.roadmap_pullback import roadmap_pullback_analyzer
from core.config import settings
from core.events import Event, EventType
from realtime.event_detector import EventDetector
from trading.models import MCPResponse


def _build_trending_daily_items() -> list[dict]:
    items = []
    for index in range(70):
        close = 100 + index
        items.append(
            {
                "date": f"2025-01-{index + 1:02d}",
                "open": close - 1,
                "high": close + 1,
                "low": close - 2,
                "close": close,
                "volume": 100_000 + index * 1_000,
            }
        )
    return items


class RoadmapPullbackAnalyzerTest(unittest.TestCase):
    def test_evaluate_accepts_sma20_pullback(self):
        daily_df = pd.DataFrame(_build_trending_daily_items())
        current_price = float(daily_df["close"].rolling(20).mean().iloc[-1])

        snapshot = roadmap_pullback_analyzer.evaluate(
            daily_df,
            current_price=current_price,
        )

        self.assertTrue(snapshot.qualified)
        self.assertEqual(snapshot.stage, "SMA20_PULLBACK")
        self.assertEqual(snapshot.reason, "SMA20_PULLBACK 진입 구간")
        self.assertGreater(snapshot.roadmap_take_profit_price, current_price)


class EventDetectorRoadmapTest(unittest.IsolatedAsyncioTestCase):
    async def test_on_price_update_publishes_indicator_signal_for_roadmap_band_entry(self):
        detector = EventDetector()
        detector.set_thresholds(
            "005930",
            market="KRX",
            roadmap_enabled=True,
            roadmap_strategy_type="ROADMAP_PULLBACK",
            roadmap_sma20_entry_low=98.0,
            roadmap_sma20_entry_high=102.0,
            roadmap_sma60_entry_low=90.0,
            roadmap_sma60_entry_high=94.0,
            roadmap_invalid_price=89.0,
            roadmap_take_profit_price=120.0,
        )

        publish_mock = AsyncMock()
        with patch("realtime.event_detector.event_bus.publish", publish_mock):
            await detector.on_price_update(
                {"symbol": "005930", "market": "KRX", "price": 105.0, "change_rate": 0.0, "volume": 1000}
            )
            await detector.on_price_update(
                {"symbol": "005930", "market": "KRX", "price": 101.0, "change_rate": 0.0, "volume": 1000}
            )

        indicator_events = [
            call.args[0]
            for call in publish_mock.await_args_list
            if call.args and call.args[0].type == EventType.INDICATOR_SIGNAL
        ]
        self.assertEqual(len(indicator_events), 1)
        self.assertEqual(indicator_events[0].data["indicator_signal"], "ROADMAP_SMA20_PULLBACK")
        self.assertEqual(indicator_events[0].data["strategy_type"], "ROADMAP_PULLBACK")


class MarketScannerRoadmapGateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_krx = settings.ROADMAP_PULLBACK_ENABLED_KRX
        settings.ROADMAP_PULLBACK_ENABLED_KRX = True

    def tearDown(self):
        settings.ROADMAP_PULLBACK_ENABLED_KRX = self._original_krx

    async def test_apply_roadmap_pullback_gate_rewrites_strategy_and_monitoring(self):
        scanner = MarketScanner()
        daily_items = _build_trending_daily_items()
        current_price = float(pd.DataFrame(daily_items)["close"].rolling(20).mean().iloc[-1])
        selected = [{
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KRX",
            "price": current_price,
            "strategy_type": "STABLE_SHORT",
            "reason": "LLM 후보",
            "monitoring": {"surge_pct": 3.0},
        }]

        with patch(
            "agent.market_scanner.mcp_client.get_daily_price",
            AsyncMock(return_value=MCPResponse(success=True, data={"prices": daily_items})),
        ):
            filtered, stats = await scanner._apply_roadmap_pullback_gate(
                selected,
                default_market="KRX",
            )

        self.assertEqual(stats["after"], 1)
        self.assertEqual(filtered[0]["strategy_type"], "ROADMAP_PULLBACK")
        self.assertEqual(filtered[0]["roadmap_stage"], "SMA20_PULLBACK")
        self.assertTrue(filtered[0]["monitoring"]["roadmap_enabled"])
        self.assertIn("roadmap_sma20_entry_low", filtered[0]["monitoring"])


class TradingAgentRoadmapEventTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = TradingAgent()
        self.agent._running = True
        self._original_krx = settings.ROADMAP_PULLBACK_ENABLED_KRX
        settings.ROADMAP_PULLBACK_ENABLED_KRX = True

    def tearDown(self):
        settings.ROADMAP_PULLBACK_ENABLED_KRX = self._original_krx

    async def test_on_market_event_ignores_volume_spike_when_roadmap_mode_enabled(self):
        event = Event(
            type=EventType.VOLUME_SPIKE,
            data={"symbol": "005930", "market": "KRX", "price": 70_000, "change_rate": 3.0},
            source="test",
        )

        with patch.object(self.agent, "_analyze_and_trade", AsyncMock()) as analyze_mock:
            await self.agent._on_market_event(event)

        analyze_mock.assert_not_awaited()
