import unittest

from trading.market_profile import (
    build_ws_key,
    market_scope,
    markets_for_scope,
    normalize_market,
    normalize_market_scope,
)


class MarketProfileTest(unittest.TestCase):
    def test_normalize_market_aliases(self):
        self.assertEqual(normalize_market("us"), "NASDAQ")
        self.assertEqual(normalize_market("nys"), "NYSE")
        self.assertEqual(normalize_market("ams"), "AMEX")

    def test_build_ws_key_for_us_markets(self):
        self.assertEqual(build_ws_key("aapl", "NASDAQ"), "DNASAAPL")
        self.assertEqual(build_ws_key("ibm", "NYSE"), "DNYSIBM")
        self.assertEqual(build_ws_key("spy", "AMEX"), "DAMSSPY")

    def test_market_scope_groups_us_markets(self):
        self.assertEqual(market_scope("NASDAQ"), "US")
        self.assertEqual(market_scope("NYSE"), "US")
        self.assertEqual(market_scope("KRX"), "KRX")
        self.assertEqual(normalize_market_scope("us"), "US")

    def test_markets_for_scope_expands_runtime_scope(self):
        self.assertEqual(markets_for_scope("US"), ("NASDAQ", "NYSE", "AMEX"))
        self.assertEqual(markets_for_scope("KRX"), ("KRX", "KOSPI", "KOSDAQ"))


if __name__ == "__main__":
    unittest.main()
