import unittest
from unittest.mock import patch

from agent.trading_agent import TradingAgent


class SoftExplorationHelperTest(unittest.TestCase):
    def setUp(self):
        self.agent = TradingAgent()

    def test_build_soft_explore_overlay_relaxes_only_within_balanced_bounds(self):
        runtime = self.agent.get_runtime("KRX")
        strategy = runtime.strategies["STABLE_SHORT"]

        overlay = self.agent._build_soft_explore_overlay(
            market_scope_code="KRX",
            strategy=strategy,
            market_regime="SIDEWAYS",
            rule_min_conf=0.62,
            min_rr=1.3,
        )

        self.assertAlmostEqual(overlay["min_confidence"], 0.57)
        self.assertAlmostEqual(overlay["rr_floor"], 1.2)

    def test_build_soft_explore_candidate_skips_when_relaxed_confidence_still_not_met(self):
        candidate = self.agent._build_soft_explore_candidate(
            stock_info={"symbol": "011070", "market": "KRX"},
            analysis={"recommendation": "BUY", "confidence": 0.59},
            strategy_type="STABLE_SHORT",
            market_code="KRX",
            gate_type="CONFIDENCE",
            gate_reason="confidence_gate",
            overlay={"min_confidence": 0.63},
            tier1_confidence=0.59,
            rule_min_conf=0.68,
        )

        self.assertIsNone(candidate)

    def test_build_soft_explore_candidate_returns_near_miss_candidate_when_overlay_clears_gate(self):
        candidate = self.agent._build_soft_explore_candidate(
            stock_info={"symbol": "011070", "market": "KRX"},
            analysis={"recommendation": "BUY", "confidence": 0.59},
            strategy_type="STABLE_SHORT",
            market_code="KRX",
            gate_type="CONFIDENCE",
            gate_reason="confidence_gate",
            overlay={"min_confidence": 0.57},
            tier1_confidence=0.59,
            rule_min_conf=0.62,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["symbol"], "011070")
        self.assertAlmostEqual(candidate["overlay"]["min_confidence"], 0.57)
        self.assertAlmostEqual(candidate["rank_score"], 0.03)

    def test_should_run_stock_soft_exploration_requires_zero_day_buy_count(self):
        with patch(
            "agent.trading_agent.market_calendar.is_trading_hours",
            return_value=True,
        ), patch.object(
            TradingAgent,
            "_minutes_until_market_buy_cutoff",
            return_value=90,
        ):
            self.assertTrue(
                self.agent._should_run_stock_soft_exploration(
                    "KRX",
                    results={"signals": 0},
                    snapshot={"today_buy_result_count": 0},
                    candidate_count=1,
                )
            )
            self.assertFalse(
                self.agent._should_run_stock_soft_exploration(
                    "KRX",
                    results={"signals": 0},
                    snapshot={"today_buy_result_count": 1},
                    candidate_count=1,
                )
            )


if __name__ == "__main__":
    unittest.main()
