import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
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
            "US_REGULAR_MIN_SELECTED_CANDIDATES": settings.US_REGULAR_MIN_SELECTED_CANDIDATES,
            "US_REGULAR_OPENING_GUARD_ENABLED": settings.US_REGULAR_OPENING_GUARD_ENABLED,
            "US_REGULAR_OPENING_GUARD_MIN_PRICE_USD": settings.US_REGULAR_OPENING_GUARD_MIN_PRICE_USD,
            "US_REGULAR_OPENING_GUARD_LOW_PRICE_MAX_ABS_CHANGE_PCT": settings.US_REGULAR_OPENING_GUARD_LOW_PRICE_MAX_ABS_CHANGE_PCT,
            "US_REGULAR_OPENING_GUARD_MID_PRICE_MAX_ABS_CHANGE_PCT": settings.US_REGULAR_OPENING_GUARD_MID_PRICE_MAX_ABS_CHANGE_PCT,
            "US_LATE_CYCLE_CANDIDATE_CAP": settings.US_LATE_CYCLE_CANDIDATE_CAP,
        }
        settings.US_LEVERAGED_PRODUCTS_ENABLED = True
        settings.US_INVERSE_PRODUCTS_ENABLED = True
        settings.US_LEVERAGE_ALLOWED_SESSIONS = "US_REGULAR"
        settings.US_LEVERAGE_ALLOWED_STRATEGIES = "STABLE_SHORT"
        settings.US_REGULAR_MIN_SELECTED_CANDIDATES = 3
        settings.US_REGULAR_OPENING_GUARD_ENABLED = True
        settings.US_REGULAR_OPENING_GUARD_MIN_PRICE_USD = 5.0
        settings.US_REGULAR_OPENING_GUARD_LOW_PRICE_MAX_ABS_CHANGE_PCT = 20.0
        settings.US_REGULAR_OPENING_GUARD_MID_PRICE_MAX_ABS_CHANGE_PCT = 50.0
        settings.US_LATE_CYCLE_CANDIDATE_CAP = 2

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

    def test_selection_target_range_keeps_floor_for_us_regular_late_session(self):
        self.assertEqual(
            MarketScanner._selection_target_range("NASDAQ", "US_REGULAR", 20),
            "3~4",
        )

    def test_apply_us_regular_opening_guard_filters_low_price_hot_mover(self):
        scanner = MarketScanner()

        filtered, stats = scanner._apply_us_regular_opening_guard(
            [{
                "symbol": "VSA",
                "name": "VSA",
                "market": "NASDAQ",
                "price": 2.22,
                "current_price": 2.22,
                "change_rate": 62.03,
            }],
            settings.us_regular_opening_guard_thresholds,
        )

        self.assertEqual(filtered, [])
        self.assertEqual(stats["dropped"], 1)
        self.assertEqual(stats["reasons"]["opening_low_price_hot_mover"], 1)

    def test_apply_us_regular_opening_guard_filters_mid_price_extreme_mover(self):
        scanner = MarketScanner()

        filtered, stats = scanner._apply_us_regular_opening_guard(
            [{
                "symbol": "ONCO",
                "name": "ONCO",
                "market": "NASDAQ",
                "price": 6.05,
                "current_price": 6.05,
                "change_rate": 88.0,
            }],
            settings.us_regular_opening_guard_thresholds,
        )

        self.assertEqual(filtered, [])
        self.assertEqual(stats["dropped"], 1)
        self.assertEqual(stats["reasons"]["opening_mid_price_extreme_mover"], 1)

    def test_cap_selected_candidates_limits_late_us_session(self):
        limited, stats = MarketScanner._cap_selected_candidates(
            [
                {"symbol": "AAPL"},
                {"symbol": "MSFT"},
                {"symbol": "NVDA"},
            ],
            market="NASDAQ",
            session="US_REGULAR",
            minutes_until_cutoff=20,
        )

        self.assertEqual([item["symbol"] for item in limited], ["AAPL", "MSFT"])
        self.assertEqual(stats, {"cap": 2, "before": 3, "after": 2, "dropped": 1})


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

    async def test_scan_backfills_us_regular_selection_to_minimum_three(self):
        scanner = MarketScanner()
        balance = self._balance(
            market="NASDAQ",
            effective_cash=700000,
            effective_cash_foreign=500.0,
        )
        affordable = [
            {"symbol": "AAPL", "name": "Apple", "market": "NASDAQ", "currency": "USD", "price": 100.0},
            {"symbol": "MSFT", "name": "Microsoft", "market": "NASDAQ", "currency": "USD", "price": 110.0},
            {"symbol": "PLTR", "name": "Palantir", "market": "NASDAQ", "currency": "USD", "price": 90.0},
            {"symbol": "SOFI", "name": "SoFi", "market": "NASDAQ", "currency": "USD", "price": 12.0},
        ]
        log_mock = AsyncMock()

        with (
            patch("agent.market_scanner.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch(
                "util.time_util.now_kst",
                return_value=datetime(2026, 4, 2, 23, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            ),
            patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=affordable)),
            patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])),
            patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", False),
            patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False),
            patch("agent.market_scanner.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1400.0)),
            patch(
                "agent.market_scanner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"selected": [{"symbol": "MSFT", "name": "Microsoft", "market": "NASDAQ", "strategy_type": "STABLE_SHORT", "reason": "최우선"}], "market_analysis": "기회", "market_regime": "BULL"}',
                    "TEST",
                )),
            ),
            patch("agent.market_scanner.activity_logger.log", log_mock),
        ):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-regular-floor",
                account_snapshot=(balance, []),
            )

        self.assertEqual([item["symbol"] for item in result["selected"][:3]], ["MSFT", "AAPL", "PLTR"])
        detail = self._scan_complete_detail(log_mock)
        self.assertEqual(detail["regular_floor"]["target"], 3)
        self.assertFalse(detail["regular_floor"]["triggered"])
        self.assertEqual(detail["regular_floor"]["candidate_pool_count"], 4)
        self.assertEqual(detail["regular_floor"]["backfilled_count"], 2)
        self.assertFalse(detail["regular_floor"]["unmet_floor"])

    async def test_scan_expands_us_regular_pool_when_affordable_candidates_are_too_few(self):
        scanner = MarketScanner()
        balance = self._balance(
            market="NASDAQ",
            effective_cash=700000,
            effective_cash_foreign=500.0,
        )
        stock_map = {
            "AAPL": {"symbol": "AAPL", "name": "Apple", "market": "NASDAQ", "currency": "USD", "price": 100.0},
            "MSFT": {"symbol": "MSFT", "name": "Microsoft", "market": "NASDAQ", "currency": "USD", "price": 110.0},
            "PLTR": {"symbol": "PLTR", "name": "Palantir", "market": "NASDAQ", "currency": "USD", "price": 90.0},
        }
        log_mock = AsyncMock()

        async def volume_side_effect(markets, limit=30, include_trade_growth=False):
            if limit == 30:
                return [stock_map["AAPL"]]
            return [stock_map["AAPL"], stock_map["MSFT"]]

        async def fluctuation_side_effect(markets, sort, limit=30):
            if limit == 30:
                return []
            return [stock_map["PLTR"]] if sort == "top" else []

        with (
            patch("agent.market_scanner.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch(
                "util.time_util.now_kst",
                return_value=datetime(2026, 4, 2, 23, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            ),
            patch.object(scanner, "_get_volume_rank", AsyncMock(side_effect=volume_side_effect)),
            patch.object(scanner, "_get_fluctuation_rank", AsyncMock(side_effect=fluctuation_side_effect)),
            patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")),
            patch.object(scanner, "_build_us_expansion_candidates", AsyncMock(return_value=[])),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", False),
            patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False),
            patch("agent.market_scanner.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1400.0)),
            patch(
                "agent.market_scanner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"selected": [{"symbol": "AAPL", "name": "Apple", "market": "NASDAQ", "strategy_type": "STABLE_SHORT", "reason": "기준"}], "market_analysis": "기회", "market_regime": "BULL"}',
                    "TEST",
                )),
            ),
            patch("agent.market_scanner.activity_logger.log", log_mock),
        ):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-regular-expand",
                account_snapshot=(balance, []),
            )

        self.assertEqual([item["symbol"] for item in result["selected"][:3]], ["AAPL", "MSFT", "PLTR"])
        detail = self._scan_complete_detail(log_mock)
        self.assertTrue(detail["regular_floor"]["triggered"])
        self.assertEqual(detail["regular_floor"]["base_affordable_count"], 1)
        self.assertEqual(detail["regular_floor"]["expanded_affordable_count"], 3)
        self.assertEqual(detail["regular_floor"]["backfilled_count"], 2)
        self.assertFalse(detail["regular_floor"]["unmet_floor"])

    async def test_scan_marks_unmet_floor_when_only_two_product_allowed_candidates_exist(self):
        scanner = MarketScanner()
        balance = self._balance(
            market="NASDAQ",
            effective_cash=700000,
            effective_cash_foreign=500.0,
        )
        candidates = [
            {"symbol": "TQQQ", "name": "ProShares UltraPro QQQ", "market": "NASDAQ", "currency": "USD", "price": 50.0},
            {"symbol": "AAPL", "name": "Apple", "market": "NASDAQ", "currency": "USD", "price": 100.0},
            {"symbol": "MSFT", "name": "Microsoft", "market": "NASDAQ", "currency": "USD", "price": 110.0},
        ]
        log_mock = AsyncMock()

        with (
            patch("agent.market_scanner.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch.object(settings, "US_LEVERAGED_PRODUCTS_ENABLED", False),
            patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=candidates)),
            patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])),
            patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", False),
            patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False),
            patch("agent.market_scanner.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1400.0)),
            patch(
                "agent.market_scanner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"selected": [{"symbol": "AAPL", "name": "Apple", "market": "NASDAQ", "strategy_type": "STABLE_SHORT", "reason": "기준"}], "market_analysis": "기회", "market_regime": "BULL"}',
                    "TEST",
                )),
            ),
            patch("agent.market_scanner.activity_logger.log", log_mock),
        ):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-regular-unmet",
                account_snapshot=(balance, []),
            )

        self.assertEqual([item["symbol"] for item in result["selected"]], ["AAPL", "MSFT"])
        detail = self._scan_complete_detail(log_mock)
        self.assertEqual(detail["regular_floor"]["candidate_pool_count"], 2)
        self.assertEqual(detail["regular_floor"]["backfilled_count"], 1)
        self.assertTrue(detail["regular_floor"]["unmet_floor"])


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


class MarketScannerRegularOpeningGuardTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _stock(
        symbol: str,
        *,
        price: float,
        change_rate: float,
        volume: int = 500_000,
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
        }

    async def test_scan_filters_opening_hot_mover_before_llm(self):
        scanner = MarketScanner()
        balance = MarketScannerCashTest._balance(
            market="NASDAQ",
            effective_cash=700000,
            effective_cash_foreign=500.0,
        )
        llm_mock = AsyncMock(return_value=(
            '{"selected": [], "market_analysis": "관망", "market_regime": "SIDEWAYS"}',
            "TEST",
        ))
        log_mock = AsyncMock()
        vsa = self._stock("VSA", price=2.22, change_rate=62.03)

        with (
            patch.object(settings, "US_REGULAR_OPENING_GUARD_ENABLED", True),
            patch("util.time_util.now_kst", return_value=datetime(2026, 3, 27, 22, 45, tzinfo=ZoneInfo("Asia/Seoul"))),
            patch("agent.market_scanner.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch.object(scanner, "_get_volume_rank", AsyncMock(return_value=[vsa])),
            patch.object(scanner, "_get_fluctuation_rank", AsyncMock(return_value=[])),
            patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", False),
            patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False),
            patch("agent.market_scanner.llm_factory.generate_tier1", llm_mock),
            patch("agent.market_scanner.activity_logger.log", log_mock),
        ):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-opening-empty",
                account_snapshot=(balance, []),
            )

        llm_mock.assert_not_awaited()
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["market_summary"], "정규장 오프닝 가드 기준 후보 없음")
        detail = MarketScannerCashTest._scan_complete_detail(log_mock)
        self.assertEqual(detail["opening_guard"]["volume_rank"]["dropped"], 1)
        self.assertEqual(
            detail["opening_guard"]["volume_rank"]["reasons"]["opening_low_price_hot_mover"],
            1,
        )

    async def test_scan_opening_guard_does_not_reintroduce_filtered_candidates_via_backfill(self):
        scanner = MarketScanner()
        balance = MarketScannerCashTest._balance(
            market="NASDAQ",
            effective_cash=700000,
            effective_cash_foreign=500.0,
        )
        stock_map = {
            "AAPL": self._stock("AAPL", price=100.0, change_rate=4.0),
            "MSFT": self._stock("MSFT", price=110.0, change_rate=5.0),
            "PLTR": self._stock("PLTR", price=90.0, change_rate=7.0),
            "VSA": self._stock("VSA", price=2.22, change_rate=62.03),
        }
        log_mock = AsyncMock()

        async def volume_side_effect(markets, limit=30, include_trade_growth=False):
            if limit == 30:
                return [stock_map["AAPL"]]
            return [stock_map["AAPL"], stock_map["MSFT"], stock_map["VSA"]]

        async def fluctuation_side_effect(markets, sort, limit=30):
            if limit == 30:
                return []
            return [stock_map["PLTR"]] if sort == "top" else []

        with (
            patch.object(settings, "US_REGULAR_OPENING_GUARD_ENABLED", True),
            patch("util.time_util.now_kst", return_value=datetime(2026, 3, 27, 22, 45, tzinfo=ZoneInfo("Asia/Seoul"))),
            patch("agent.market_scanner.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch.object(scanner, "_get_volume_rank", AsyncMock(side_effect=volume_side_effect)),
            patch.object(scanner, "_get_fluctuation_rank", AsyncMock(side_effect=fluctuation_side_effect)),
            patch.object(scanner, "_get_performance_summary", AsyncMock(return_value="매매 이력 없음")),
            patch.object(scanner, "_build_us_expansion_candidates", AsyncMock(return_value=[])),
            patch.object(settings, "US_PREMARKET_JUNK_FILTER_ENABLED", False),
            patch.object(settings, "US_PREMARKET_EXPANSION_ENABLED", False),
            patch(
                "agent.market_scanner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"selected": [{"symbol": "AAPL", "name": "AAPL", "market": "NASDAQ", "strategy_type": "STABLE_SHORT", "reason": "기준"}], "market_analysis": "기회", "market_regime": "BULL"}',
                    "TEST",
                )),
            ),
            patch("agent.market_scanner.activity_logger.log", log_mock),
        ):
            result = await scanner.scan(
                market="NASDAQ",
                cycle_id="cycle-opening-floor",
                account_snapshot=(balance, []),
            )

        self.assertEqual([item["symbol"] for item in result["selected"]], ["AAPL", "MSFT", "PLTR"])
        detail = MarketScannerCashTest._scan_complete_detail(log_mock)
        self.assertTrue(detail["regular_floor"]["triggered"])
        self.assertEqual(detail["regular_floor"]["candidate_pool_count"], 3)
        self.assertEqual(detail["regular_floor"]["backfilled_count"], 2)
        self.assertEqual(
            detail["opening_guard"]["volume_rank"]["reasons"]["opening_low_price_hot_mover"],
            1,
        )


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
