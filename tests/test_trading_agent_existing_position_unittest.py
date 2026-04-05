import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

from agent.trading_agent import TradingAgent
from analysis.chart_analyzer import ChartAnalysisResult
from analysis.technical.trend_analyzer import TrendReport
from core.config import settings
from core.events import Event, EventType
from scheduler.market_calendar import market_calendar
from trading.enums import ActivityPhase, ActivityType
from trading.models import AccountBalance, HoldingInfo, MCPResponse


class TradingAgentExistingPositionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = TradingAgent()

    @staticmethod
    def _balance() -> AccountBalance:
        return AccountBalance(
            total_asset=300_000_000,
            total_asset_foreign=202_730.10,
            cash=100_000_000,
            cash_foreign=67_576.70,
            stock_value=200_000_000,
            stock_value_foreign=135_153.40,
            total_pnl=0.0,
            total_pnl_rate=0.0,
            market="NASDAQ",
            currency="USD",
            exchange_rate_to_krw=1479.8,
            raw_cash=100_000_000,
            raw_cash_foreign=67_576.70,
            effective_cash=100_000_000,
            effective_cash_foreign=67_576.70,
            cash_source="BROKER",
            is_valid=True,
        )

    @staticmethod
    def _holding(
        symbol: str = "PLTR",
        quantity: int = 1000,
        *,
        current_price: float = 149.63,
        pnl: float = -4050.0,
        pnl_rate: float = -2.64,
    ) -> HoldingInfo:
        return HoldingInfo(
            symbol=symbol,
            name="Palantir",
            market="NASDAQ",
            currency="USD",
            quantity=quantity,
            avg_buy_price=153.68,
            current_price=current_price,
            pnl=pnl,
            pnl_rate=pnl_rate,
            exchange_rate_to_krw=1479.8,
        )

    def test_format_current_position_for_prompt_uses_usd_only_for_us_holdings(self):
        text = self.agent._format_current_position_for_prompt(
            {
                "symbol": "PLTR",
                "market": "NASDAQ",
                "currency": "USD",
                "quantity": 1000,
                "avg_buy_price": 153.68,
                "current_price": 149.63,
                "pnl": -4050.0,
                "pnl_rate": -2.64,
                "current_value_krw": 221_421_074.0,
                "position_pct": 73.8,
            }
        )

        self.assertIn("평가손익: -2.64% (-4,050.00USD)", text)
        self.assertIn("현재 비중: 73.8% (약 149,630.00USD)", text)
        self.assertNotIn("원", text)

    def test_format_product_context_for_prompt_includes_market_alignment(self):
        text = self.agent._format_product_context_for_prompt(
            {
                "product_type": "INVERSE_ETF",
                "is_inverse": True,
                "leverage_multiplier": 1.0,
                "restricted_product": True,
                "classification_source": "override",
                "etp_type_name": "ETF",
                "market_bias": "BEAR",
                "market_alignment": "ALIGNED",
                "alignment_reason": "약세장과 같은 방향의 순노출",
            }
        )

        self.assertIn("노출 배수: -1x", text)
        self.assertIn("시장 방향 정합성: BEAR 정합", text)
        self.assertIn("약세장과 같은 방향의 순노출", text)
        self.assertIn("ETP 유형 힌트: ETF", text)

    def test_build_account_context_does_not_convert_krw_totals_to_usd_without_foreign_snapshot(self):
        context = self.agent._build_account_context(
            market="NASDAQ",
            portfolio_snapshot={
                "cash": 100_000_000,
                "total_asset": 300_000_000,
                "holding_count": 0,
            },
            current_position=None,
            dynamic_limits={"max_single_order_krw": 30_000_000, "max_position_pct": 25.0, "min_cash_ratio": 0.05},
            current_price=149.63,
            currency="USD",
            exchange_rate_to_krw=1479.8,
            orderable_amount_context=None,
        )

        self.assertIn("총자산: 조회값 없음 (USD)", context["text"])
        self.assertIn("가용 현금: 조회값 없음 (USD)", context["text"])
        self.assertNotIn("202,730.10USD", context["text"])
        self.assertNotIn("67,576.70USD", context["text"])

    async def test_tier1_prompt_includes_current_position_and_account_context(self):
        chart_result = ChartAnalysisResult(
            indicators_text="지표",
            patterns_text="패턴",
            trend_text="추세",
        )
        current_position = {
            "symbol": "PLTR",
            "market": "NASDAQ",
            "currency": "USD",
            "quantity": 1000,
            "avg_buy_price": 153.68,
            "current_price": 149.63,
            "pnl": -4050.0,
            "pnl_rate": -2.64,
            "exchange_rate_to_krw": 1479.8,
            "current_value_krw": 221_421_074.0,
            "position_pct": 73.8,
        }

        with patch(
            "agent.trading_agent.llm_factory.generate_tier1",
            AsyncMock(return_value=('{"recommendation":"HOLD","confidence":0.4}', "CODEX_CLI")),
        ) as tier1_mock:
            await self.agent._tier1_analysis(
                symbol="PLTR",
                name="팔란티어 테크",
                current_price=149.63,
                chart_result=chart_result,
                price_data={
                    "market": "NASDAQ",
                    "currency": "USD",
                    "price": 149.63,
                    "change": 3.87,
                    "change_rate": -2.52,
                    "volume": 12_725_830,
                },
                feedback_context="매매 이력 없음",
                current_position_context=(
                    "- 현재 보유 여부: 보유 중\n"
                    "- 보유 수량: 1000주\n"
                    "- 평균단가: 153.68USD"
                ),
                portfolio_snapshot={
                    "cash": 100_000_000,
                    "cash_foreign": 67_576.70,
                    "effective_cash_foreign": 67_576.70,
                    "total_asset": 300_000_000,
                    "total_asset_foreign": 202_730.10,
                    "holding_count": 1,
                },
                current_position=current_position,
                dynamic_limits={"max_single_order_krw": 30_000_000, "max_position_pct": 25.0, "min_cash_ratio": 0.05},
                market_context="시장 컨텍스트 없음",
                trading_context="현재 세션: US_REGULAR",
                orderable_amount_context={
                    "orderable_amount_source": "INQUIRE_PSAMOUNT",
                    "orderable_amount_krw": 6_500_000,
                    "orderable_amount_foreign": 4_392.0,
                    "orderable_qty": 43,
                },
                cycle_id="cycle-1",
            )

        prompt = tier1_mock.await_args.args[0]
        self.assertIn("### 현재 종목 포지션", prompt)
        self.assertIn("보유 수량: 1000주", prompt)
        self.assertIn("### 계좌 상태", prompt)
        self.assertIn("실주문 기준 현금", prompt)
        self.assertIn("총자산: 202,730.10USD", prompt)
        self.assertIn("4,392.00USD", prompt)
        self.assertIn("현재 이 종목 비중: 73.8%", prompt)
        self.assertNotIn("300,000,000원", prompt)
        self.assertNotIn("6,500,000원", prompt)
        self.assertNotIn("### 기존 보유 계획 상태", prompt)

    async def test_tier2_review_formats_us_prompt_without_krw_values(self):
        with patch(
            "agent.trading_agent.llm_factory.generate_tier2",
            AsyncMock(return_value=('{"approved": true, "action": "HOLD", "confidence": 0.41, "reason": "관망", "risk_warnings": []}', "CODEX_CLI")),
        ) as tier2_mock:
            await self.agent._tier2_review(
                symbol="PLTR",
                name="팔란티어 테크",
                current_price=149.63,
                strategy_type="STABLE_SHORT",
                tier1_analysis={
                    "market": "NASDAQ",
                    "currency": "USD",
                    "exchange_rate_to_krw": 1479.8,
                    "confidence": 0.72,
                },
                market="NASDAQ",
                market_context="시장 컨텍스트 없음",
                trading_context="현재 세션: US_REGULAR",
                portfolio_snapshot={
                    "cash": 100_000_000,
                    "cash_foreign": 67_576.70,
                    "effective_cash_foreign": 67_576.70,
                    "total_asset": 100_000_000,
                    "total_asset_foreign": 67_576.70,
                    "holding_count": 0,
                },
                dynamic_limits={
                    "max_single_order_krw": 30_000_000,
                    "max_position_pct": 100.0,
                    "min_cash_ratio": 0.0,
                },
                orderable_amount_context={
                    "orderable_amount_source": "INQUIRE_PSAMOUNT",
                    "orderable_amount_krw": 6_500_000,
                    "orderable_amount_foreign": 4_392.0,
                    "orderable_qty": 43,
                },
                cycle_id="cycle-tier2-1",
            )

        prompt = tier2_mock.await_args.args[0]
        self.assertIn("실주문 기준 현금: 4,392.00USD", prompt)
        self.assertIn("종목당 최대: 4,392.00USD", prompt)
        self.assertNotIn("환산 참고", prompt)
        self.assertNotIn("6,500,000원", prompt)

    async def test_tier2_review_rejects_external_link_response(self):
        with patch(
            "agent.trading_agent.llm_factory.generate_tier2",
            AsyncMock(return_value=('{"approved": true, "action": "BUY", "reason": "https://www.reuters.com 기사 확인", "risk_warnings": []}', "CODEX_CLI")),
        ):
            result = await self.agent._tier2_review(
                symbol="PLTR",
                name="팔란티어 테크",
                current_price=149.63,
                strategy_type="STABLE_SHORT",
                tier1_analysis={
                    "market": "NASDAQ",
                    "currency": "USD",
                    "exchange_rate_to_krw": 1479.8,
                    "confidence": 0.72,
                },
                market="NASDAQ",
                market_context="시장 컨텍스트 없음",
                trading_context="현재 세션: US_REGULAR",
                portfolio_snapshot={
                    "cash": 100_000_000,
                    "cash_foreign": 67_576.70,
                    "effective_cash_foreign": 67_576.70,
                    "total_asset": 100_000_000,
                    "total_asset_foreign": 67_576.70,
                    "holding_count": 0,
                },
                dynamic_limits={
                    "max_single_order_krw": 30_000_000,
                    "max_position_pct": 100.0,
                    "min_cash_ratio": 0.0,
                },
                orderable_amount_context={
                    "orderable_amount_source": "INQUIRE_PSAMOUNT",
                    "orderable_amount_krw": 6_500_000,
                    "orderable_amount_foreign": 4_392.0,
                    "orderable_qty": 43,
                },
                cycle_id="cycle-tier2-link",
            )

        self.assertFalse(result["approved"])
        self.assertEqual(result["action"], "HOLD")
        self.assertIn("외부 링크", result["reason"])

    async def test_build_trading_context_uses_premarket_scalp_mode_for_us_pre(self):
        balance = self._balance()
        with patch.object(settings, "US_PREMARKET_SCALP_ENABLED", True), \
                patch("util.time_util.now_kst", return_value=datetime(2026, 3, 27, 22, 0, tzinfo=ZoneInfo("Asia/Seoul"))), \
                patch("agent.trading_agent._analysis_mixin.market_calendar.get_market_session", return_value="US_PRE"), \
                patch("trading.account_manager.account_manager.get_balance", AsyncMock(return_value=balance)), \
                patch.object(self.agent, "_get_today_trade_stats", AsyncMock(return_value={"wins": 1, "losses": 0, "total": 1})):
            context = await self.agent._build_trading_context("NASDAQ")

        self.assertIn("모드: 프리마켓 스캘프 | holding_policy=PREMARKET_SCALP | 정규장 carry 금지", context)
        self.assertIn("세션 규칙: 신규 매수 마감 09:15 EDT | 강제 청산 09:25 EDT", context)
        self.assertIn("이번 세션은 강제 청산 시각 전 정리 전제의 단타만 허용", context)
        self.assertIn("session=US_PRE", context)
        self.assertIn("minutes_until_buy_cutoff=15", context)
        self.assertIn("minutes_until_force_liquidation=25", context)
        self.assertNotIn("모드: 스윙", context)

    def test_build_market_session_context_marks_krx_observation_window(self):
        with patch.object(settings, "KRX_REGULAR_OPENING_OBSERVATION_ENABLED", True), \
                patch.object(settings, "KRX_REGULAR_OPENING_OBSERVATION_WINDOW_MINUTES", 10), \
                patch.object(settings, "KRX_REGULAR_OPENING_GUARD_ENABLED", True), \
                patch.object(settings, "KRX_REGULAR_OPENING_GUARD_WINDOW_MINUTES", 30), \
                patch("util.time_util.now_kst", return_value=datetime(2026, 4, 1, 9, 5, tzinfo=ZoneInfo("Asia/Seoul"))), \
                patch("agent.trading_agent._analysis_mixin.market_calendar.get_market_session", return_value="KRX_NXT"):
            context = self.agent._build_market_session_context("KRX")

        self.assertEqual(context["opening_policy"], "OBSERVE_ONLY")
        self.assertTrue(context["opening_observation_active"])
        self.assertFalse(context["opening_guard_active"])
        self.assertEqual(context["minutes_from_regular_open"], 5)

    def test_build_market_session_context_marks_us_soft_guard_window(self):
        with patch.object(settings, "US_REGULAR_OPENING_OBSERVATION_ENABLED", True), \
                patch.object(settings, "US_REGULAR_OPENING_OBSERVATION_WINDOW_MINUTES", 15), \
                patch.object(settings, "US_REGULAR_OPENING_GUARD_ENABLED", True), \
                patch.object(settings, "US_REGULAR_OPENING_GUARD_WINDOW_MINUTES", 30), \
                patch("util.time_util.now_kst", return_value=datetime(2026, 3, 27, 22, 55, tzinfo=ZoneInfo("Asia/Seoul"))), \
                patch("agent.trading_agent._analysis_mixin.market_calendar.get_market_session", return_value="US_REGULAR"):
            context = self.agent._build_market_session_context("NASDAQ")

        self.assertEqual(context["opening_policy"], "SOFT_GUARD")
        self.assertFalse(context["opening_observation_active"])
        self.assertTrue(context["opening_guard_active"])
        self.assertEqual(context["minutes_from_regular_open"], 25)

    async def test_build_trading_context_uses_regular_opening_guard_mode_for_us_open(self):
        balance = self._balance()
        with patch.object(settings, "US_REGULAR_OPENING_OBSERVATION_ENABLED", True), \
                patch.object(settings, "US_REGULAR_OPENING_OBSERVATION_WINDOW_MINUTES", 15), \
                patch.object(settings, "US_REGULAR_OPENING_GUARD_ENABLED", True), \
                patch.object(settings, "US_REGULAR_OPENING_GUARD_WINDOW_MINUTES", 30), \
                patch("util.time_util.now_kst", return_value=datetime(2026, 3, 27, 22, 45, tzinfo=ZoneInfo("Asia/Seoul"))), \
                patch("agent.trading_agent._analysis_mixin.market_calendar.get_market_session", return_value="US_REGULAR"), \
                patch("trading.account_manager.account_manager.get_balance", AsyncMock(return_value=balance)), \
                patch.object(self.agent, "_get_today_trade_stats", AsyncMock(return_value={"wins": 1, "losses": 0, "total": 1})):
            context = await self.agent._build_trading_context("NASDAQ")

        self.assertIn("모드: 미국 정규장 오프닝 가드 | opening_policy=SOFT_GUARD", context)
        self.assertIn("추격 매수보다 확인 우선", context)
        self.assertIn("session=US_REGULAR", context)
        self.assertIn("opening_guard_active=true", context)
        self.assertIn("minutes_from_regular_open=15", context)
        self.assertNotIn("모드: 스윙", context)

    def test_apply_regular_opening_observation_tier1_gate_blocks_all_buy_intents(self):
        analysis = {
            "recommendation": "BUY",
            "position_intent": "ADD_ON_PYRAMID",
            "confidence": 0.72,
            "target_price": 71_500,
            "stop_loss_price": 68_000,
            "reason": "재돌파",
        }

        gated, gate_detail = self.agent._apply_regular_opening_observation_tier1_gate(
            analysis,
            market_code="KRX",
            session_context={
                "session": "KRX_NXT",
                "opening_policy": "OBSERVE_ONLY",
                "opening_observation_active": True,
                "minutes_from_regular_open": 6,
            },
        )

        self.assertEqual(gated["recommendation"], "HOLD")
        self.assertEqual(gated["position_intent"], "HOLD")
        self.assertTrue(gated["reason"].startswith("[OPENING_OBSERVATION_WINDOW]"))
        self.assertTrue(gate_detail["tier1_gate_applied"])
        self.assertEqual(gate_detail["tier1_gate_reason_code"], "OPENING_OBSERVATION_WINDOW")

    def test_apply_us_premarket_scalp_tier1_gate_blocks_late_new_buy(self):
        analysis = {
            "recommendation": "BUY",
            "position_intent": "NEW",
            "confidence": 0.68,
            "target_price": 6.47,
            "stop_loss_price": 4.74,
            "reason": "매수",
        }
        chart_result = ChartAnalysisResult(
            trend=TrendReport(
                momentum="ACCELERATING",
                intraday={"direction": "BULLISH", "vwap_position": "ABOVE_VWAP"},
            )
        )

        gated, gate_detail = self.agent._apply_us_premarket_scalp_tier1_gate(
            analysis,
            market_code="NASDAQ",
            current_price=5.47,
            current_position=None,
            product_context={"restricted_product": False},
            chart_result=chart_result,
            session_context={
                "session": "US_PRE",
                "holding_policy": "PREMARKET_SCALP",
                "minutes_until_buy_cutoff": 9,
                "minutes_until_force_liquidation": 19,
                "is_us_premarket_scalp": True,
            },
        )

        self.assertEqual(gated["recommendation"], "HOLD")
        self.assertEqual(gated["position_intent"], "HOLD")
        self.assertTrue(gated["reason"].startswith("[TIMEBOX_TOO_SHORT]"))
        self.assertTrue(gate_detail["tier1_gate_applied"])
        self.assertEqual(gate_detail["tier1_gate_reason_code"], "TIMEBOX_TOO_SHORT")

    def test_apply_krx_regular_opening_tier1_gate_blocks_low_price_hot_mover(self):
        analysis = {
            "recommendation": "BUY",
            "position_intent": "NEW",
            "confidence": 0.63,
            "target_price": 4_300,
            "stop_loss_price": 3_850,
            "reason": "거래량 동반 상승",
        }
        chart_result = ChartAnalysisResult(
            trend=TrendReport(
                momentum="ACCELERATING",
                intraday={"direction": "BULLISH", "vwap_position": "ABOVE_VWAP"},
            )
        )

        with patch.object(settings, "KRX_REGULAR_OPENING_GUARD_ENABLED", True):
            gated, gate_detail = self.agent._apply_krx_regular_opening_tier1_gate(
                analysis,
                market_code="KRX",
                current_price=3_950,
                change_rate=14.2,
                current_position=None,
                chart_result=chart_result,
                session_context={
                    "session": "KRX_NXT",
                    "opening_policy": "SOFT_GUARD",
                    "opening_guard_active": True,
                    "minutes_from_regular_open": 18,
                },
            )

        self.assertEqual(gated["recommendation"], "HOLD")
        self.assertEqual(gated["position_intent"], "HOLD")
        self.assertTrue(gated["reason"].startswith("[OPENING_LOW_PRICE_HOT_MOVER]"))
        self.assertTrue(gate_detail["tier1_gate_applied"])
        self.assertEqual(gate_detail["tier1_gate_reason_code"], "OPENING_LOW_PRICE_HOT_MOVER")

    def test_apply_us_premarket_scalp_tier1_gate_blocks_loose_restricted_product_buy(self):
        analysis = {
            "recommendation": "BUY",
            "position_intent": "NEW",
            "confidence": 0.64,
            "target_price": 11.03,
            "stop_loss_price": 8.89,
            "reason": "매수",
        }
        chart_result = ChartAnalysisResult(
            trend=TrendReport(
                momentum="ACCELERATING",
                intraday={"direction": "BULLISH", "vwap_position": "ABOVE_VWAP"},
            )
        )

        gated, gate_detail = self.agent._apply_us_premarket_scalp_tier1_gate(
            analysis,
            market_code="AMEX",
            current_price=9.98,
            current_position=None,
            product_context={"restricted_product": True},
            chart_result=chart_result,
            session_context={
                "session": "US_PRE",
                "holding_policy": "PREMARKET_SCALP",
                "minutes_until_buy_cutoff": 11,
                "minutes_until_force_liquidation": 21,
                "is_us_premarket_scalp": True,
            },
        )

        self.assertEqual(gated["recommendation"], "HOLD")
        self.assertTrue(gated["reason"].startswith("[RESTRICTED_PRODUCT_NOT_TIGHT]"))
        self.assertEqual(gate_detail["tier1_gate_reason_code"], "RESTRICTED_PRODUCT_NOT_TIGHT")

    def test_apply_us_regular_opening_tier1_gate_blocks_low_price_hot_mover(self):
        analysis = {
            "recommendation": "BUY",
            "position_intent": "NEW",
            "confidence": 0.61,
            "target_price": 2.5,
            "stop_loss_price": 2.05,
            "reason": "강한 상승",
        }
        chart_result = ChartAnalysisResult(
            trend=TrendReport(
                momentum="ACCELERATING",
                intraday={"direction": "BULLISH", "vwap_position": "ABOVE_VWAP"},
            )
        )

        with patch.object(settings, "US_REGULAR_OPENING_GUARD_ENABLED", True):
            gated, gate_detail = self.agent._apply_us_regular_opening_tier1_gate(
                analysis,
                market_code="NASDAQ",
                current_price=2.22,
                change_rate=62.03,
                current_position=None,
                chart_result=chart_result,
                session_context={
                    "session": "US_REGULAR",
                    "is_us_regular_opening": True,
                    "opening_guard_active": True,
                    "minutes_from_regular_open": 12,
                },
            )

        self.assertEqual(gated["recommendation"], "HOLD")
        self.assertEqual(gated["position_intent"], "HOLD")
        self.assertTrue(gated["reason"].startswith("[OPENING_LOW_PRICE_HOT_MOVER]"))
        self.assertTrue(gate_detail["tier1_gate_applied"])
        self.assertEqual(gate_detail["tier1_gate_reason_code"], "OPENING_LOW_PRICE_HOT_MOVER")
        self.assertEqual(gate_detail["opening_gate_reason_code"], "OPENING_LOW_PRICE_HOT_MOVER")

    async def test_analyze_and_trade_skips_tier2_when_premarket_scalp_tier1_gate_downgrades_buy(self):
        portfolio_snapshot = {
            "cash": 100_000_000,
            "total_asset": 300_000_000,
            "holding_count": 0,
            "holding_symbols": set(),
            "holding_positions": {},
            "today_trade_count": 0,
        }
        chart_result = ChartAnalysisResult(
            trend=TrendReport(
                momentum="ACCELERATING",
                intraday={"direction": "BULLISH", "vwap_position": "ABOVE_VWAP"},
            ),
            signal_summary={"direction": "BULLISH"},
        )
        price_response = MCPResponse(
            success=True,
            data={
                "market": "NASDAQ",
                "currency": "USD",
                "price": 5.47,
                "price_krw": 8_094.006,
                "exchange_rate_to_krw": 1479.8,
                "change": 1.0,
                "change_rate": 22.37,
                "volume": 1_000_000,
            },
        )
        daily_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"date": "20260310", "open": 4.1, "high": 4.2, "low": 4.0, "close": 4.15, "volume": 1000},
                    {"date": "20260311", "open": 4.15, "high": 4.3, "low": 4.1, "close": 4.22, "volume": 1100},
                    {"date": "20260312", "open": 4.22, "high": 4.5, "low": 4.2, "close": 4.4, "volume": 1200},
                    {"date": "20260313", "open": 4.4, "high": 4.9, "low": 4.35, "close": 4.82, "volume": 1300},
                    {"date": "20260314", "open": 4.82, "high": 5.6, "low": 4.8, "close": 5.47, "volume": 1400},
                ]
            },
        )
        minute_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"time": "0900", "open": 5.1, "high": 5.2, "low": 5.0, "close": 5.15, "volume": 100},
                    {"time": "0905", "open": 5.15, "high": 5.5, "low": 5.1, "close": 5.47, "volume": 160},
                ]
            },
        )
        orderable_response = MCPResponse(
            success=True,
            data={
                "orderable_amount_source": "INQUIRE_PSAMOUNT",
                "orderable_amount_krw": 150_620,
                "orderable_amount_foreign": 100.0,
                "orderable_qty": 18,
            },
        )

        class DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        activity_log = AsyncMock()
        detector = MagicMock()

        with patch.object(settings, "US_PREMARKET_SCALP_ENABLED", True), \
                patch("util.time_util.now_kst", return_value=datetime(2026, 3, 27, 22, 6, tzinfo=ZoneInfo("Asia/Seoul"))), \
                patch("agent.trading_agent._analysis_mixin.market_calendar.get_market_session", return_value="US_PRE"), \
                patch("agent.trading_agent.activity_logger.log", activity_log), \
                patch("agent.trading_agent.AsyncSessionLocal", return_value=DummySession()), \
                patch(
                    "analysis.feedback.performance_tracker.PerformanceTracker.get_consecutive_losses",
                    AsyncMock(return_value=0),
                ), \
                patch("agent.trading_agent.FeedbackContextBuilder.build_full_context", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.trading_agent.chart_analyzer.analyze", return_value=chart_result), \
                patch("agent.trading_agent.mcp_client.get_current_price", AsyncMock(return_value=price_response)), \
                patch("agent.trading_agent.mcp_client.get_daily_price", AsyncMock(return_value=daily_response)), \
                patch("agent.trading_agent.mcp_client.get_minute_price", AsyncMock(return_value=minute_response)), \
                patch("agent.trading_agent.mcp_client.get_orderable_amount", AsyncMock(return_value=orderable_response)), \
                patch.object(self.agent, "_tier1_analysis", AsyncMock(return_value={
                    "recommendation": "BUY",
                    "position_intent": "NEW",
                    "confidence": 0.68,
                    "target_price": 6.47,
                    "stop_loss_price": 4.74,
                    "reason": "강한 추세",
                })) as tier1_mock, \
                patch.object(self.agent, "_tier2_review", AsyncMock()) as tier2_mock, \
                patch("agent.trading_agent._analysis_mixin.event_detector", detector), \
                patch("agent.trading_agent.risk_manager.check", AsyncMock()) as risk_mock, \
                patch("agent.trading_agent.decision_maker.execute", AsyncMock()) as decision_mock:
            result = await self.agent._analyze_and_trade(
                {
                    "symbol": "ONCO",
                    "name": "온코네틱스",
                    "market": "NASDAQ",
                    "strategy_type": "STABLE_SHORT",
                },
                cycle_id="cycle-tier1-gate",
                portfolio_snapshot=portfolio_snapshot,
            )

        self.assertFalse(result["signal"])
        self.assertFalse(result["executed"])
        tier1_mock.assert_awaited_once()
        tier2_mock.assert_not_awaited()
        risk_mock.assert_not_awaited()
        decision_mock.assert_not_awaited()
        matching_calls = [
            call for call in activity_log.await_args_list
            if call.args[:2] == ("TIER1_ANALYSIS", "COMPLETE")
        ]
        self.assertTrue(matching_calls)
        hold_detail = matching_calls[-1].kwargs["detail"]
        self.assertEqual(hold_detail["tier1_gate_reason_code"], "TIMEBOX_TOO_SHORT")
        self.assertTrue(hold_detail["tier1_gate_applied"])

    async def test_analyze_and_trade_skips_tier2_when_regular_opening_gate_downgrades_buy(self):
        portfolio_snapshot = {
            "cash": 100_000_000,
            "total_asset": 300_000_000,
            "holding_count": 0,
            "holding_symbols": set(),
            "holding_positions": {},
            "today_trade_count": 0,
        }
        chart_result = ChartAnalysisResult(
            trend=TrendReport(
                momentum="ACCELERATING",
                intraday={"direction": "BULLISH", "vwap_position": "ABOVE_VWAP"},
            ),
            signal_summary={"direction": "BULLISH"},
        )
        price_response = MCPResponse(
            success=True,
            data={
                "market": "NASDAQ",
                "currency": "USD",
                "price": 2.22,
                "price_krw": 3285.156,
                "exchange_rate_to_krw": 1479.8,
                "change": 0.85,
                "change_rate": 62.03,
                "volume": 13_340_000,
            },
        )
        daily_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"date": "20260310", "open": 1.1, "high": 1.2, "low": 1.05, "close": 1.15, "volume": 1000},
                    {"date": "20260311", "open": 1.15, "high": 1.3, "low": 1.1, "close": 1.22, "volume": 1100},
                    {"date": "20260312", "open": 1.22, "high": 1.5, "low": 1.2, "close": 1.4, "volume": 1200},
                    {"date": "20260313", "open": 1.4, "high": 2.0, "low": 1.35, "close": 1.82, "volume": 1300},
                    {"date": "20260314", "open": 1.82, "high": 2.4, "low": 1.8, "close": 2.22, "volume": 1400},
                ]
            },
        )
        minute_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"time": "0940", "open": 2.05, "high": 2.12, "low": 2.0, "close": 2.1, "volume": 100},
                    {"time": "0945", "open": 2.1, "high": 2.25, "low": 2.08, "close": 2.22, "volume": 160},
                ]
            },
        )
        orderable_response = MCPResponse(
            success=True,
            data={
                "orderable_amount_source": "INQUIRE_PSAMOUNT",
                "orderable_amount_krw": 150_620,
                "orderable_amount_foreign": 100.0,
                "orderable_qty": 45,
            },
        )

        class DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        activity_log = AsyncMock()
        detector = MagicMock()

        with patch.object(settings, "US_REGULAR_OPENING_GUARD_ENABLED", True), \
                patch("util.time_util.now_kst", return_value=datetime(2026, 3, 27, 22, 45, tzinfo=ZoneInfo("Asia/Seoul"))), \
                patch("agent.trading_agent._analysis_mixin.market_calendar.get_market_session", return_value="US_REGULAR"), \
                patch("agent.trading_agent.activity_logger.log", activity_log), \
                patch("agent.trading_agent.AsyncSessionLocal", return_value=DummySession()), \
                patch(
                    "analysis.feedback.performance_tracker.PerformanceTracker.get_consecutive_losses",
                    AsyncMock(return_value=0),
                ), \
                patch("agent.trading_agent.FeedbackContextBuilder.build_full_context", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.trading_agent.chart_analyzer.analyze", return_value=chart_result), \
                patch("agent.trading_agent.mcp_client.get_current_price", AsyncMock(return_value=price_response)), \
                patch("agent.trading_agent.mcp_client.get_daily_price", AsyncMock(return_value=daily_response)), \
                patch("agent.trading_agent.mcp_client.get_minute_price", AsyncMock(return_value=minute_response)), \
                patch("agent.trading_agent.mcp_client.get_orderable_amount", AsyncMock(return_value=orderable_response)), \
                patch.object(self.agent, "_tier1_analysis", AsyncMock(return_value={
                    "recommendation": "BUY",
                    "position_intent": "NEW",
                    "confidence": 0.61,
                    "target_price": 2.5,
                    "stop_loss_price": 2.05,
                    "reason": "강한 추세",
                })) as tier1_mock, \
                patch.object(self.agent, "_tier2_review", AsyncMock()) as tier2_mock, \
                patch("agent.trading_agent._analysis_mixin.event_detector", detector), \
                patch("agent.trading_agent.risk_manager.check", AsyncMock()) as risk_mock, \
                patch("agent.trading_agent.decision_maker.execute", AsyncMock()) as decision_mock:
            result = await self.agent._analyze_and_trade(
                {
                    "symbol": "VSA",
                    "name": "VSA",
                    "market": "NASDAQ",
                    "strategy_type": "STABLE_SHORT",
                },
                cycle_id="cycle-opening-gate",
                portfolio_snapshot=portfolio_snapshot,
            )

        self.assertFalse(result["signal"])
        self.assertFalse(result["executed"])
        tier1_mock.assert_not_awaited()
        tier2_mock.assert_not_awaited()
        risk_mock.assert_not_awaited()
        decision_mock.assert_not_awaited()
        matching_calls = [
            call for call in activity_log.await_args_list
            if call.args[:2] == ("TIER1_ANALYSIS", "COMPLETE")
        ]
        self.assertTrue(matching_calls)
        hold_detail = matching_calls[-1].kwargs["detail"]
        self.assertEqual(hold_detail["tier1_gate_reason_code"], "OPENING_LOW_PRICE_HOT_MOVER")
        self.assertEqual(hold_detail["opening_gate_reason_code"], "OPENING_LOW_PRICE_HOT_MOVER")
        self.assertTrue(hold_detail["tier1_gate_applied"])

    async def test_crypto_tier1_analysis_falls_back_to_change_price_when_change_is_direction_string(self):
        chart_result = ChartAnalysisResult(
            indicators_text="지표",
            patterns_text="패턴",
            trend_text="추세",
        )

        with patch(
            "agent.trading_agent.llm_factory.generate_tier1",
            AsyncMock(return_value=('{"recommendation":"HOLD","confidence":0.4}', "CODEX_CLI")),
        ) as tier1_mock:
            result = await self.agent._tier1_analysis(
                symbol="BTC",
                name="비트코인",
                current_price=145_000_000,
                chart_result=chart_result,
                price_data={
                    "market": "BITHUMB",
                    "currency": "KRW",
                    "price": 145_000_000,
                    "change": "FALL",
                    "change_price": 1_250_000,
                    "change_rate": -0.85,
                    "volume": 12_345.67,
                },
                feedback_context="매매 이력 없음",
                current_position_context="현재 포지션 없음 (신규 진입 후보)",
                portfolio_snapshot={
                    "cash": 50_000,
                    "total_asset": 50_000,
                    "holding_count": 0,
                },
                current_position=None,
                dynamic_limits={"max_single_order_krw": 30_000, "max_position_pct": 90.0, "min_cash_ratio": 0.05},
                market_context="시장 컨텍스트 없음",
                trading_context="현재 세션: CRYPTO_ACTIVE",
                orderable_amount_context=None,
                cycle_id="cycle-crypto-1",
            )

        self.assertIsNotNone(result)
        prompt = tier1_mock.await_args.args[0]
        self.assertIn("-1,250,000.00원", prompt)

    async def test_on_market_event_analyzes_held_symbol(self):
        self.agent._running = True
        event = Event(
            type=EventType.VOLUME_SPIKE,
            data={
                "symbol": "PLTR",
                "market": "NASDAQ",
                "price": 149.18,
                "change_rate": -2.81,
                "currency": "USD",
            },
            source="test",
        )

        with patch.object(self.agent, "_build_trading_context", AsyncMock(return_value="ctx")), \
                patch.object(self.agent, "_get_today_trade_count", AsyncMock(return_value=0)), \
                patch("agent.trading_agent._event_mixin.settings.AI_RISK_TUNING_ENABLED", False), \
                patch("agent.trading_agent._event_mixin.market_calendar.get_market_session", return_value="US_REGULAR"), \
                patch("trading.account_manager.account_manager.get_account_snapshot", AsyncMock(return_value=(self._balance(), [self._holding()]))), \
                patch("agent.trading_agent.activity_logger.log", AsyncMock()) as log_mock, \
                patch.object(self.agent, "_ensure_realtime_subscription", AsyncMock()) as subscribe_mock, \
                patch.object(self.agent, "_analyze_and_trade", AsyncMock(return_value={"executed": False})) as analyze_mock:
            await self.agent._on_market_event(event)

        analyze_mock.assert_awaited_once()
        subscribe_mock.assert_awaited_once_with("PLTR", market="NASDAQ")
        stock_info = analyze_mock.await_args.args[0]
        self.assertEqual(stock_info["analysis_source"], "event")
        self.assertEqual(stock_info["event_type"], "VOLUME_SPIKE")
        self.assertFalse(
            any("기보유 종목 이벤트 감지" in call.args[2] for call in log_mock.await_args_list),
        )

    async def test_on_market_event_reconciles_watchlist_after_execution(self):
        self.agent._running = True
        event = Event(
            type=EventType.VOLUME_SPIKE,
            data={
                "symbol": "PLTR",
                "market": "NASDAQ",
                "price": 149.18,
                "change_rate": -2.81,
                "currency": "USD",
            },
            source="test",
        )

        with patch.object(self.agent, "_build_trading_context", AsyncMock(return_value="ctx")), \
                patch.object(self.agent, "_get_today_trade_count", AsyncMock(return_value=0)), \
                patch("agent.trading_agent._event_mixin.settings.AI_RISK_TUNING_ENABLED", False), \
                patch("agent.trading_agent._event_mixin.market_calendar.get_market_session", return_value="US_REGULAR"), \
                patch("trading.account_manager.account_manager.get_account_snapshot", AsyncMock(return_value=(self._balance(), [self._holding()]))), \
                patch("agent.trading_agent.activity_logger.log", AsyncMock()), \
                patch("services.watchlist_sync.reconcile_market_watchlist", AsyncMock(return_value=[("PLTR", "NASDAQ")])) as reconcile_mock, \
                patch.object(self.agent, "_ensure_realtime_subscription", AsyncMock()) as subscribe_mock, \
                patch.object(
                    self.agent,
                    "_analyze_and_trade",
                    AsyncMock(return_value={"executed": True, "order_amount": 25_000}),
                ):
            await self.agent._on_market_event(event)

        self.assertEqual(self.agent.get_runtime("NASDAQ").available_cash, self._balance().effective_cash - 25_000)
        reconcile_mock.assert_awaited_once_with("NASDAQ")
        subscribe_mock.assert_awaited_once_with("PLTR", market="NASDAQ")

    async def test_on_market_event_dedups_momentum_group_within_cooldown(self):
        self.agent._running = True
        surge_event = Event(
            type=EventType.PRICE_SURGE,
            data={
                "symbol": "PLTR",
                "market": "NASDAQ",
                "price": 149.18,
                "change_rate": 5.21,
                "currency": "USD",
            },
            source="test",
        )
        volume_event = Event(
            type=EventType.VOLUME_SPIKE,
            data={
                "symbol": "PLTR",
                "market": "NASDAQ",
                "price": 149.24,
                "change_rate": 5.34,
                "currency": "USD",
            },
            source="test",
        )

        with (
            patch.object(self.agent, "_build_trading_context", AsyncMock(return_value="ctx")),
            patch.object(self.agent, "_get_today_trade_count", AsyncMock(return_value=0)),
            patch("agent.trading_agent._event_mixin.settings.AI_RISK_TUNING_ENABLED", False),
            patch("agent.trading_agent._event_mixin.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch("trading.account_manager.account_manager.get_account_snapshot", AsyncMock(return_value=(self._balance(), [self._holding()]))),
            patch("agent.trading_agent.activity_logger.log", AsyncMock()) as log_mock,
            patch.object(self.agent, "_ensure_realtime_subscription", AsyncMock()),
            patch.object(self.agent, "_analyze_and_trade", AsyncMock(return_value={"executed": False})) as analyze_mock,
            patch("time.time", side_effect=[1_000.0, 1_001.0]),
        ):
            await self.agent._on_market_event(surge_event)
            await self.agent._on_market_event(volume_event)

        analyze_mock.assert_awaited_once()
        skip_logs = [
            call.kwargs.get("detail", {})
            for call in log_mock.await_args_list
            if call.args[1] == ActivityPhase.SKIP
        ]
        self.assertTrue(any(detail.get("event_skip_reason") == "momentum_group_dedup" for detail in skip_logs))

    async def test_on_market_event_reuses_cached_dynamic_limits(self):
        self.agent._running = True
        event = Event(
            type=EventType.PRICE_SURGE,
            data={
                "symbol": "PLTR",
                "market": "NASDAQ",
                "price": 149.18,
                "change_rate": 5.21,
                "currency": "USD",
            },
            source="test",
        )
        runtime = self.agent.get_runtime("US")
        runtime.trading_date = market_calendar.market_date(market="US")
        expected_limits = {
            "max_single_order_krw": 1_500_000,
            "max_position_pct": 12.5,
            "min_cash_ratio": 0.08,
        }
        runtime.cached_dynamic_limits = {
            **expected_limits,
        }
        runtime.cached_dynamic_limits_at = datetime.now().astimezone()

        with (
            patch.object(self.agent, "_build_trading_context", AsyncMock(return_value="ctx")),
            patch.object(self.agent, "_get_today_trade_count", AsyncMock(return_value=0)),
            patch("agent.trading_agent._event_mixin.settings.AI_RISK_TUNING_ENABLED", True),
            patch("agent.trading_agent._event_mixin.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch("trading.account_manager.account_manager.get_account_snapshot", AsyncMock(return_value=(self._balance(), [self._holding()]))),
            patch("agent.trading_agent.activity_logger.log", AsyncMock()) as log_mock,
            patch.object(self.agent, "_ensure_realtime_subscription", AsyncMock()),
            patch.object(self.agent, "_analyze_and_trade", AsyncMock(return_value={"executed": False})) as analyze_mock,
        ):
            await self.agent._on_market_event(event)

        self.assertEqual(
            analyze_mock.await_args.kwargs["dynamic_limits"],
            expected_limits,
        )
        self.assertTrue(
            any(
                call.kwargs.get("detail", {}).get("event_skip_reason") == "reuse_cached_dynamic_limits"
                for call in log_mock.await_args_list
            )
        )

    async def test_on_market_event_uses_defaults_when_dynamic_limits_cache_is_stale(self):
        self.agent._running = True
        event = Event(
            type=EventType.PRICE_SURGE,
            data={
                "symbol": "PLTR",
                "market": "NASDAQ",
                "price": 149.18,
                "change_rate": 5.21,
                "currency": "USD",
            },
            source="test",
        )
        runtime = self.agent.get_runtime("US")
        runtime.trading_date = market_calendar.market_date(market="US")
        runtime.cached_dynamic_limits = {
            "max_single_order_krw": 1_500_000,
            "max_position_pct": 12.5,
            "min_cash_ratio": 0.08,
        }
        runtime.cached_dynamic_limits_at = datetime(2020, 1, 1).astimezone()

        with (
            patch.object(self.agent, "_build_trading_context", AsyncMock(return_value="ctx")),
            patch.object(self.agent, "_get_today_trade_count", AsyncMock(return_value=0)),
            patch("agent.trading_agent._event_mixin.settings.AI_RISK_TUNING_ENABLED", True),
            patch("agent.trading_agent._event_mixin.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch("trading.account_manager.account_manager.get_account_snapshot", AsyncMock(return_value=(self._balance(), [self._holding()]))),
            patch("agent.trading_agent.activity_logger.log", AsyncMock()),
            patch.object(self.agent, "_ensure_realtime_subscription", AsyncMock()),
            patch.object(self.agent, "_analyze_and_trade", AsyncMock(return_value={"executed": False})) as analyze_mock,
        ):
            await self.agent._on_market_event(event)

        self.assertIsNone(analyze_mock.await_args.kwargs["dynamic_limits"])

    async def test_on_stop_loss_skips_sell_when_symbol_is_not_held(self):
        self.agent._running = True
        event = Event(
            type=EventType.STOP_LOSS_HIT,
            data={
                "symbol": "319400",
                "market": "KRX",
                "price": 28_700,
                "stop_loss_price": 28_825,
            },
            source="test",
        )

        with patch.object(type(settings), "is_trading_enabled_for_market", return_value=True), \
                patch("agent.trading_agent.activity_logger.log", AsyncMock()) as log_mock, \
                patch("trading.account_manager.account_manager.get_holdings", AsyncMock(return_value=[])), \
                patch("agent.trading_agent._event_mixin.mcp_client.place_order", AsyncMock()) as place_order_mock:
            await self.agent._on_stop_loss(event)

        place_order_mock.assert_not_awaited()
        summaries = [call.args[2] for call in log_mock.await_args_list]
        self.assertTrue(any("손절 조건 감지" in summary for summary in summaries))
        self.assertTrue(any("보유 수량 없음으로 매도 생략" in summary for summary in summaries))
        self.assertFalse(any("즉시 매도 실행" in summary for summary in summaries))

    async def test_analyze_and_trade_does_not_arm_trade_thresholds_before_risk_approval(self):
        portfolio_snapshot = {
            "cash": 10_000_000,
            "total_asset": 30_000_000,
            "holding_count": 0,
            "holding_symbols": set(),
            "holding_positions": {},
            "today_trade_count": 0,
        }
        chart_result = ChartAnalysisResult(
            indicators={
                "rsi_14": 58.0,
                "macd_histogram": 0.9,
            },
            signal_summary={"direction": "BULLISH"},
        )
        price_response = MCPResponse(
            success=True,
            data={
                "market": "KRX",
                "currency": "KRW",
                "price": 29_400,
                "price_krw": 29_400,
                "exchange_rate_to_krw": 1.0,
                "change": 2_000,
                "change_rate": 7.3,
                "volume": 120_000,
            },
        )
        daily_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"date": "20260326", "open": 25_500, "high": 26_000, "low": 25_100, "close": 25_800, "volume": 1000},
                    {"date": "20260327", "open": 25_900, "high": 26_500, "low": 25_600, "close": 26_200, "volume": 1200},
                    {"date": "20260330", "open": 26_300, "high": 27_100, "low": 26_100, "close": 26_900, "volume": 1500},
                    {"date": "20260331", "open": 27_000, "high": 27_800, "low": 26_900, "close": 27_500, "volume": 1700},
                    {"date": "20260401", "open": 27_600, "high": 29_900, "low": 27_400, "close": 29_400, "volume": 2200},
                ]
            },
        )
        minute_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"time": "0915", "open": 28_100, "high": 28_600, "low": 28_000, "close": 28_450, "volume": 400},
                    {"time": "0920", "open": 28_450, "high": 29_500, "low": 28_400, "close": 29_400, "volume": 620},
                ]
            },
        )
        orderable_response = MCPResponse(
            success=True,
            data={
                "orderable_amount_source": "INQUIRE_PSAMOUNT",
                "orderable_amount_krw": 2_000_000,
                "orderable_qty": 68,
            },
        )

        class DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        detector = MagicMock()
        detector.get_thresholds.return_value = type(
            "Thresholds",
            (),
            {"stop_loss": 0.0, "take_profit": 0.0, "trailing_stop_pct": 0.0},
        )()

        with patch("agent.trading_agent.activity_logger.log", AsyncMock()), \
                patch("agent.trading_agent.AsyncSessionLocal", return_value=DummySession()), \
                patch(
                    "analysis.feedback.performance_tracker.PerformanceTracker.get_consecutive_losses",
                    AsyncMock(return_value=0),
                ), \
                patch("agent.trading_agent.FeedbackContextBuilder.build_full_context", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.trading_agent.chart_analyzer.analyze", return_value=chart_result), \
                patch("agent.trading_agent.mcp_client.get_current_price", AsyncMock(return_value=price_response)), \
                patch("agent.trading_agent.mcp_client.get_daily_price", AsyncMock(return_value=daily_response)), \
                patch("agent.trading_agent.mcp_client.get_minute_price", AsyncMock(return_value=minute_response)), \
                patch("agent.trading_agent.mcp_client.get_orderable_amount", AsyncMock(return_value=orderable_response)), \
                patch.object(self.agent, "_tier1_analysis", AsyncMock(return_value={
                    "recommendation": "BUY",
                    "position_intent": "NEW",
                    "confidence": 0.73,
                    "target_price": 31_000,
                    "stop_loss_price": 28_825,
                    "trailing_stop_pct": 2.8,
                    "reason": "오프닝 돌파",
                })), \
                patch.object(self.agent, "_tier2_review", AsyncMock(return_value={
                    "approved": True,
                    "action": "BUY",
                    "position_intent": "NEW",
                    "suggested_quantity": 6,
                    "entry_price": 29_200,
                    "target_price": 31_000,
                    "stop_loss_price": 28_825,
                    "take_profit_price": 30_035,
                    "planned_hold_days": 1,
                    "reason": "조건 충족",
                    "provider": "CODEX_CLI",
                })), \
                patch("agent.trading_agent._analysis_mixin.event_detector", detector), \
                patch("agent.trading_agent.risk_manager.check", AsyncMock(return_value={
                    "approved": False,
                    "reason": "리스크:보상 비율 부족",
                })) as risk_mock, \
                patch("agent.trading_agent.decision_maker.execute", AsyncMock()) as decision_mock:
            result = await self.agent._analyze_and_trade(
                {
                    "symbol": "319400",
                    "name": "테스트종목",
                    "market": "KRX",
                    "strategy_type": "STABLE_SHORT",
                },
                cycle_id="cycle-risk-reject",
                portfolio_snapshot=portfolio_snapshot,
            )

        self.assertTrue(result["signal"])
        self.assertFalse(result["executed"])
        risk_mock.assert_awaited_once()
        decision_mock.assert_not_awaited()
        detector.set_thresholds.assert_not_called()

    async def test_analyze_and_trade_runs_cycle_for_held_symbol_when_tier2_approves_add_on(self):
        holding_symbols, holding_positions = self.agent._build_holding_snapshot(
            [self._holding(current_price=159.63, pnl=5950.0, pnl_rate=3.87)],
            total_asset=300_000_000,
        )

        portfolio_snapshot = {
            "cash": 100_000_000,
            "total_asset": 300_000_000,
            "holding_count": 1,
            "holding_symbols": holding_symbols,
            "holding_positions": holding_positions,
            "today_trade_count": 0,
        }
        chart_result = ChartAnalysisResult(
            indicators={
                "rsi_14": 49.0,
                "macd_histogram": 1.2,
                "obv_trend": "rising",
                "sma_5": 149.0,
            },
            signal_summary={"direction": "BULLISH"},
        )
        price_response = MCPResponse(
            success=True,
            data={
                "market": "NASDAQ",
                "currency": "USD",
                "price": 150.5,
                "price_krw": 222_209.9,
                "exchange_rate_to_krw": 1479.8,
                "change": 1.0,
                "change_rate": 0.67,
                "volume": 120_000,
            },
        )
        daily_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"date": "20260310", "open": 145.0, "high": 147.0, "low": 144.0, "close": 146.0, "volume": 1000},
                    {"date": "20260311", "open": 146.0, "high": 148.0, "low": 145.0, "close": 147.0, "volume": 1100},
                    {"date": "20260312", "open": 147.0, "high": 149.0, "low": 146.0, "close": 148.0, "volume": 1200},
                    {"date": "20260313", "open": 148.0, "high": 151.0, "low": 147.0, "close": 150.0, "volume": 1300},
                    {"date": "20260314", "open": 149.0, "high": 152.0, "low": 148.0, "close": 150.5, "volume": 1400},
                ]
            },
        )
        minute_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"time": "0930", "open": 149.0, "high": 149.5, "low": 148.8, "close": 149.3, "volume": 100},
                    {"time": "0935", "open": 149.3, "high": 150.2, "low": 149.1, "close": 150.1, "volume": 150},
                ]
            },
        )
        orderable_response = MCPResponse(
            success=True,
            data={
                "orderable_amount_source": "INQUIRE_PSAMOUNT",
                "orderable_amount_krw": 6_500_000,
                "orderable_amount_foreign": 4_392.0,
                "orderable_qty": 43,
            },
        )

        class DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        detector = MagicMock()
        detector.get_thresholds.return_value = type(
            "Thresholds",
            (),
            {
                "stop_loss": 144.0,
                "take_profit": 156.5,
                "trailing_stop_pct": 3.25,
            },
        )()
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(return_value=type(
            "TradeResult",
            (),
            {
                "currency": "USD",
                "entry_at": datetime(2026, 3, 13, 9, 31, 0),
                "created_at": datetime(2026, 3, 13, 9, 31, 0),
                "ai_stop_loss_price": 145.0,
                "ai_take_profit_price": 157.0,
                "ai_target_price": 160.0,
                "notes": (
                    '{"planned_hold_days": 4, "close_review_count": 1, '
                    '"last_close_review_date": "2026-03-13", "trailing_stop_pct": 3.25}'
                ),
            },
        )())
        expected_hold_days = max(0, (market_calendar.market_date(market="NASDAQ") - date(2026, 3, 13)).days)

        with patch("agent.trading_agent.activity_logger.log", AsyncMock()), \
                patch("agent.trading_agent.AsyncSessionLocal", return_value=DummySession()), \
                patch("repositories.trade_result_repository.TradeResultRepository", return_value=repo), \
                patch(
                    "analysis.feedback.performance_tracker.PerformanceTracker.get_consecutive_losses",
                    AsyncMock(return_value=0),
                ), \
                patch("agent.trading_agent.FeedbackContextBuilder.build_full_context", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.trading_agent.chart_analyzer.analyze", return_value=chart_result), \
                patch("agent.trading_agent.mcp_client.get_current_price", AsyncMock(return_value=price_response)) as price_mock, \
                patch("agent.trading_agent.mcp_client.get_daily_price", AsyncMock(return_value=daily_response)) as daily_mock, \
                patch("agent.trading_agent.mcp_client.get_minute_price", AsyncMock(return_value=minute_response)) as minute_mock, \
                patch("agent.trading_agent.mcp_client.get_orderable_amount", AsyncMock(return_value=orderable_response)) as orderable_mock, \
                patch.object(self.agent, "_tier1_analysis", AsyncMock(return_value={
                    "recommendation": "BUY",
                    "confidence": 0.72,
                    "target_price": 160.0,
                    "stop_loss_price": 145.0,
                    "trailing_stop_pct": 4.0,
                })) as tier1_mock, \
                patch.object(self.agent, "_tier2_review", AsyncMock(return_value={
                    "approved": True,
                    "action": "BUY",
                    "position_intent": "ADD_ON_PYRAMID",
                    "suggested_quantity": 5,
                    "entry_price": 151.0,
                    "target_price": 160.0,
                    "stop_loss_price": 145.0,
                    "take_profit_price": 157.0,
                    "planned_hold_days": 4,
                    "reason": "수익 구간 불타기",
                    "provider": "CODEX_CLI",
                })) as tier2_mock, \
                patch("agent.trading_agent._analysis_mixin.event_detector", detector), \
                patch("agent.trading_agent.risk_manager.check", AsyncMock(return_value={
                    "approved": True,
                    "adjusted_quantity": None,
                    "combined_position_pct": 12.0,
                    "cash_basis_krw": 6_500_000,
                })) as risk_mock, \
                patch("agent.trading_agent.decision_maker.execute", AsyncMock(return_value={"success": False})) as decision_mock:
            result = await self.agent._analyze_and_trade(
                {
                    "symbol": "PLTR",
                    "name": "팔란티어 테크",
                    "market": "NASDAQ",
                    "strategy_type": "STABLE_SHORT",
                    "analysis_source": "event",
                    "event_type": "PRICE_SURGE",
                },
                cycle_id="cycle-2",
                portfolio_snapshot=portfolio_snapshot,
            )

        self.assertTrue(result["signal"])
        self.assertFalse(result["executed"])
        self.assertEqual(price_mock.await_count, 2)
        daily_mock.assert_awaited_once()
        self.assertEqual(minute_mock.await_count, 2)
        orderable_mock.assert_awaited_once_with("PLTR", 150.5, market="NASDAQ")
        tier1_mock.assert_awaited_once()
        tier2_mock.assert_awaited_once()
        self.assertEqual(risk_mock.await_args.kwargs["orderable_cash_krw"], 6_500_000)
        decision_mock.assert_awaited_once()
        hold_plan_context = tier2_mock.await_args.kwargs["hold_plan_context"]
        self.assertIn("- 보유 계획 추적 상태: 활성", hold_plan_context)
        self.assertIn("- planned_hold_days: 4일", hold_plan_context)
        self.assertIn("- close_review_count: 1회", hold_plan_context)
        self.assertIn("- last_close_review_date: 2026-03-13", hold_plan_context)
        self.assertIn(f"- 실제 보유일(달력 기준): {expected_hold_days}일", hold_plan_context)
        self.assertIn("- 현재 stop_loss_price: 145.00USD", hold_plan_context)
        self.assertIn("- 현재 take_profit_price: 157.00USD", hold_plan_context)
        self.assertIn("- 현재 trailing_stop_pct: 3.25%", hold_plan_context)
        self.assertIn("장중 stop_loss / take_profit / trailing stop은 별도로 살아 있으며 우선 실행됩니다.", hold_plan_context)
        signal = decision_mock.await_args.args[0]
        analysis_context = decision_mock.await_args.kwargs["analysis_context"]
        self.assertEqual(signal.metadata["entry_mode"], "ADD_ON_PYRAMID")
        self.assertEqual(signal.metadata["analysis_source"], "event")
        self.assertEqual(signal.metadata["event_type"], "PRICE_SURGE")
        self.assertEqual(signal.metadata["broker_cash_krw"], 100_000_000)
        self.assertEqual(signal.metadata["symbol_orderable_amount_krw"], 6_500_000)
        self.assertEqual(signal.metadata["symbol_orderable_qty"], 43)
        self.assertEqual(signal.metadata["orderable_amount_source"], "INQUIRE_PSAMOUNT")
        self.assertEqual(signal.stop_loss_price, 144.0)
        self.assertEqual(signal.take_profit_price, 156.5)
        self.assertEqual(signal.metadata["trailing_stop_pct"], 3.25)
        self.assertEqual(signal.metadata["planned_hold_days"], 4)
        self.assertEqual(signal.metadata["existing_hold_plan_tracking_status"], "tracked")
        self.assertEqual(signal.metadata["existing_planned_hold_days"], 4)
        self.assertEqual(signal.metadata["existing_close_review_count"], 1)
        self.assertEqual(signal.metadata["existing_last_close_review_date"], "2026-03-13")
        self.assertEqual(signal.metadata["existing_calendar_hold_days"], expected_hold_days)
        self.assertEqual(signal.metadata["existing_hold_plan_stop_loss_price"], 145.0)
        self.assertEqual(signal.metadata["existing_hold_plan_take_profit_price"], 157.0)
        self.assertEqual(signal.metadata["existing_hold_plan_trailing_stop_pct"], 3.25)
        self.assertEqual(analysis_context["entry_mode"], "ADD_ON_PYRAMID")
        self.assertEqual(analysis_context["analysis_source"], "event")
        self.assertEqual(analysis_context["event_type"], "PRICE_SURGE")
        self.assertEqual(analysis_context["broker_cash_krw"], 100_000_000)
        self.assertEqual(analysis_context["symbol_orderable_amount_krw"], 6_500_000)
        self.assertEqual(analysis_context["symbol_orderable_qty"], 43)
        self.assertEqual(analysis_context["ai_target_price"], 160.0)
        self.assertEqual(analysis_context["ai_stop_loss_price"], 144.0)
        self.assertEqual(analysis_context["ai_take_profit_price"], 156.5)
        self.assertEqual(analysis_context["trailing_stop_pct"], 3.25)
        self.assertEqual(analysis_context["planned_hold_days"], 4)
        self.assertEqual(analysis_context["existing_hold_plan_tracking_status"], "tracked")
        self.assertEqual(analysis_context["existing_planned_hold_days"], 4)
        self.assertEqual(analysis_context["existing_close_review_count"], 1)
        self.assertEqual(analysis_context["existing_last_close_review_date"], "2026-03-13")
        self.assertEqual(analysis_context["existing_calendar_hold_days"], expected_hold_days)
        self.assertEqual(analysis_context["existing_hold_plan_stop_loss_price"], 145.0)
        self.assertEqual(analysis_context["existing_hold_plan_take_profit_price"], 157.0)
        self.assertEqual(analysis_context["existing_hold_plan_trailing_stop_pct"], 3.25)
        self.assertEqual(analysis_context["trade_threshold_payload"]["stop_loss"], 145.0)
        self.assertEqual(analysis_context["trade_threshold_payload"]["take_profit"], 157.0)
        self.assertEqual(analysis_context["trade_threshold_payload"]["trailing_stop_pct"], 4.0)

    async def test_evaluate_position_intent_blocks_second_average_down_same_day(self):
        chart_result = ChartAnalysisResult(
            indicators={
                "rsi_14": 41.0,
                "macd_histogram": 0.8,
                "obv_trend": "rising",
                "sma_5": 149.0,
            },
            signal_summary={"direction": "BULLISH"},
        )

        with patch.object(self.agent, "_count_today_trade_result_entry_mode", AsyncMock(return_value=1)):
            result = await self.agent._evaluate_position_intent(
                symbol="PLTR",
                market_code="NASDAQ",
                scope="US",
                trading_date=date(2026, 3, 14),
                current_position={
                    "symbol": "PLTR",
                    "market": "NASDAQ",
                    "quantity": 100,
                    "pnl_rate": -4.2,
                    "current_value_krw": 22_000_000,
                },
                entry_mode="ADD_ON_AVERAGE_DOWN",
                chart_result=chart_result,
                current_price=150.0,
            )

        self.assertFalse(result["approved"])
        self.assertIn("당일 허용 횟수", result["reason"])


if __name__ == "__main__":
    unittest.main()
