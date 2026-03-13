import unittest

import pandas as pd

from agent.trading_agent import TradingAgent
from analysis.llm.prompts.stock_analysis import STOCK_ANALYSIS_PROMPT


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

    def test_final_review_prompt_requires_market_currency_for_price_fields(self):
        from analysis.llm.prompts.final_review import FINAL_REVIEW_PROMPT

        self.assertIn("환산 참고: 1{currency}", FINAL_REVIEW_PROMPT)
        self.assertIn("stop_loss_price: 손절 기준가 ({currency})", FINAL_REVIEW_PROMPT)
        self.assertIn("가격 필드에 넣지 마세요", FINAL_REVIEW_PROMPT)


if __name__ == "__main__":
    unittest.main()
