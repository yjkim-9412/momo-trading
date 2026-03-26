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
            "CRYPTO_TRADING_ENABLED": settings.CRYPTO_TRADING_ENABLED,
            "CRYPTO_MAX_SINGLE_ORDER_KRW": settings.CRYPTO_MAX_SINGLE_ORDER_KRW,
            "CRYPTO_MIN_BUY_QUANTITY": settings.CRYPTO_MIN_BUY_QUANTITY,
            "CRYPTO_MIN_CASH_RATIO": settings.CRYPTO_MIN_CASH_RATIO,
            "CRYPTO_MAX_POSITION_PCT": settings.CRYPTO_MAX_POSITION_PCT,
            "CRYPTO_MAX_DAILY_TRADES": settings.CRYPTO_MAX_DAILY_TRADES,
            "CRYPTO_TRADING_STYLE_MODE": settings.CRYPTO_TRADING_STYLE_MODE,
            "US_LEVERAGED_PRODUCTS_ENABLED": settings.US_LEVERAGED_PRODUCTS_ENABLED,
            "US_INVERSE_PRODUCTS_ENABLED": settings.US_INVERSE_PRODUCTS_ENABLED,
            "US_LEVERAGE_ALLOWED_SESSIONS": settings.US_LEVERAGE_ALLOWED_SESSIONS,
            "US_LEVERAGE_ALLOWED_STRATEGIES": settings.US_LEVERAGE_ALLOWED_STRATEGIES,
            "US_LEVERAGE_MAX_SINGLE_ORDER_RATIO": settings.US_LEVERAGE_MAX_SINGLE_ORDER_RATIO,
        }
        settings.TRADING_ENABLED = True
        settings.MAX_SINGLE_ORDER_KRW = 10000
        settings.MIN_BUY_QUANTITY = 1
        settings.CRYPTO_TRADING_ENABLED = True
        settings.CRYPTO_MAX_SINGLE_ORDER_KRW = 0
        settings.CRYPTO_MIN_BUY_QUANTITY = 0.0
        settings.CRYPTO_MIN_CASH_RATIO = 0.10
        settings.CRYPTO_MAX_POSITION_PCT = 20.0
        settings.CRYPTO_MAX_DAILY_TRADES = 0
        settings.CRYPTO_TRADING_STYLE_MODE = "CONSERVATIVE"
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

    async def test_crypto_conservative_mode_blocks_rr_below_two_in_bull_regime(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="BTC",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=100.0,
            suggested_amount_krw=10000.0,
            target_price=116.0,
            stop_loss_price=90.0,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="AGGRESSIVE_SHORT",
            metadata={
                "market": "BITHUMB",
                "price_krw": 100.0,
            },
        )

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=50000,
                portfolio_budget=100000,
                today_trade_count=0,
                current_holding_count=0,
                market_regime="ALTSEASON",
            )

        self.assertFalse(result["approved"])
        self.assertIn("최소 2.0:1 필요", result["reason"])

    async def test_crypto_aggressive_mode_allows_rr_one_point_six_in_bull_regime(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="BTC",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=100.0,
            suggested_amount_krw=10000.0,
            target_price=116.0,
            stop_loss_price=90.0,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="AGGRESSIVE_SHORT",
            metadata={
                "market": "BITHUMB",
                "price_krw": 100.0,
            },
        )

        with patch.object(settings, "CRYPTO_TRADING_STYLE_MODE", "AGGRESSIVE"), \
                patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=50000,
                portfolio_budget=100000,
                today_trade_count=0,
                current_holding_count=0,
                market_regime="ALTSEASON",
            )

        self.assertTrue(result["approved"])
        self.assertNotIn("리스크:보상 비율 부족", result["reason"])

    async def test_stock_rr_uses_take_profit_price_before_target_price(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="AAPL",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=100.0,
            suggested_quantity=10,
            target_price=140.0,
            stop_loss_price=90.0,
            take_profit_price=110.0,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="STABLE_SHORT",
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
                current_holding_count=0,
                orderable_cash_krw=50000,
                market_regime="SIDEWAYS",
            )

        self.assertFalse(result["approved"])
        self.assertIn("리스크:보상 비율 부족", result["reason"])


if __name__ == "__main__":
    unittest.main()
