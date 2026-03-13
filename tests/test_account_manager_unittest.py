import unittest

from core.config import settings
from trading.account_manager import AccountManager
from trading.models import HoldingInfo


class AccountManagerBalanceTest(unittest.TestCase):
    def setUp(self):
        self._original_account_type = settings.KIS_ACCOUNT_TYPE
        settings.KIS_ACCOUNT_TYPE = "VIRTUAL"
        self.manager = AccountManager()

    def tearDown(self):
        settings.KIS_ACCOUNT_TYPE = self._original_account_type

    def test_parse_balance_uses_total_asset_proxy_for_us_paper_cash(self):
        holdings = [
            HoldingInfo(
                symbol="NVDA",
                name="NVIDIA",
                market="NASDAQ",
                currency="USD",
                quantity=2,
                avg_buy_price=182.0,
                current_price=183.0,
                pnl=2.0,
                pnl_rate=0.5,
                exchange_rate_to_krw=1450.0,
            )
        ]
        data = {
            "msg1": "모의투자 조회할 내역(자료)이 없습니다.",
            "output2": [{
                "frcr_dncl_amt_2": "0",
                "tot_asst_amt": "369166161",
                "frst_bltn_exrt": "1450.0",
            }],
        }

        balance = self.manager._parse_balance(data, holdings=holdings, market="NASDAQ")

        expected_stock_value = 183.0 * 1450.0 * 2
        self.assertEqual(balance.raw_cash, 0.0)
        self.assertAlmostEqual(balance.stock_value, expected_stock_value)
        self.assertAlmostEqual(balance.effective_cash, balance.total_asset - expected_stock_value)
        self.assertEqual(balance.cash_source, "TOTAL_ASSET_PROXY")

    def test_parse_balance_recalculates_us_paper_pnl_from_holdings(self):
        holdings = [
            HoldingInfo(
                symbol="NVDA",
                name="NVIDIA",
                market="NASDAQ",
                currency="USD",
                quantity=2,
                avg_buy_price=182.785,
                current_price=183.0,
                pnl=-0.17,
                pnl_rate=-0.05,
                exchange_rate_to_krw=1467.3,
            )
        ]
        data = {
            "output2": [{
                "frcr_dncl_amt_2": "0",
                "tot_asst_amt": "369169425",
                "tot_evlu_pfls_amt": "7497.903",
                "evlu_pfls_rt": "0",
                "frst_bltn_exrt": "1467.3",
            }],
        }

        balance = self.manager._parse_balance(data, holdings=holdings, market="NASDAQ")

        expected_total_pnl = -0.17 * 1467.3
        expected_purchase = 182.785 * 2 * 1467.3
        expected_total_pnl_rate = (expected_total_pnl / expected_purchase) * 100

        self.assertAlmostEqual(balance.total_pnl, expected_total_pnl)
        self.assertAlmostEqual(balance.total_pnl_rate, expected_total_pnl_rate)
        self.assertEqual(balance.raw_total_pnl, 7497.903)
        self.assertEqual(balance.raw_total_pnl_rate, 0.0)
        self.assertEqual(balance.pnl_source, "HOLDINGS_SUM")

    def test_parse_balance_zeroes_us_paper_pnl_without_holdings(self):
        data = {
            "output2": [{
                "frcr_dncl_amt_2": "0",
                "tot_asst_amt": "369169425",
                "tot_evlu_pfls_amt": "7497.903",
                "evlu_pfls_rt": "1.23",
                "frst_bltn_exrt": "1467.3",
            }],
        }

        balance = self.manager._parse_balance(data, holdings=[], market="NASDAQ")

        self.assertEqual(balance.total_pnl, 0.0)
        self.assertEqual(balance.total_pnl_rate, 0.0)
        self.assertEqual(balance.raw_total_pnl, 7497.903)
        self.assertEqual(balance.raw_total_pnl_rate, 1.23)
        self.assertEqual(balance.pnl_source, "HOLDINGS_SUM")

    def test_parse_balance_keeps_domestic_cash_unchanged(self):
        data = {
            "output2": [{
                "dnca_tot_amt": "1500000",
                "tot_evlu_amt": "2500000",
                "scts_evlu_amt": "1000000",
            }],
        }

        balance = self.manager._parse_balance(data, holdings=[], market="KRX")

        self.assertEqual(balance.cash, 1500000.0)
        self.assertEqual(balance.raw_cash, 1500000.0)
        self.assertEqual(balance.effective_cash, 1500000.0)
        self.assertEqual(balance.cash_source, "BROKER")
        self.assertEqual(balance.raw_total_pnl, 0.0)
        self.assertEqual(balance.raw_total_pnl_rate, 0.0)
        self.assertEqual(balance.pnl_source, "BROKER_SUMMARY")


if __name__ == "__main__":
    unittest.main()
