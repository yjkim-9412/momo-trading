import unittest
from unittest.mock import AsyncMock, patch

import pandas as pd

from agent.trading_agent import TradingAgent
from analysis.chart_analyzer import ChartAnalysisResult
from analysis.llm.prompts.daily_plan import DAILY_PLAN_PROMPT
from analysis.llm.prompts.crypto_cycle_review import (
    CRYPTO_CYCLE_REVIEW_PROMPT,
    CRYPTO_CYCLE_REVIEW_SYSTEM,
)
from analysis.llm.prompts.final_review import (
    CRYPTO_REVIEW_PROMPT,
    CRYPTO_REVIEW_SYSTEM,
    FINAL_REVIEW_PROMPT,
    FINAL_REVIEW_SYSTEM,
)
from analysis.llm.prompts.market_scan import (
    CRYPTO_MARKET_SCAN_PROMPT,
    CRYPTO_MARKET_SCAN_SYSTEM,
    MARKET_SCAN_PROMPT,
    US_MARKET_SCAN_PROMPT,
    US_MARKET_SCAN_SYSTEM,
)
from analysis.llm.prompts.stock_analysis import (
    CRYPTO_ANALYSIS_PROMPT,
    CRYPTO_ANALYSIS_SYSTEM,
    STOCK_ANALYSIS_PROMPT,
    STOCK_ANALYSIS_SYSTEM,
)
from core.config import settings
from trading.risk_policy import (
    BULL_THEME_RR_FLOOR,
    CRYPTO_DEFENSIVE_RR_FLOOR,
    CRYPTO_MOMENTUM_RR_FLOOR,
    DEFENSIVE_RR_FLOOR,
)


class TradingAgentDataQualityTest(unittest.TestCase):
    def test_sort_market_data_frame_orders_oldest_first(self):
        df = pd.DataFrame({
            "date": ["20260313", "20260311", "20260312"],
            "close": [104.0, 100.0, 102.0],
        })

        ordered = TradingAgent._sort_market_data_frame(df, "date")

        self.assertEqual(ordered["date"].tolist(), ["20260311", "20260312", "20260313"])
        self.assertEqual(ordered["close"].tolist(), [100.0, 102.0, 104.0])

    def test_detect_price_consistency_issue_allows_small_gap(self):
        daily_df = pd.DataFrame({"close": [98.0, 100.0, 102.0]})
        minute_df = pd.DataFrame({"close": [101.0, 101.5]})

        issue = TradingAgent._detect_price_consistency_issue(100.0, daily_df, minute_df)

        self.assertIsNone(issue)

    def test_detect_price_consistency_issue_blocks_large_gap_using_worst_anchor(self):
        daily_df = pd.DataFrame({"close": [145.0, 148.0, 150.0]})
        minute_df = pd.DataFrame({"close": [92.0, 90.0]})

        issue = TradingAgent._detect_price_consistency_issue(100.0, daily_df, minute_df)

        self.assertIsNotNone(issue)
        self.assertEqual(issue["anchor"], "latest_daily_close")
        self.assertEqual(issue["anchor_price"], 150.0)
        self.assertEqual(issue["latest_daily_close"], 150.0)
        self.assertEqual(issue["latest_minute_close"], 90.0)
        self.assertGreaterEqual(issue["gap_pct"], 0.25)

    def test_normalize_tier2_price_fields_converts_krw_prices_back_to_market_currency(self):
        parsed = {
            "entry_price": 42300,
            "target_price": 44461,
            "stop_loss_price": 40764,
        }

        normalized = TradingAgent._normalize_tier2_price_fields(
            parsed,
            current_price=196.6,
            currency="USD",
            exchange_rate_to_krw=215.16,
        )

        self.assertAlmostEqual(normalized["entry_price"], 196.5979, places=3)
        self.assertAlmostEqual(normalized["target_price"], 206.6416, places=3)
        self.assertAlmostEqual(normalized["stop_loss_price"], 189.459, places=3)
        self.assertIn("normalized_price_fields", normalized)

    def test_stock_analysis_prompt_uses_trend_summary_label(self):
        self.assertIn("### 추세 분석 요약", STOCK_ANALYSIS_PROMPT)
        self.assertNotIn("### 최근 일봉 데이터", STOCK_ANALYSIS_PROMPT)
        self.assertIn("### 상품 특성", STOCK_ANALYSIS_PROMPT)
        self.assertIn("{product_context}", STOCK_ANALYSIS_PROMPT)
        self.assertIn("### 트레이딩 상황", STOCK_ANALYSIS_PROMPT)
        self.assertIn(f"BULL/THEME 국면: {BULL_THEME_RR_FLOOR:.1f}:1 이상이면 적정", STOCK_ANALYSIS_SYSTEM)
        self.assertIn(f"SIDEWAYS/BEAR 국면: 최소 {DEFENSIVE_RR_FLOOR:.1f}:1", STOCK_ANALYSIS_SYSTEM)
        self.assertIn("recommendation은 BUY 또는 HOLD만 사용", STOCK_ANALYSIS_SYSTEM)
        self.assertIn("recommendation: BUY 또는 HOLD만 사용하세요", STOCK_ANALYSIS_PROMPT)
        self.assertIn("confidence: 이 매매가 손절 전에 목표가에 도달할 확률", STOCK_ANALYSIS_PROMPT)
        self.assertNotIn('"recommendation": "BUY/SELL/HOLD"', STOCK_ANALYSIS_PROMPT)

    def test_crypto_analysis_prompt_uses_coin_specific_short_term_rules(self):
        self.assertIn("수시간~2일", CRYPTO_ANALYSIS_SYSTEM)
        self.assertIn(f"BULL_RUN/ALTSEASON/THEME 국면: {CRYPTO_MOMENTUM_RR_FLOOR:.1f}:1 이상이면 적정", CRYPTO_ANALYSIS_SYSTEM)
        self.assertIn(f"BEAR_MARKET/CONSOLIDATION 국면: 최소 {CRYPTO_DEFENSIVE_RR_FLOOR:.1f}:1", CRYPTO_ANALYSIS_SYSTEM)
        self.assertIn("24/7 시장이므로 시간 압박은 없습니다", CRYPTO_ANALYSIS_SYSTEM)
        self.assertIn("ADD_ON_PYRAMID", CRYPTO_ANALYSIS_SYSTEM)
        self.assertIn("ADD_ON_AVERAGE_DOWN", CRYPTO_ANALYSIS_SYSTEM)
        self.assertIn("5,000 KRW", CRYPTO_ANALYSIS_SYSTEM)
        self.assertIn("수량이 아니라 KRW 투자금 기준", CRYPTO_ANALYSIS_PROMPT)
        self.assertIn("24h 거래대금: {trade_value_text}", CRYPTO_ANALYSIS_PROMPT)
        self.assertIn("거래대금이 약하거나 추격 매수 성격이 강하면", CRYPTO_ANALYSIS_PROMPT)
        self.assertNotIn('"recommendation": "BUY/SELL/HOLD"', CRYPTO_ANALYSIS_PROMPT)

    def test_final_review_prompt_requires_market_currency_for_price_fields(self):
        self.assertIn("### 원본 차트 요약", FINAL_REVIEW_PROMPT)
        self.assertIn("### 상품 특성", FINAL_REVIEW_PROMPT)
        self.assertIn("배수(1x/2x/3x)", FINAL_REVIEW_PROMPT)
        self.assertIn("환산 참고: 1{currency}", FINAL_REVIEW_PROMPT)
        self.assertIn("stop_loss_price: 손절 기준가 ({currency})", FINAL_REVIEW_PROMPT)
        self.assertIn("가격 필드에 넣지 마세요", FINAL_REVIEW_PROMPT)
        self.assertIn(f"RR비율: {BULL_THEME_RR_FLOOR:.1f}:1 이상이면 허용", FINAL_REVIEW_SYSTEM)
        self.assertIn(f"RR비율: 최소 {DEFENSIVE_RR_FLOOR:.1f}:1", FINAL_REVIEW_SYSTEM)
        self.assertIn(
            f"THEME/BULL: {BULL_THEME_RR_FLOOR:.1f}:1 이상, SIDEWAYS/BEAR: {DEFENSIVE_RR_FLOOR:.1f}:1 이상",
            FINAL_REVIEW_PROMPT,
        )
        self.assertIn("confidence: 이 매매가 손절 전에 목표가에 도달할 확률", FINAL_REVIEW_PROMPT)
        self.assertNotIn("기타: 1.5:1 이상", FINAL_REVIEW_PROMPT)
        self.assertNotIn("checklist_pass", FINAL_REVIEW_PROMPT)
        self.assertNotIn("partial_exit_plan", FINAL_REVIEW_PROMPT)

    def test_crypto_final_review_prompt_enforces_buy_hold_and_canonical_regimes(self):
        self.assertIn("BULL_RUN/ALTSEASON/THEME 국면", CRYPTO_REVIEW_SYSTEM)
        self.assertIn(f"RR비율: {CRYPTO_MOMENTUM_RR_FLOOR:.1f}:1 이상이면 허용", CRYPTO_REVIEW_SYSTEM)
        self.assertIn(f"RR비율: 최소 {CRYPTO_DEFENSIVE_RR_FLOOR:.1f}:1", CRYPTO_REVIEW_SYSTEM)
        self.assertIn("시장가 금액 매수", CRYPTO_REVIEW_SYSTEM)
        self.assertIn("suggested_amount_krw", CRYPTO_REVIEW_PROMPT)
        self.assertIn("24h 거래대금: {trade_value_text}", CRYPTO_REVIEW_PROMPT)
        self.assertIn("action: BUY 또는 HOLD만 사용하세요", CRYPTO_REVIEW_PROMPT)
        self.assertIn('"action": "BUY/HOLD"', CRYPTO_REVIEW_PROMPT)
        self.assertNotIn('"action": "BUY/SELL/HOLD"', CRYPTO_REVIEW_PROMPT)

    def test_market_scan_prompts_use_local_timezone_and_variable_selection_range(self):
        self.assertIn("현재 시각({timezone_label})", MARKET_SCAN_PROMPT)
        self.assertIn("이번 스캔 선정 목표: {selection_target_range}개", MARKET_SCAN_PROMPT)
        self.assertNotIn('"direction": "BUY/SELL"', MARKET_SCAN_PROMPT)
        self.assertIn("현재 시각({timezone_label})", US_MARKET_SCAN_PROMPT)
        self.assertNotIn("현재 시각(KST)", US_MARKET_SCAN_PROMPT)
        self.assertNotIn('"direction": "BUY/SELL"', US_MARKET_SCAN_PROMPT)
        self.assertIn("프리마켓(04:00~09:30 ET)", US_MARKET_SCAN_SYSTEM)
        self.assertIn("정규장(09:30~15:00 ET)", US_MARKET_SCAN_SYSTEM)
        self.assertIn("장후반(15:00 ET~매수 마감)", US_MARKET_SCAN_SYSTEM)

    def test_crypto_market_scan_prompt_uses_canonical_regimes_and_can_return_zero(self):
        self.assertIn("수시간~2일", CRYPTO_MARKET_SCAN_SYSTEM)
        self.assertIn("THEME(섹터 장세)", CRYPTO_MARKET_SCAN_SYSTEM)
        self.assertIn("canonical 국면명만 사용", CRYPTO_MARKET_SCAN_SYSTEM)
        self.assertIn("최소 주문금액은 5,000 KRW", CRYPTO_MARKET_SCAN_PROMPT)
        self.assertIn("수량이 아니라 KRW 투자금 기준", CRYPTO_MARKET_SCAN_SYSTEM)
        self.assertIn("이번 스캔 선정 목표: {selection_target_range}개", CRYPTO_MARKET_SCAN_PROMPT)
        self.assertIn("적합한 후보가 없으면 0개 허용", CRYPTO_MARKET_SCAN_PROMPT)
        self.assertIn('"market_regime": "BULL_RUN/BEAR_MARKET/CONSOLIDATION/ALTSEASON/THEME"', CRYPTO_MARKET_SCAN_PROMPT)

    def test_crypto_primary_market_falls_back_to_bithumb_for_invalid_value(self):
        original = settings.CRYPTO_PRIMARY_MARKET
        try:
            settings.CRYPTO_PRIMARY_MARKET = "KRX"
            self.assertEqual(settings.crypto_primary_market_code, "BITHUMB")
        finally:
            settings.CRYPTO_PRIMARY_MARKET = original

    def test_daily_plan_prompt_limits_action_items_to_top_changes(self):
        self.assertIn("action_items는 가장 중요한 3~5개만 제안하세요", DAILY_PLAN_PROMPT)

    def test_crypto_cycle_review_prompt_targets_next_cycle_feedback(self):
        self.assertIn("다음 코인 사이클", CRYPTO_CYCLE_REVIEW_SYSTEM)
        self.assertIn("직전 자동 운영 구간", CRYPTO_CYCLE_REVIEW_PROMPT)
        self.assertIn("다음 적용 사이클", CRYPTO_CYCLE_REVIEW_PROMPT)
        self.assertIn('"next_cycle_plan"', CRYPTO_CYCLE_REVIEW_PROMPT)
        self.assertIn("ALL / BULL_RUN / BEAR_MARKET / CONSOLIDATION / ALTSEASON / THEME", CRYPTO_CYCLE_REVIEW_SYSTEM)

    def test_should_skip_tier2_blocks_restricted_products(self):
        self.assertFalse(
            TradingAgent._should_skip_tier2(
                is_restricted_product=True,
                tier1_confidence=0.95,
                market_regime="BULL",
                recommendation="BUY",
            )
        )

    def test_apply_trade_thresholds_prefers_explicit_take_profit_price(self):
        captured = {}

        class _EventDetectorStub:
            @staticmethod
            def set_thresholds(symbol, market=None, **kwargs):
                captured["symbol"] = symbol
                captured["market"] = market
                captured["kwargs"] = kwargs

        import agent.trading_agent._analysis_mixin as _amixin
        original_detector = _amixin.event_detector
        _amixin.event_detector = _EventDetectorStub()
        try:
            TradingAgent()._apply_trade_thresholds(
                "AAPL",
                {"target_price": 205.0, "stop_loss_price": 190.0},
                {"take_profit_price": 210.0, "trailing_stop_pct": 2.0},
                market="NASDAQ",
            )
        finally:
            _amixin.event_detector = original_detector

        self.assertEqual(captured["symbol"], "AAPL")
        self.assertEqual(captured["market"], "NASDAQ")
        self.assertEqual(captured["kwargs"]["take_profit"], 210.0)
        self.assertEqual(captured["kwargs"]["stop_loss"], 190.0)
        self.assertEqual(captured["kwargs"]["trailing_stop_pct"], 2.0)
        self.assertTrue(
            TradingAgent._should_skip_tier2(
                is_restricted_product=False,
                tier1_confidence=0.95,
                market_regime="BULL",
                recommendation="BUY",
            )
        )


class TradingAgentCryptoFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_primary = settings.PRIMARY_MARKET
        self._original_crypto_primary = settings.CRYPTO_PRIMARY_MARKET
        settings.PRIMARY_MARKET = "KRX"
        settings.CRYPTO_PRIMARY_MARKET = "BITHUMB"

    async def asyncTearDown(self):
        settings.PRIMARY_MARKET = self._original_primary
        settings.CRYPTO_PRIMARY_MARKET = self._original_crypto_primary

    async def test_tier1_analysis_uses_explicit_crypto_market_not_shared_primary_market(self):
        agent = TradingAgent()
        chart_result = ChartAnalysisResult(
            indicators_text="지표 없음",
            patterns_text="패턴 없음",
            trend_text="추세 없음",
        )

        with patch.object(
            agent,
            "_build_account_context",
            return_value={"text": "계좌 컨텍스트"},
        ), patch(
            "agent.trading_agent._analysis_mixin.llm_factory.generate_tier1",
            AsyncMock(return_value=('{"recommendation":"HOLD","confidence":0.55}', "TEST")),
        ) as generate_tier1:
            result = await agent._tier1_analysis(
                "BTC",
                "비트코인",
                150000000,
                chart_result,
                {
                    "currency": "KRW",
                    "change_rate": 1.2,
                    "volume": 10,
                    "trade_value": 1000000,
                },
                market=settings.crypto_primary_market_code,
            )

        self.assertEqual(result["market"], "BITHUMB")
        self.assertEqual(
            generate_tier1.await_args.kwargs["system_prompt"],
            CRYPTO_ANALYSIS_SYSTEM,
        )


if __name__ == "__main__":
    unittest.main()
