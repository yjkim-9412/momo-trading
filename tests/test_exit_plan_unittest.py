"""ExitPlan 관련 단위 테스트 — ExitPlanManager + EventDetector 다단계 TP"""
import json
import unittest

from realtime.event_detector import EventDetector, StockThresholds
from strategy.exit_plan_manager import ExitPlanManager


class ExitPlanManagerTest(unittest.TestCase):
    """ExitPlanManager 유틸리티 메서드 테스트"""

    def test_calculate_sell_quantity_50pct(self):
        self.assertEqual(ExitPlanManager.calculate_sell_quantity(10, 50, "KRX"), 5)

    def test_calculate_sell_quantity_100pct(self):
        self.assertEqual(ExitPlanManager.calculate_sell_quantity(10, 100, "KRX"), 10)

    def test_calculate_sell_quantity_min_1(self):
        # 1주일 때 50% → 최소 1주
        self.assertEqual(ExitPlanManager.calculate_sell_quantity(1, 50, "KRX"), 1)

    def test_calculate_sell_quantity_floor(self):
        # 3주의 30% = 0.9 → floor → 0 → max(1, 0) = 1
        self.assertEqual(ExitPlanManager.calculate_sell_quantity(3, 30, "KRX"), 1)

    def test_calculate_sell_quantity_large(self):
        self.assertEqual(ExitPlanManager.calculate_sell_quantity(100, 70, "KRX"), 70)

    def test_calculate_sell_quantity_crypto_preserves_fractional_precision(self):
        self.assertAlmostEqual(
            ExitPlanManager.calculate_sell_quantity(0.12345678, 50, "BITHUMB"),
            0.06172839,
            places=8,
        )

    def test_calculate_sell_quantity_crypto_uses_min_step(self):
        self.assertAlmostEqual(
            ExitPlanManager.calculate_sell_quantity(0.00000003, 10, "BITHUMB"),
            0.00000001,
            places=8,
        )

    def test_parse_levels(self):
        levels_json = json.dumps([
            {"type": "TAKE_PROFIT", "price": 155.0, "pct": 50},
            {"type": "STOP_LOSS", "price": 140.0, "pct": 100},
        ])
        levels = ExitPlanManager.parse_levels(levels_json)
        self.assertEqual(len(levels), 2)
        self.assertEqual(levels[0]["type"], "TAKE_PROFIT")

    def test_get_active_tp_levels(self):
        levels = [
            {"type": "TAKE_PROFIT", "price": 165.0, "pct": 100, "triggered": False},
            {"type": "TAKE_PROFIT", "price": 155.0, "pct": 50, "triggered": True},
            {"type": "STOP_LOSS", "price": 140.0, "pct": 100, "triggered": False},
        ]
        active = ExitPlanManager.get_active_tp_levels(levels)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["price"], 165.0)

    def test_get_stop_loss_price(self):
        levels = [
            {"type": "TAKE_PROFIT", "price": 155.0, "pct": 50},
            {"type": "STOP_LOSS", "price": 140.0, "pct": 100},
        ]
        self.assertEqual(ExitPlanManager.get_stop_loss_price(levels), 140.0)

    def test_get_stop_loss_price_none(self):
        levels = [{"type": "TAKE_PROFIT", "price": 155.0, "pct": 50}]
        self.assertEqual(ExitPlanManager.get_stop_loss_price(levels), 0.0)


class EventDetectorMultiLevelTPTest(unittest.IsolatedAsyncioTestCase):
    """EventDetector 다단계 TP 테스트"""

    def setUp(self):
        self.detector = EventDetector()

    async def test_set_thresholds_with_tp_levels(self):
        tp_levels = [
            {"price": 155.0, "pct": 50, "level_index": 0},
            {"price": 165.0, "pct": 100, "level_index": 1},
        ]
        self.detector.set_thresholds(
            "AAPL", market="NASDAQ",
            stop_loss=140.0,
            exit_plan_id="plan-123",
            tp_levels=tp_levels,
        )

        th = self.detector.get_thresholds("AAPL", market="NASDAQ")
        self.assertEqual(th.stop_loss, 140.0)
        self.assertEqual(th.exit_plan_id, "plan-123")
        self.assertEqual(len(th.tp_levels), 2)
        # take_profit은 첫 번째 TP 레벨로 설정 (하위 호환)
        self.assertEqual(th.take_profit, 155.0)

    async def test_advance_tp_level(self):
        tp_levels = [
            {"price": 155.0, "pct": 50, "level_index": 0},
            {"price": 165.0, "pct": 100, "level_index": 1},
        ]
        self.detector.set_thresholds(
            "AAPL", market="NASDAQ",
            exit_plan_id="plan-123",
            tp_levels=tp_levels,
        )

        self.detector.advance_tp_level("AAPL", market="NASDAQ")
        th = self.detector.get_thresholds("AAPL", market="NASDAQ")
        self.assertEqual(len(th.tp_levels), 1)
        self.assertEqual(th.take_profit, 165.0)  # 다음 레벨

    async def test_advance_tp_level_last(self):
        tp_levels = [{"price": 155.0, "pct": 100, "level_index": 0}]
        self.detector.set_thresholds(
            "AAPL", market="NASDAQ",
            exit_plan_id="plan-123",
            tp_levels=tp_levels,
        )

        self.detector.advance_tp_level("AAPL", market="NASDAQ")
        th = self.detector.get_thresholds("AAPL", market="NASDAQ")
        self.assertEqual(len(th.tp_levels), 0)
        self.assertEqual(th.take_profit, 0.0)

    async def test_clear_trade_thresholds_clears_exit_plan(self):
        self.detector.set_thresholds(
            "AAPL", market="NASDAQ",
            stop_loss=140.0,
            exit_plan_id="plan-123",
            tp_levels=[{"price": 155.0, "pct": 100, "level_index": 0}],
        )
        self.detector.clear_trade_thresholds("AAPL", market="NASDAQ")
        th = self.detector.get_thresholds("AAPL", market="NASDAQ")
        self.assertIsNone(th.exit_plan_id)
        self.assertEqual(th.tp_levels, [])

    async def test_tp_levels_sorted_by_price(self):
        # 역순으로 넣어도 정렬되는지
        tp_levels = [
            {"price": 165.0, "pct": 100, "level_index": 1},
            {"price": 155.0, "pct": 50, "level_index": 0},
        ]
        self.detector.set_thresholds(
            "AAPL", market="NASDAQ",
            tp_levels=tp_levels,
        )
        th = self.detector.get_thresholds("AAPL", market="NASDAQ")
        self.assertEqual(th.tp_levels[0]["price"], 155.0)
        self.assertEqual(th.tp_levels[1]["price"], 165.0)


class StockThresholdsDefaultTest(unittest.TestCase):
    """StockThresholds 기본값 테스트"""

    def test_defaults(self):
        th = StockThresholds()
        self.assertIsNone(th.exit_plan_id)
        self.assertEqual(th.tp_levels, [])
        self.assertEqual(th.stop_loss, 0.0)
        self.assertEqual(th.take_profit, 0.0)


if __name__ == "__main__":
    unittest.main()
