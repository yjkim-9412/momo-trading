import unittest

from core.config import settings
from trading.product_policy import (
    build_product_context,
    classify_product,
    classification_from_metadata,
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
            "US_PRODUCT_TYPE_OVERRIDES": settings.US_PRODUCT_TYPE_OVERRIDES,
            "US_LEVERAGE_KEYWORDS": settings.US_LEVERAGE_KEYWORDS,
            "US_INVERSE_KEYWORDS": settings.US_INVERSE_KEYWORDS,
        }
        settings.US_LEVERAGED_PRODUCTS_ENABLED = True
        settings.US_INVERSE_PRODUCTS_ENABLED = True
        settings.US_LEVERAGE_ALLOWED_SESSIONS = "US_REGULAR"
        settings.US_LEVERAGE_ALLOWED_STRATEGIES = "STABLE_SHORT"
        settings.US_LEVERAGE_ALLOWLIST = ""
        settings.US_LEVERAGE_DENYLIST = ""
        settings.US_PRODUCT_TYPE_OVERRIDES = ""
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

    def test_classify_uses_etp_type_name_and_override_before_fallback_common(self):
        settings.US_PRODUCT_TYPE_OVERRIDES = "UVIX=LEVERAGED_ETF"

        from_detail = classification_from_metadata(
            symbol="CRCD",
            market="AMEX",
            metadata={
                "name": "CoreCard ETF",
                "product_type": "COMMON",
                "classification_source": "default",
                "etp_type_name": "ETF",
            },
        )
        from_override = classification_from_metadata(
            symbol="UVIX",
            market="NASDAQ",
            metadata={
                "name": "2x UVIX",
                "product_type": "COMMON",
                "classification_source": "default",
            },
        )

        self.assertEqual(from_detail.product_type, "ETF")
        self.assertEqual(from_detail.classification_source, "etp_type_or_text")
        self.assertEqual(from_detail.etp_type_name, "ETF")
        self.assertEqual(from_override.product_type, "LEVERAGED_ETF")
        self.assertTrue(from_override.is_leveraged)
        self.assertEqual(from_override.classification_source, "override")

    def test_build_product_context_marks_bear_inverse_as_aligned(self):
        context = build_product_context(
            symbol="SQQQ",
            market="NASDAQ",
            metadata={
                "name": "ProShares UltraPro Short QQQ",
                "product_type": "INVERSE_ETF",
                "is_inverse": True,
                "leverage_multiplier": 3.0,
                "classification_source": "metadata",
            },
            market_regime="BEAR",
        )

        self.assertEqual(context["market_bias"], "BEAR")
        self.assertEqual(context["market_alignment"], "ALIGNED")
        self.assertIn("약세장과 같은 방향", context["alignment_reason"])

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
