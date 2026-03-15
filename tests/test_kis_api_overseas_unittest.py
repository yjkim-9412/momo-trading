import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from trading.kis_api import get_overseas_order_list


class OverseasOrderListApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_paper_order_list_uses_market_local_date_and_blank_exchange(self):
        captured: dict = {}

        async def fake_request(api_url, tr_id, params, output_keys):
            captured["api_url"] = api_url
            captured["tr_id"] = tr_id
            captured["params"] = params
            return {"rt_cd": "0", "output": []}

        with (
            patch("trading.kis_api.settings.KIS_ACCOUNT_TYPE", "VIRTUAL"),
            patch("scheduler.market_calendar.market_calendar.market_date", return_value=date(2026, 3, 13)),
            patch("trading.kis_api._request_paged_json", new=AsyncMock(side_effect=fake_request)),
        ):
            result = await get_overseas_order_list("NASDAQ")

        self.assertTrue(result["success"])
        self.assertEqual(captured["api_url"], "/uapi/overseas-stock/v1/trading/inquire-ccnl")
        self.assertEqual(captured["tr_id"], "VTTS3035R")
        self.assertEqual(captured["params"]["ORD_STRT_DT"], "20260313")
        self.assertEqual(captured["params"]["ORD_END_DT"], "20260313")
        self.assertEqual(captured["params"]["OVRS_EXCG_CD"], "")


if __name__ == "__main__":
    unittest.main()
