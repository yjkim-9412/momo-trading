import unittest

from core.config import settings
from strategy.risk_manager import RiskManager
from strategy.signal import TradeSignal
from trading.enums import SignalAction, SignalUrgency


class RiskManagerPolicyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original = {
            "TRADING_ENABLED": settings.TRADING_ENABLED,
            "MAX_SINGLE_ORDER_KRW": settings.MAX_SINGLE_ORDER_KRW,
            "MIN_BUY_QUANTITY": settings.MIN_BUY_QUANTITY,
            "US_LEVERAGED_PRODUCTS_ENABLED": settings.US_LEVERAGED_PRODUCTS_ENABLED,
            "US_INVERSE_PRODUCTS_ENABLED": settings.US_INVERSE_PRODUCTS_ENABLED,
            "US_LEVERAGE_ALLOWED_SESSIONS": settings.US_LEVERAGE_ALLOWED_SESSIONS,
            "US_LEVERAGE_ALLOWED_STRATEGIES": settings.US_LEVERAGE_ALLOWED_STRATEGIES,
            "US_LEVERAGE_MAX_SINGLE_ORDER_RATIO": settings.US_LEVERAGE_MAX_SINGLE_ORDER_RATIO,
        }
        settings.TRADING_ENABLED = True
        settings.MAX_SINGLE_ORDER_KRW = 10000
        settings.MIN_BUY_QUANTITY = 1
        settings.US_LEVERAGED_PRODUCTS_ENABLED = True
        settings.US_INVERSE_PRODUCTS_ENABLED = True
        settings.US_LEVERAGE_ALLOWED_SESSIONS = "US_REGULAR"
        settings.US_LEVERAGE_ALLOWED_STRATEGIES = "STABLE_SHORT"
        settings.US_LEVERAGE_MAX_SINGLE_ORDER_RATIO = 0.3

    def tearDown(self):
        for field_name, value in self._original.items():
            setattr(settings, field_name, value)

    async def test_restricted_product_order_cap_is_scaled_down(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="TQQQ",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=50,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="STABLE_SHORT",
            metadata={
                "market": "NASDAQ",
                "price_krw": 100.0,
                "product_type": "LEVERAGED_ETF",
                "is_leveraged": True,
                "session": "US_REGULAR",
            },
        )

        result = await manager.check(
            signal=signal,
            portfolio_cash=50000,
            portfolio_budget=100000,
            today_trade_count=0,
            current_holding_count=0,
        )

        self.assertTrue(result["approved"])
        self.assertEqual(result["adjusted_quantity"], 30)

    async def test_restricted_product_blocks_outside_regular_session(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="TQQQ",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=10,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="STABLE_SHORT",
            metadata={
                "market": "NASDAQ",
                "price_krw": 100.0,
                "product_type": "LEVERAGED_ETF",
                "is_leveraged": True,
                "session": "US_PRE",
            },
        )

        result = await manager.check(
            signal=signal,
            portfolio_cash=50000,
            portfolio_budget=100000,
            today_trade_count=0,
            current_holding_count=0,
        )

        self.assertFalse(result["approved"])
        self.assertIn("US_REGULAR", result["reason"])


if __name__ == "__main__":
    unittest.main()
