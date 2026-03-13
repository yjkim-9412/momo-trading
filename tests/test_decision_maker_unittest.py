import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from agent.decision_maker import DecisionMaker
from strategy.signal import TradeSignal
from trading.enums import SignalAction, SignalUrgency
from util.time_util import now_kst


class DecisionMakerPriceGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_us_order_price_guard_blocks_krw_mispriced_limit_order(self):
        signal = TradeSignal(
            symbol="COIN",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.7,
            suggested_price=42300.0,
            suggested_quantity=1,
            urgency=SignalUrgency.IMMEDIATE,
            metadata={
                "market": "NASDAQ",
                "currency": "USD",
                "exchange_rate_to_krw": 215.16,
                "live_price": 196.6,
                "live_price_krw": 42300.456,
                "entry_price_krw": 42300.0,
            },
        )

        maker = DecisionMaker()

        with (
            patch("agent.decision_maker.activity_logger.log", AsyncMock()) as log_mock,
            patch("agent.decision_maker.event_bus.publish", AsyncMock()) as publish_mock,
            patch("agent.decision_maker.mcp_client.place_order", AsyncMock()) as place_order_mock,
        ):
            result = await maker.execute(signal, cycle_id="cycle-1")

        self.assertFalse(result["success"])
        self.assertIn("실시간 현재가 대비 과도하게 벗어남", result["message"])
        place_order_mock.assert_not_awaited()
        publish_mock.assert_awaited_once()
        self.assertGreaterEqual(log_mock.await_count, 2)

    async def test_paper_us_premarket_falls_back_to_recommendation(self):
        signal = TradeSignal(
            symbol="COIN",
            stock_id="stock-1",
            action=SignalAction.BUY,
            strength=0.7,
            suggested_price=198.78,
            suggested_quantity=1,
            urgency=SignalUrgency.IMMEDIATE,
            confidence=0.81,
            reason="Premarket breakout",
            metadata={
                "market": "NASDAQ",
                "currency": "USD",
                "price_krw": 42700.0,
                "live_price": 198.78,
                "live_price_krw": 42700.0,
            },
        )

        maker = DecisionMaker()

        with (
            patch("agent.decision_maker.settings.KIS_ACCOUNT_TYPE", "VIRTUAL"),
            patch("agent.decision_maker.market_calendar.get_market_session", return_value="US_PRE"),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()) as log_mock,
            patch("agent.decision_maker.event_bus.publish", AsyncMock()) as publish_mock,
            patch("agent.decision_maker.mcp_client.place_order", AsyncMock()) as place_order_mock,
        ):
            result = await maker.execute(signal, analysis_id="analysis-1", cycle_id="cycle-2")

        self.assertEqual(result["mode"], "AUTONOMOUS_FALLBACK")
        self.assertEqual(result["fallback_session"], "US_PRE")
        self.assertEqual(result["recommendation"]["analysis_id"], "analysis-1")
        self.assertEqual(result["recommendation"]["status"], "PENDING")
        expires_at = result["recommendation"]["expires_at"]
        self.assertGreater(expires_at, now_kst())
        self.assertLess(expires_at, now_kst() + timedelta(hours=1))
        place_order_mock.assert_not_awaited()
        publish_mock.assert_awaited_once()
        self.assertGreaterEqual(log_mock.await_count, 3)


if __name__ == "__main__":
    unittest.main()
