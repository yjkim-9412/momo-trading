import unittest
from unittest.mock import AsyncMock, patch

from core.config import settings
from trading.kis_api import (
    DOMAIN,
    VIRTUAL_DOMAIN,
    _get_account_parts,
    _get_token_file,
    _get_trading_domain,
    cancel_overseas_order,
    get_overseas_daily_price,
    get_overseas_order_list,
    place_overseas_order,
)


class AccountPartsTest(unittest.TestCase):
    def setUp(self):
        self._original_account_type = settings.KIS_ACCOUNT_TYPE
        self._original_paper_stock = settings.KIS_PAPER_STOCK
        self._original_prod_type = settings.KIS_PROD_TYPE
        settings.KIS_ACCOUNT_TYPE = "VIRTUAL"

    def tearDown(self):
        settings.KIS_ACCOUNT_TYPE = self._original_account_type
        settings.KIS_PAPER_STOCK = self._original_paper_stock
        settings.KIS_PROD_TYPE = self._original_prod_type

    def test_get_account_parts_uses_account_suffix_when_prod_type_is_blank(self):
        settings.KIS_PAPER_STOCK = "1234567802"
        settings.KIS_PROD_TYPE = ""

        self.assertEqual(_get_account_parts(), ("12345678", "02"))

    def test_get_account_parts_accepts_cano_with_explicit_prod_type(self):
        settings.KIS_PAPER_STOCK = "12345678"
        settings.KIS_PROD_TYPE = "01"

        self.assertEqual(_get_account_parts(), ("12345678", "01"))

    def test_get_account_parts_rejects_cano_without_prod_type(self):
        settings.KIS_PAPER_STOCK = "12345678"
        settings.KIS_PROD_TYPE = ""

        with self.assertRaisesRegex(ValueError, "KIS_PROD_TYPE"):
            _get_account_parts()


class OverseasOrderListTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_account_type = settings.KIS_ACCOUNT_TYPE
        self._original_paper_stock = settings.KIS_PAPER_STOCK
        self._original_prod_type = settings.KIS_PROD_TYPE
        settings.KIS_ACCOUNT_TYPE = "VIRTUAL"
        settings.KIS_PAPER_STOCK = "1234567801"
        settings.KIS_PROD_TYPE = ""

    def tearDown(self):
        settings.KIS_ACCOUNT_TYPE = self._original_account_type
        settings.KIS_PAPER_STOCK = self._original_paper_stock
        settings.KIS_PROD_TYPE = self._original_prod_type

    async def test_get_overseas_order_list_includes_required_query_fields(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": []})

        with patch("trading.kis_api._request_paged_json", mock_request):
            await get_overseas_order_list("NASDAQ")

        params = mock_request.await_args.kwargs["params"]
        self.assertIn("ORD_DT", params)
        self.assertIn("ORD_GNO_BRNO", params)
        self.assertIn("ODNO", params)
        self.assertEqual(params["ORD_DT"], "")
        self.assertEqual(params["ORD_GNO_BRNO"], "")
        self.assertEqual(params["ODNO"], "")

    async def test_get_overseas_daily_price_uses_unadjusted_basis_for_intraday_alignment(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output2": []})

        with patch("trading.kis_api._request_json", mock_request):
            await get_overseas_daily_price("NVDA", "NASDAQ")

        params = mock_request.await_args.kwargs["params"]
        self.assertEqual(params["MODP"], "0")


class TradingProfileConfigTest(unittest.TestCase):
    def setUp(self):
        self._original_account_type = settings.KIS_ACCOUNT_TYPE

    def tearDown(self):
        settings.KIS_ACCOUNT_TYPE = self._original_account_type

    def test_virtual_account_uses_virtual_token_file_and_domain(self):
        settings.KIS_ACCOUNT_TYPE = "VIRTUAL"

        self.assertEqual(str(_get_token_file()), "data/kis_token.virtual.json")
        self.assertEqual(_get_trading_domain(), VIRTUAL_DOMAIN)

    def test_real_account_uses_real_token_file_and_domain(self):
        settings.KIS_ACCOUNT_TYPE = "REAL"

        self.assertEqual(str(_get_token_file()), "data/kis_token.real.json")
        self.assertEqual(_get_trading_domain(), DOMAIN)


class OverseasOrderRoutingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_account_type = settings.KIS_ACCOUNT_TYPE
        self._original_acct_stock = settings.KIS_ACCT_STOCK
        self._original_prod_type = settings.KIS_PROD_TYPE
        settings.KIS_ACCOUNT_TYPE = "REAL"
        settings.KIS_ACCT_STOCK = "1234567801"
        settings.KIS_PROD_TYPE = ""

    def tearDown(self):
        settings.KIS_ACCOUNT_TYPE = self._original_account_type
        settings.KIS_ACCT_STOCK = self._original_acct_stock
        settings.KIS_PROD_TYPE = self._original_prod_type

    async def test_place_overseas_order_routes_us_premarket_buy_to_regular_order(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

        with patch("trading.kis_api._request_json", mock_request):
            await place_overseas_order(
                symbol="PLTR",
                side="BUY",
                quantity=2.0,
                price=197.25,
                market="NASDAQ",
                session="US_PRE",
            )

        self.assertEqual(mock_request.await_args.args[0], "/uapi/overseas-stock/v1/trading/order")
        self.assertEqual(mock_request.await_args.args[1], "TTTT1002U")
        params = mock_request.await_args.kwargs["params"]
        self.assertEqual(params["OVRS_EXCG_CD"], "NASD")
        self.assertEqual(params["ORD_QTY"], "2")
        self.assertEqual(params["OVRS_ORD_UNPR"], "197.25")
        self.assertEqual(params["ORD_DVSN"], "00")
        self.assertEqual(params["SLL_TYPE"], "")

    async def test_place_overseas_order_keeps_daytime_order_for_explicit_us_daytime_session(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

        with patch("trading.kis_api._request_json", mock_request):
            await place_overseas_order(
                symbol="PLTR",
                side="BUY",
                quantity=2.0,
                price=197.25,
                market="NASDAQ",
                session="US_DAYTIME",
            )

        self.assertEqual(mock_request.await_args.args[0], "/uapi/overseas-stock/v1/trading/daytime-order")
        self.assertEqual(mock_request.await_args.args[1], "TTTS6036U")
        params = mock_request.await_args.kwargs["params"]
        self.assertEqual(params["ORD_DVSN"], "00")
        self.assertNotIn("SLL_TYPE", params)

    async def test_place_overseas_order_keeps_regular_route_for_us_regular_buy(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

        with patch("trading.kis_api._request_json", mock_request):
            await place_overseas_order(
                symbol="PLTR",
                side="BUY",
                quantity=1,
                price=198.5,
                market="NASDAQ",
                session="US_REGULAR",
            )

        self.assertEqual(mock_request.await_args.args[0], "/uapi/overseas-stock/v1/trading/order")
        self.assertEqual(mock_request.await_args.args[1], "TTTT1002U")
        params = mock_request.await_args.kwargs["params"]
        self.assertEqual(params["SLL_TYPE"], "")
        self.assertEqual(params["ORD_DVSN"], "00")

    async def test_place_overseas_order_keeps_regular_route_for_us_regular_sell(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

        with patch("trading.kis_api._request_json", mock_request):
            await place_overseas_order(
                symbol="PLTR",
                side="SELL",
                quantity=1,
                price=198.5,
                market="NASDAQ",
                session="US_REGULAR",
            )

        self.assertEqual(mock_request.await_args.args[0], "/uapi/overseas-stock/v1/trading/order")
        self.assertEqual(mock_request.await_args.args[1], "TTTT1006U")
        params = mock_request.await_args.kwargs["params"]
        self.assertEqual(params["SLL_TYPE"], "00")
        self.assertEqual(params["ORD_DVSN"], "00")

    async def test_place_overseas_order_rejects_zero_price_for_us_orders_before_request(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

        with patch("trading.kis_api._request_json", mock_request):
            result = await place_overseas_order(
                symbol="PLTR",
                side="SELL",
                quantity=1,
                price=None,
                market="NASDAQ",
                session="US_REGULAR",
            )

        mock_request.assert_not_awaited()
        self.assertFalse(result["success"])
        self.assertEqual(result["msg_cd"], "LOCAL_PRICE_REQUIRED")
        self.assertEqual(result["resolved_limit_price"], "0")

    async def test_cancel_overseas_order_routes_us_premarket_to_regular_cancel(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

        with patch("trading.kis_api._request_json", mock_request):
            await cancel_overseas_order(
                symbol="PLTR",
                order_id="00001234",
                quantity=3,
                price=197.1,
                market="NASDAQ",
                session="US_PRE",
            )

        self.assertEqual(
            mock_request.await_args.args[0],
            "/uapi/overseas-stock/v1/trading/order-rvsecncl",
        )
        self.assertEqual(mock_request.await_args.args[1], "TTTT1004U")
        params = mock_request.await_args.kwargs["params"]
        self.assertEqual(params["ORD_QTY"], "3")
        self.assertEqual(params["OVRS_ORD_UNPR"], "197.1")
        self.assertEqual(params["RVSE_CNCL_DVSN_CD"], "02")

    async def test_cancel_overseas_order_keeps_daytime_cancel_for_explicit_us_daytime_session(self):
        mock_request = AsyncMock(return_value={"rt_cd": "0", "output": {}})

        with patch("trading.kis_api._request_json", mock_request):
            await cancel_overseas_order(
                symbol="PLTR",
                order_id="00001234",
                quantity=3,
                price=197.1,
                market="NASDAQ",
                session="US_DAYTIME",
            )

        self.assertEqual(
            mock_request.await_args.args[0],
            "/uapi/overseas-stock/v1/trading/daytime-order-rvsecncl",
        )
        self.assertEqual(mock_request.await_args.args[1], "TTTS6038U")


if __name__ == "__main__":
    unittest.main()
