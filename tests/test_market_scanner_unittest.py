import unittest
from unittest.mock import AsyncMock, patch

from agent.market_scanner import MarketScanner
from trading.enums import ActivityPhase, ActivityType
from trading.models import AccountBalance
from trading.models import MCPResponse
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
    @staticmethod
    def _scan_complete_detail(log_mock: AsyncMock) -> dict:
        for call in log_mock.await_args_list:
            if call.args[:2] == (ActivityType.SCAN, ActivityPhase.COMPLETE):
                return call.kwargs.get("detail", {})
        raise AssertionError("SCAN COMPLETE 로그를 찾지 못했습니다.")

    @staticmethod
    def _balance(
        market: str = "NASDAQ",
        effective_cash: float = 700000,
        *,
        effective_cash_foreign: float = 0.0,
        cash_foreign: float = 0.0,
    ) -> AccountBalance:
        return AccountBalance(
            total_asset=1000000,
            cash=0,
            cash_foreign=cash_foreign,
            raw_cash=0,
            effective_cash=effective_cash,
            effective_cash_foreign=effective_cash_foreign,
            cash_source="TOTAL_ASSET_PROXY",
            stock_value=300000,
            total_pnl=0,
            total_pnl_rate=0,
            market=market,
            currency="KRW",
            exchange_rate_to_krw=1450.0,
        )

    async def test_scan_uses_effective_cash_for_available_cash(self):
        scanner = MarketScanner()
        balance = self._balance(effective_cash_foreign=700000 / 1450.0)

        with patch("agent.market_scanner.account_manager.get_account_snapshot", AsyncMock(return_value=(balance, []))), \
                patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")), \
                patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", False), \
                patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False), \
                patch("agent.market_scanner.llm_factory.generate_tier1", AsyncMock(return_value=(
                    '{"selected": [], "market_analysis": "관망", "market_regime": "SIDEWAYS"}',
                    "TEST",
                ))), \
                patch("agent.market_scanner.activity_logger.log", AsyncMock()):
            result = await scanner.scan(market="NASDAQ", cycle_id="cycle-1")

        self.assertEqual(result["available_cash"], 700000)
        self.assertAlmostEqual(result["available_cash_foreign"], 700000 / 1450.0)

    async def test_scan_uses_prefetched_account_snapshot_when_provided(self):
        scanner = MarketScanner()
        balance = self._balance(effective_cash_foreign=700000 / 1450.0)

        with patch("agent.market_scanner.account_manager.get_account_snapshot", AsyncMock()) as snapshot_mock, \
                patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")), \
                patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", False), \
                patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False), \
                patch("agent.market_scanner.llm_factory.generate_tier1", AsyncMock(return_value=(
                    '{"selected": [], "market_analysis": "관망", "market_regime": "SIDEWAYS"}',
                    "TEST",
                ))), \
                patch("agent.market_scanner.activity_logger.log", AsyncMock()):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-1",
                account_snapshot=(balance, []),
            )

        snapshot_mock.assert_not_awaited()
        self.assertEqual(result["available_cash"], 700000)
        self.assertAlmostEqual(result["available_cash_foreign"], 700000 / 1450.0)

    async def test_scan_skips_llm_when_no_affordable_candidates_remain(self):
        scanner = MarketScanner()
        balance = self._balance(market="KRX", effective_cash=1000)
        llm_mock = AsyncMock(return_value=(
            '{"selected": [], "market_analysis": "관망", "market_regime": "SIDEWAYS"}',
            "TEST",
        ))

        with patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[
            {"symbol": "005930", "name": "삼성전자", "market": "KRX", "currency": "KRW", "price": 1500},
        ])), \
                patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.market_scanner.llm_factory.generate_tier1", llm_mock), \
                patch("agent.market_scanner.activity_logger.log", AsyncMock()):
            result = await scanner.scan(
                market="KRX",
                cycle_id="cycle-1",
                account_snapshot=(balance, []),
            )

        llm_mock.assert_not_awaited()
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["market_summary"], "가용 현금 기준 1주 매수 가능 후보 없음")

    async def test_scan_keeps_affordable_stock_even_if_max_per_stock_is_lower(self):
        scanner = MarketScanner()
        balance = self._balance(market="KRX", effective_cash=100000)
        llm_mock = AsyncMock(return_value=(
            '{"selected": [{"symbol": "005930", "name": "삼성전자", "strategy_type": "STABLE_SHORT", "reason": "현금 내 매수 가능"}], "market_analysis": "기회", "market_regime": "BULL"}',
            "TEST",
        ))
        log_mock = AsyncMock()

        with patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[
            {"symbol": "005930", "name": "삼성전자", "market": "KRX", "currency": "KRW", "price": 50000},
        ])), \
                patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.market_scanner.llm_factory.generate_tier1", llm_mock), \
                patch("agent.market_scanner.activity_logger.log", log_mock):
            result = await scanner.scan(
                market="KRX",
                cycle_id="cycle-1",
                dynamic_limits={"max_position_pct": 10.0},
                account_snapshot=(balance, []),
            )

        llm_mock.assert_awaited()
        self.assertEqual(result["max_per_stock"], 10000)
        self.assertEqual([item["symbol"] for item in result["selected"]], ["005930"])
        detail = self._scan_complete_detail(log_mock)
        self.assertEqual(detail["affordability_filter"]["volume_rank"], {"before": 1, "after": 1, "dropped": 0})

    async def test_scan_filters_unaffordable_selected_symbol_from_llm(self):
        scanner = MarketScanner()
        balance = self._balance(market="KRX", effective_cash=60000)

        with patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[
            {"symbol": "005930", "name": "삼성전자", "market": "KRX", "currency": "KRW", "price": 50000},
            {"symbol": "000660", "name": "SK하이닉스", "market": "KRX", "currency": "KRW", "price": 120000},
        ])), \
                patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.market_scanner.llm_factory.generate_tier1", AsyncMock(return_value=(
                    '{"selected": [{"symbol": "005930", "name": "삼성전자", "strategy_type": "STABLE_SHORT", "reason": "가능"}, {"symbol": "000660", "name": "SK하이닉스", "strategy_type": "STABLE_SHORT", "reason": "비쌈"}], "market_analysis": "기회", "market_regime": "BULL"}',
                    "TEST",
                ))), \
                patch("agent.market_scanner.activity_logger.log", AsyncMock()):
            result = await scanner.scan(
                market="KRX",
                cycle_id="cycle-1",
                account_snapshot=(balance, []),
            )

        self.assertEqual([item["symbol"] for item in result["selected"]], ["005930"])

    async def test_scan_filters_us_candidates_using_real_orderable_cash_usd(self):
        scanner = MarketScanner()
        balance = self._balance(
            market="NASDAQ",
            effective_cash=100000,
            effective_cash_foreign=100000 / 1450.0,
        )
        llm_mock = AsyncMock(return_value=(
            '{"selected": [], "market_analysis": "관망", "market_regime": "SIDEWAYS"}',
            "TEST",
        ))
        log_mock = AsyncMock()

        with patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[
            {"symbol": "AAPL", "name": "Apple", "market": "NASDAQ", "currency": "USD", "price": 100.0},
        ])), \
                patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])), \
                patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")), \
                patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", False), \
                patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False), \
                patch("agent.market_scanner.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1400.0)), \
                patch("agent.market_scanner.llm_factory.generate_tier1", llm_mock), \
                patch("agent.market_scanner.activity_logger.log", log_mock):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-1",
                account_snapshot=(balance, []),
            )

        llm_mock.assert_not_awaited()
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["market_summary"], "가용 현금 기준 1주 매수 가능 후보 없음")
        self.assertAlmostEqual(result["available_cash_foreign"], 100000 / 1450.0)
        detail = self._scan_complete_detail(log_mock)
        self.assertEqual(detail["affordability_filter"]["volume_rank"], {"before": 1, "after": 0, "dropped": 1})


class MarketScannerPremarketJunkFilterTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _stock(
        symbol: str,
        *,
        price: float,
        change_rate: float,
        volume: int,
        trade_value_usd: float,
        market: str = "NASDAQ",
    ) -> dict:
        return {
            "symbol": symbol,
            "name": symbol,
            "market": market,
            "currency": "USD",
            "price": price,
            "current_price": price,
            "change": round(price * (change_rate / 100.0), 4),
            "change_rate": change_rate,
            "volume": volume,
            "trade_value_usd": trade_value_usd,
            "scan_source": "DISCOVERY",
        }

    async def test_scan_filters_premarket_junk_candidates_before_llm(self):
        scanner = MarketScanner()
        balance = MarketScannerCashTest._balance(
            market="NASDAQ",
            effective_cash=700000,
            effective_cash_foreign=500.0,
        )
        junk_stock = self._stock(
            "JUNK",
            price=2.5,
            change_rate=45.0,
            volume=500000,
            trade_value_usd=2_000_000.0,
        )
        llm_mock = AsyncMock(return_value=(
            '{"selected": [], "market_analysis": "관망", "market_regime": "SIDEWAYS"}',
            "TEST",
        ))
        log_mock = AsyncMock()

        with (
            patch.object(settings, "US_PREMARKET_ENABLED", True),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", True),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_PROFILE", "MODERATE"),
            patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False),
            patch.object(settings, "US_PREMARKET_EXPANSION_MAX_STAGE", 0),
            patch.object(settings, "US_SCAN_MARKETS", "NASDAQ"),
            patch("agent.market_scanner.market_calendar.get_market_session", return_value="US_PRE"),
            patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[junk_stock])),
            patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])),
            patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")),
            patch(
                "agent.market_scanner.mcp_client.get_current_price_detail",
                AsyncMock(return_value=MCPResponse(success=True, data=junk_stock)),
            ),
            patch("agent.market_scanner.llm_factory.generate_tier1", llm_mock),
            patch("agent.market_scanner.activity_logger.log", log_mock),
        ):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-1",
                account_snapshot=(balance, []),
            )

        llm_mock.assert_not_awaited()
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["market_summary"], "프리마켓 잡주 필터 기준 후보 없음")
        detail = MarketScannerCashTest._scan_complete_detail(log_mock)
        self.assertEqual(detail["junk_filter"]["volume_rank"]["dropped"], 1)
        self.assertEqual(detail["junk_filter"]["volume_rank"]["reasons"]["price_below_min"], 1)

    async def test_scan_expands_candidates_when_premarket_pool_is_too_small(self):
        scanner = MarketScanner()
        balance = MarketScannerCashTest._balance(
            market="NASDAQ",
            effective_cash=2_000_000,
            effective_cash_foreign=2_000.0,
        )
        stock_map = {
            "AAPL": self._stock("AAPL", price=15.0, change_rate=4.0, volume=400000, trade_value_usd=6_000_000.0),
            "MSFT": self._stock("MSFT", price=18.0, change_rate=3.0, volume=350000, trade_value_usd=6_300_000.0),
            "PLTR": self._stock("PLTR", price=20.0, change_rate=6.0, volume=500000, trade_value_usd=10_000_000.0),
            "SOFI": self._stock("SOFI", price=8.0, change_rate=12.0, volume=600000, trade_value_usd=4_800_000.0),
            "F": self._stock("F", price=11.0, change_rate=-6.0, volume=500000, trade_value_usd=5_500_000.0, market="NYSE"),
            "HOOD": self._stock("HOOD", price=22.0, change_rate=9.0, volume=450000, trade_value_usd=9_900_000.0),
            "NIO": self._stock("NIO", price=7.0, change_rate=-7.0, volume=700000, trade_value_usd=4_900_000.0),
        }

        async def volume_side_effect(markets, limit=30, include_trade_growth=False):
            if limit == 30:
                return [stock_map["AAPL"]]
            if include_trade_growth:
                return [stock_map["AAPL"], stock_map["MSFT"], stock_map["PLTR"]]
            return [stock_map["AAPL"], stock_map["MSFT"]]

        async def fluctuation_side_effect(markets, sort, limit=30):
            if limit == 30:
                return []
            if sort == "top":
                return [stock_map["SOFI"]]
            return [stock_map["F"]]

        async def price_detail_side_effect(symbol, market="NASDAQ"):
            return MCPResponse(success=True, data=stock_map[symbol])

        llm_mock = AsyncMock(return_value=(
            '{"selected": [], "market_analysis": "관망", "market_regime": "SIDEWAYS"}',
            "TEST",
        ))
        log_mock = AsyncMock()

        with (
            patch.object(settings, "US_PREMARKET_ENABLED", True),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", True),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_PROFILE", "MODERATE"),
            patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", True),
            patch.object(settings, "US_PREMARKET_EXPANSION_MAX_STAGE", 3),
            patch.object(settings, "US_PREMARKET_MIN_MONITOR_CANDIDATES", 4),
            patch.object(settings, "US_SCAN_MARKETS", "NASDAQ,NYSE"),
            patch("agent.market_scanner.market_calendar.get_market_session", return_value="US_PRE"),
            patch.object(scanner, "_get_volume_rank", AsyncMock(side_effect=volume_side_effect)),
            patch.object(scanner, "_get_fluctuation_rank", AsyncMock(side_effect=fluctuation_side_effect)),
            patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")),
            patch.object(
                scanner,
                "_build_us_expansion_candidates",
                AsyncMock(return_value=[stock_map["HOOD"], stock_map["NIO"]]),
            ),
            patch(
                "agent.market_scanner.mcp_client.get_current_price_detail",
                AsyncMock(side_effect=price_detail_side_effect),
            ),
            patch("agent.market_scanner.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1400.0)),
            patch("agent.market_scanner.llm_factory.generate_tier1", llm_mock),
            patch("agent.market_scanner.activity_logger.log", log_mock),
        ):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-1",
                account_snapshot=(balance, []),
            )

        llm_mock.assert_awaited()
        self.assertEqual(result["selected"], [])
        detail = MarketScannerCashTest._scan_complete_detail(log_mock)
        self.assertTrue(detail["expansion"]["triggered"])
        self.assertEqual(detail["expansion"]["final_stage"], 3)
        self.assertEqual(detail["expansion"]["stage_counts"][-1]["supplemental_count"], 2)


class MarketScannerRankTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_volume_rank_sorts_across_markets(self):
        scanner = MarketScanner()
        responses = [
            MCPResponse(
                success=True,
                data={
                    "stocks": [
                        {"symbol": "AAPL", "market": "NASDAQ", "volume": 100},
                        {"symbol": "MSFT", "market": "NASDAQ", "volume": 90},
                    ],
                },
            ),
            MCPResponse(
                success=True,
                data={
                    "stocks": [
                        {"symbol": "BAC", "market": "NYSE", "volume": 1000},
                    ],
                },
            ),
        ]

        with patch("agent.market_scanner.mcp_client.get_volume_rank", new=AsyncMock(side_effect=responses)):
            stocks = await scanner._get_volume_rank(["NASDAQ", "NYSE"])

        self.assertEqual([item["symbol"] for item in stocks[:3]], ["BAC", "AAPL", "MSFT"])


if __name__ == "__main__":
    unittest.main()
