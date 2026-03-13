import unittest
from unittest.mock import AsyncMock, patch

from agent.market_scanner import MarketScanner
from trading.models import AccountBalance
from core.config import settings


class MarketScannerPolicyTest(unittest.TestCase):
    def setUp(self):
        self._original = {
            "US_LEVERAGED_PRODUCTS_ENABLED": settings.US_LEVERAGED_PRODUCTS_ENABLED,
            "US_INVERSE_PRODUCTS_ENABLED": settings.US_INVERSE_PRODUCTS_ENABLED,
            "US_LEVERAGE_ALLOWED_SESSIONS": settings.US_LEVERAGE_ALLOWED_SESSIONS,
            "US_LEVERAGE_ALLOWED_STRATEGIES": settings.US_LEVERAGE_ALLOWED_STRATEGIES,
        }
        settings.US_LEVERAGED_PRODUCTS_ENABLED = True
        settings.US_INVERSE_PRODUCTS_ENABLED = True
        settings.US_LEVERAGE_ALLOWED_SESSIONS = "US_REGULAR"
        settings.US_LEVERAGE_ALLOWED_STRATEGIES = "STABLE_SHORT"

    def tearDown(self):
        for field_name, value in self._original.items():
            setattr(settings, field_name, value)

    def test_apply_product_policy_coerces_leveraged_strategy(self):
        scanner = MarketScanner()
        selected = [{
            "symbol": "TQQQ",
            "name": "ProShares UltraPro QQQ",
            "market": "NASDAQ",
            "strategy_type": "AGGRESSIVE_SHORT",
        }]

        with patch("agent.market_scanner.market_calendar.get_market_session", return_value="US_REGULAR"):
            filtered = scanner._apply_product_policy(selected)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["strategy_type"], "STABLE_SHORT")
        self.assertTrue(filtered[0]["is_leveraged"])


class MarketScannerCashTest(unittest.IsolatedAsyncioTestCase):
    async def test_scan_uses_effective_cash_for_available_cash(self):
        scanner = MarketScanner()
        balance = AccountBalance(
            total_asset=1000000,
            cash=0,
            raw_cash=0,
            effective_cash=700000,
            cash_source="TOTAL_ASSET_PROXY",
            stock_value=300000,
            total_pnl=0,
            total_pnl_rate=0,
            market="NASDAQ",
            currency="KRW",
            exchange_rate_to_krw=1450.0,
        )

        with patch("agent.market_scanner.account_manager.get_account_snapshot", AsyncMock(return_value=(balance, []))), \
                patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.market_scanner.llm_factory.generate_tier1", AsyncMock(return_value=(
                    '{"selected": [], "market_analysis": "관망", "market_regime": "SIDEWAYS"}',
                    "TEST",
                ))), \
                patch("agent.market_scanner.activity_logger.log", AsyncMock()):
            result = await scanner.scan(cycle_id="cycle-1")

        self.assertEqual(result["available_cash"], 700000)


if __name__ == "__main__":
    unittest.main()
