import unittest
from unittest.mock import AsyncMock, patch

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

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=50000,
                portfolio_budget=100000,
                today_trade_count=0,
                current_holding_count=0,
                orderable_cash_krw=50000,
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

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=50000,
                portfolio_budget=100000,
                today_trade_count=0,
                current_holding_count=0,
                orderable_cash_krw=50000,
            )

        self.assertFalse(result["approved"])
        self.assertIn("US_REGULAR", result["reason"])

    async def test_risk_log_formats_unlimited_daily_limit(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="PLTR",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=1,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="AGGRESSIVE_SHORT",
            metadata={
                "market": "NASDAQ",
                "price_krw": 100.0,
                "session": "US_REGULAR",
            },
        )

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()) as log_mock:
            result = await manager.check(
                signal=signal,
                portfolio_cash=50000,
                portfolio_budget=100000,
                today_trade_count=3,
                current_holding_count=0,
                orderable_cash_krw=50000,
                dynamic_limits={"max_daily_trades": 0},
            )

        self.assertTrue(result["approved"])
        self.assertIn("3/무제한", log_mock.await_args.args[2])

    async def test_combined_position_cap_uses_existing_position_value(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="PLTR",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=100,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="AGGRESSIVE_SHORT",
            metadata={
                "market": "NASDAQ",
                "price_krw": 100.0,
                "session": "US_REGULAR",
            },
        )

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=50000,
                portfolio_budget=100000,
                today_trade_count=0,
                current_holding_count=1,
                orderable_cash_krw=50000,
                current_position={
                    "symbol": "PLTR",
                    "market": "NASDAQ",
                    "quantity": 220,
                    "current_value_krw": 22000,
                },
                dynamic_limits={"max_position_pct": 25.0},
            )

        self.assertTrue(result["approved"])
        self.assertEqual(result["adjusted_quantity"], 30)
        self.assertEqual(result["combined_position_pct"], 25.0)

    async def test_combined_position_cap_still_applies_after_order_cap_adjustment(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="PLTR",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=300,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="AGGRESSIVE_SHORT",
            metadata={
                "market": "NASDAQ",
                "price_krw": 100.0,
                "session": "US_REGULAR",
            },
        )

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=50000,
                portfolio_budget=100000,
                today_trade_count=0,
                current_holding_count=1,
                orderable_cash_krw=50000,
                current_position={
                    "symbol": "PLTR",
                    "market": "NASDAQ",
                    "quantity": 220,
                    "current_value_krw": 22000,
                },
                dynamic_limits={"max_position_pct": 25.0},
            )

        self.assertTrue(result["approved"])
        self.assertEqual(result["adjusted_quantity"], 30)
        self.assertIn("합산 비중 한도", result["reason"])

    async def test_us_buy_uses_orderable_cash_even_when_broker_cash_is_zero(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="COIN",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=10,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="AGGRESSIVE_SHORT",
            metadata={
                "market": "NASDAQ",
                "price_krw": 100.0,
                "session": "US_REGULAR",
            },
        )

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=0,
                portfolio_budget=100000,
                today_trade_count=0,
                current_holding_count=0,
                orderable_cash_krw=10000,
            )

        self.assertTrue(result["approved"])
        self.assertEqual(result["cash_basis_krw"], 10000)
        self.assertEqual(result["broker_cash_krw"], 0)
        self.assertEqual(result["orderable_cash_krw"], 10000)

    async def test_us_buy_blocks_when_orderable_amount_lookup_fails(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="COIN",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=10,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="AGGRESSIVE_SHORT",
            metadata={
                "market": "NASDAQ",
                "price_krw": 100.0,
                "session": "US_REGULAR",
            },
        )

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=0,
                portfolio_budget=100000,
                today_trade_count=0,
                current_holding_count=0,
            )

        self.assertFalse(result["approved"])
        self.assertIn("종목별 주문가능금액 조회 실패", result["reason"])


if __name__ == "__main__":
    unittest.main()
