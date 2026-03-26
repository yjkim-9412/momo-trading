import unittest
from unittest.mock import patch

from analysis.feedback.trading_rules import TradingRuleEngine
from strategy.aggressive_short import AggressiveShortStrategy
from strategy.risk_manager import RiskManager
from strategy.stable_short import StableShortStrategy
from trading.risk_policy import normalize_crypto_regime, resolve_rr_floor as resolve_shared_rr_floor


class _FakeScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _FakeExecuteResult:
    def __init__(self, items=None):
        self._items = items or []

    def scalars(self):
        return _FakeScalarResult(self._items)


class _FakeSession:
    def __init__(self):
        self.added = []

    async def execute(self, _statement):
        return _FakeExecuteResult([])

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        return None


class _FakeSessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TradingRuleEngineScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_generate_rules_from_review_uses_apply_scope_for_rr_floor(self):
        engine = TradingRuleEngine()
        session = _FakeSession()
        parsed_review = {
            "action_items": [
                {
                    "rule_type": "PARAM_OVERRIDE",
                    "apply_scope": "BULL",
                    "param_name": "rr_floor",
                    "param_value": 1.3,
                    "reason": "BULL 국면 RR 강화",
                }
            ]
        }

        with patch("analysis.feedback.trading_rules.AsyncSessionLocal", new=lambda: _FakeSessionContext(session)):
            rules = await engine.generate_rules_from_review(parsed_review, report_date="2026-03-13", market_scope="KRX")

        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].strategy_type, "BULL")
        self.assertEqual(session.added[0].strategy_type, "BULL")

    async def test_generate_rules_from_review_rejects_invalid_rr_floor_scope(self):
        engine = TradingRuleEngine()
        session = _FakeSession()
        parsed_review = {
            "action_items": [
                {
                    "rule_type": "PARAM_OVERRIDE",
                    "apply_scope": "STABLE_SHORT",
                    "param_name": "rr_floor",
                    "param_value": 1.4,
                    "reason": "잘못된 scope",
                }
            ]
        }

        with patch("analysis.feedback.trading_rules.AsyncSessionLocal", new=lambda: _FakeSessionContext(session)):
            rules = await engine.generate_rules_from_review(parsed_review, report_date="2026-03-13", market_scope="KRX")

        self.assertEqual(rules, [])
        self.assertEqual(session.added, [])

    async def test_generate_rules_from_review_rejects_stock_stop_loss_override(self):
        engine = TradingRuleEngine()
        session = _FakeSession()
        parsed_review = {
            "action_items": [
                {
                    "rule_type": "PARAM_OVERRIDE",
                    "apply_scope": "ALL",
                    "param_name": "stop_loss_pct",
                    "param_value": -3.0,
                    "reason": "주식 손절 퍼센트 강화",
                }
            ]
        }

        with patch("analysis.feedback.trading_rules.AsyncSessionLocal", new=lambda: _FakeSessionContext(session)):
            rules = await engine.generate_rules_from_review(parsed_review, report_date="2026-03-13", market_scope="KRX")

        self.assertEqual(rules, [])
        self.assertEqual(session.added, [])

    async def test_generate_rules_from_review_allows_crypto_stop_loss_override(self):
        engine = TradingRuleEngine()
        session = _FakeSession()
        parsed_review = {
            "action_items": [
                {
                    "rule_type": "PARAM_OVERRIDE",
                    "apply_scope": "ALL",
                    "param_name": "stop_loss_pct",
                    "param_value": -3.0,
                    "reason": "코인 손절 퍼센트 유지",
                }
            ]
        }

        with patch("analysis.feedback.trading_rules.AsyncSessionLocal", new=lambda: _FakeSessionContext(session)):
            rules = await engine.generate_rules_from_review(
                parsed_review,
                report_date="2026-03-13",
                market_scope="CRYPTO",
            )

        self.assertEqual(len(rules), 1)
        self.assertEqual(session.added[0].param_name, "stop_loss_pct")


class RiskManagerRRFloorTest(unittest.TestCase):
    def test_normalize_crypto_regime_maps_legacy_aliases(self):
        self.assertEqual(normalize_crypto_regime("BEAR"), "BEAR_MARKET")
        self.assertEqual(normalize_crypto_regime("sideways"), "CONSOLIDATION")
        self.assertEqual(normalize_crypto_regime("ALT_SEASON"), "ALTSEASON")
        self.assertEqual(normalize_crypto_regime("THEME"), "THEME")

    def test_resolve_rr_floor_prefers_regime_then_all_then_default(self):
        self.assertEqual(
            RiskManager.resolve_rr_floor("BULL", {"ALL": 1.1, "BULL": 1.4}),
            1.4,
        )
        self.assertEqual(
            RiskManager.resolve_rr_floor("SIDEWAYS", {"ALL": 1.25}),
            1.25,
        )
        self.assertEqual(
            RiskManager.resolve_rr_floor("SIDEWAYS", None),
            1.2,
        )

    def test_resolve_rr_floor_matches_shared_policy_defaults(self):
        self.assertEqual(
            RiskManager.resolve_rr_floor("BULL", None),
            resolve_shared_rr_floor("BULL", None),
        )
        self.assertEqual(
            RiskManager.resolve_rr_floor("SIDEWAYS", None),
            resolve_shared_rr_floor("SIDEWAYS", None),
        )

    def test_crypto_rr_floor_supports_canonical_regimes(self):
        self.assertEqual(RiskManager.CRYPTO_RR_FLOOR["BULL_RUN"], 2.0)
        self.assertEqual(RiskManager.CRYPTO_RR_FLOOR["ALTSEASON"], 2.0)
        self.assertEqual(RiskManager.CRYPTO_RR_FLOOR["THEME"], 2.0)
        self.assertEqual(RiskManager.CRYPTO_RR_FLOOR["BEAR_MARKET"], 1.5)
        self.assertEqual(RiskManager.CRYPTO_RR_FLOOR["CONSOLIDATION"], 1.5)


class CryptoStrategyRegimeMappingTest(unittest.IsolatedAsyncioTestCase):
    async def test_stable_strategy_uses_canonical_crypto_regime_params(self):
        strategy = StableShortStrategy()

        signal = await strategy.evaluate(
            {
                "recommendation": "BUY",
                "confidence": 0.8,
                "symbol": "BTC",
                "stock_id": "BTC",
                "current_price": 100.0,
                "market": "BITHUMB",
                "currency": "KRW",
            },
            market_regime="CONSOLIDATION",
        )

        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal.target_price, 103.0)
        self.assertAlmostEqual(signal.stop_loss_price, 98.0)

    async def test_aggressive_strategy_uses_canonical_crypto_regime_params(self):
        strategy = AggressiveShortStrategy()

        signal = await strategy.evaluate(
            {
                "recommendation": "BUY",
                "confidence": 0.8,
                "symbol": "DOGE",
                "stock_id": "DOGE",
                "current_price": 100.0,
                "market": "BITHUMB",
                "currency": "KRW",
            },
            market_regime="ALTSEASON",
        )

        self.assertIsNotNone(signal)
        self.assertAlmostEqual(signal.target_price, 108.0)
        self.assertAlmostEqual(signal.stop_loss_price, 96.5)


if __name__ == "__main__":
    unittest.main()
