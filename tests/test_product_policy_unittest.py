import unittest

from core.config import settings
from trading.product_policy import (
    classify_product,
    coerce_strategy_for_product,
    is_product_trade_allowed,
)


class ProductPolicyTest(unittest.TestCase):
    def setUp(self):
        self._original = {
            "US_LEVERAGED_PRODUCTS_ENABLED": settings.US_LEVERAGED_PRODUCTS_ENABLED,
            "US_INVERSE_PRODUCTS_ENABLED": settings.US_INVERSE_PRODUCTS_ENABLED,
            "US_LEVERAGE_ALLOWED_SESSIONS": settings.US_LEVERAGE_ALLOWED_SESSIONS,
            "US_LEVERAGE_ALLOWED_STRATEGIES": settings.US_LEVERAGE_ALLOWED_STRATEGIES,
            "US_LEVERAGE_ALLOWLIST": settings.US_LEVERAGE_ALLOWLIST,
            "US_LEVERAGE_DENYLIST": settings.US_LEVERAGE_DENYLIST,
            "US_LEVERAGE_KEYWORDS": settings.US_LEVERAGE_KEYWORDS,
            "US_INVERSE_KEYWORDS": settings.US_INVERSE_KEYWORDS,
        }
        settings.US_LEVERAGED_PRODUCTS_ENABLED = True
        settings.US_INVERSE_PRODUCTS_ENABLED = True
        settings.US_LEVERAGE_ALLOWED_SESSIONS = "US_REGULAR"
        settings.US_LEVERAGE_ALLOWED_STRATEGIES = "STABLE_SHORT"
        settings.US_LEVERAGE_ALLOWLIST = ""
        settings.US_LEVERAGE_DENYLIST = ""
        settings.US_LEVERAGE_KEYWORDS = "2X,3X,ULTRA,ULTRAPRO,LEVERAGED"
        settings.US_INVERSE_KEYWORDS = "INVERSE,SHORT,BEAR"

    def tearDown(self):
        for field_name, value in self._original.items():
            setattr(settings, field_name, value)

    def test_classify_leveraged_and_inverse_by_name(self):
        leveraged = classify_product(
            symbol="TQQQ",
            market="NASDAQ",
            name="ProShares UltraPro QQQ",
        )
        inverse = classify_product(
            symbol="SQQQ",
            market="NASDAQ",
            name="ProShares UltraPro Short QQQ",
        )

        self.assertEqual(leveraged.product_type, "LEVERAGED_ETF")
        self.assertTrue(leveraged.is_leveraged)
        self.assertEqual(leveraged.leverage_multiplier, 3.0)
        self.assertEqual(leveraged.signed_exposure, 3.0)
        self.assertEqual(inverse.product_type, "INVERSE_ETF")
        self.assertTrue(inverse.is_inverse)
        self.assertEqual(inverse.leverage_multiplier, 3.0)
        self.assertEqual(inverse.signed_exposure, -3.0)

    def test_inverse_without_explicit_multiplier_defaults_to_one_x(self):
        inverse = classify_product(
            symbol="SH",
            market="NYSE",
            name="ProShares Short S&P500",
        )

        self.assertEqual(inverse.product_type, "INVERSE_ETF")
        self.assertEqual(inverse.leverage_multiplier, 1.0)
        self.assertEqual(inverse.signed_exposure, -1.0)

    def test_coerce_restricted_strategy_to_stable(self):
        classification = classify_product(
            symbol="TQQQ",
            market="NASDAQ",
            name="ProShares UltraPro QQQ",
        )

        self.assertEqual(
            coerce_strategy_for_product("AGGRESSIVE_SHORT", classification),
            "STABLE_SHORT",
        )

    def test_restricted_product_blocks_premarket(self):
        classification = classify_product(
            symbol="TQQQ",
            market="NASDAQ",
            name="ProShares UltraPro QQQ",
        )

        allowed, reason = is_product_trade_allowed(
            classification,
            strategy_type="STABLE_SHORT",
            session="US_PRE",
        )

        self.assertFalse(allowed)
        self.assertIn("US_REGULAR", reason)


if __name__ == "__main__":
    unittest.main()
