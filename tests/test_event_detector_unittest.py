import unittest

from realtime.event_detector import EventDetector


class EventDetectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_thresholds_are_isolated_by_market(self):
        detector = EventDetector()

        detector.set_thresholds("AAPL", market="NASDAQ", surge_pct=2.5)
        detector.set_thresholds("AAPL", market="NYSE", surge_pct=4.0)

        nasdaq = detector.get_thresholds("AAPL", market="NASDAQ")
        nyse = detector.get_thresholds("AAPL", market="NYSE")

        self.assertEqual(nasdaq.surge_pct, 2.5)
        self.assertEqual(nyse.surge_pct, 4.0)

    async def test_price_cache_isolated_by_market(self):
        detector = EventDetector()

        await detector.on_price_update({
            "symbol": "AAPL",
            "market": "NASDAQ",
            "price": 200.0,
            "volume": 1000,
            "change_rate": 1.2,
        })
        await detector.on_price_update({
            "symbol": "AAPL",
            "market": "NYSE",
            "price": 150.0,
            "volume": 500,
            "change_rate": -0.8,
        })

        self.assertEqual(detector._prev_prices["NASDAQ:AAPL"], 200.0)
        self.assertEqual(detector._prev_prices["NYSE:AAPL"], 150.0)

    async def test_clear_trade_thresholds_preserves_scan_monitors(self):
        detector = EventDetector()

        detector.set_thresholds(
            "CRCD",
            market="AMEX",
            surge_pct=4.0,
            drop_pct=-4.0,
            volume_spike_ratio=5.0,
            stop_loss=6.8,
            take_profit=7.8,
            trailing_stop_pct=2.0,
        )

        detector.clear_trade_thresholds("CRCD", market="AMEX")
        thresholds = detector.get_thresholds("CRCD", market="AMEX")

        self.assertEqual(thresholds.surge_pct, 4.0)
        self.assertEqual(thresholds.drop_pct, -4.0)
        self.assertEqual(thresholds.volume_spike_ratio, 5.0)
        self.assertEqual(thresholds.stop_loss, 0.0)
        self.assertEqual(thresholds.take_profit, 0.0)
        self.assertEqual(thresholds.trailing_stop_pct, 0.0)
        self.assertEqual(thresholds.highest_price, 0.0)


if __name__ == "__main__":
    unittest.main()
