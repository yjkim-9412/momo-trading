import unittest

from trading.market_profile import build_ws_key, normalize_market


class MarketProfileTest(unittest.TestCase):
    def test_normalize_market_aliases(self):
        self.assertEqual(normalize_market("us"), "NASDAQ")
        self.assertEqual(normalize_market("nys"), "NYSE")
        self.assertEqual(normalize_market("ams"), "AMEX")

    def test_build_ws_key_for_us_markets(self):
        self.assertEqual(build_ws_key("aapl", "NASDAQ"), "DNASAAPL")
        self.assertEqual(build_ws_key("ibm", "NYSE"), "DNYSIBM")
        self.assertEqual(build_ws_key("spy", "AMEX"), "DAMSSPY")


if __name__ == "__main__":
    unittest.main()
