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


if __name__ == "__main__":
    unittest.main()
