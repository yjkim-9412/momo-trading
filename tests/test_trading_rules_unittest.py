import unittest
from unittest.mock import patch

from analysis.feedback.trading_rules import TradingRuleEngine
from strategy.risk_manager import RiskManager


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


class RiskManagerRRFloorTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
