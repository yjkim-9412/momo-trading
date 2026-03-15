import unittest

import pandas as pd

from analysis.chart_analyzer import chart_analyzer
from analysis.technical.indicators import TechnicalIndicators


def _build_daily_df(reverse: bool = False) -> pd.DataFrame:
    closes = list(range(100, 160))
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(closes), freq="D"),
        "open": [close - 1 for close in closes],
        "high": [close + 2 for close in closes],
        "low": [close - 2 for close in closes],
        "close": closes,
        "volume": [1000 + idx * 10 for idx in range(len(closes))],
    })
    if reverse:
        return df.iloc[::-1].reset_index(drop=True)
    return df


class DailyPriceOrderTest(unittest.TestCase):
    def test_technical_indicators_require_chronological_daily_prices(self):
        chronological_df = _build_daily_df()
        reversed_df = _build_daily_df(reverse=True)

        chronological = TechnicalIndicators.calculate_all(chronological_df)
        reversed_order = TechnicalIndicators.calculate_all(reversed_df)

        self.assertEqual(chronological["current_price"], 159)
        self.assertEqual(reversed_order["current_price"], 100)
        self.assertNotEqual(chronological["sma_20"], reversed_order["sma_20"])
        self.assertNotEqual(chronological["ema_20"], reversed_order["ema_20"])
        self.assertNotIn("vwap", chronological)
        self.assertNotIn("VWAP", TechnicalIndicators.format_for_prompt(chronological))

    def test_chart_analyzer_trend_flips_when_daily_prices_are_reversed(self):
        chronological_df = _build_daily_df()
        reversed_df = _build_daily_df(reverse=True)

        chronological = chart_analyzer.analyze(chronological_df)
        reversed_order = chart_analyzer.analyze(reversed_df)

        self.assertGreater(chronological.trend.slope_20d, 0)
        self.assertLess(reversed_order.trend.slope_20d, 0)
        self.assertEqual(chronological.trend.ma_arrangement, "BULLISH")
        self.assertEqual(reversed_order.trend.ma_arrangement, "BEARISH")


if __name__ == "__main__":
    unittest.main()
