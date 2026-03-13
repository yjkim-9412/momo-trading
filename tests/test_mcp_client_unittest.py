import json
import unittest
from unittest.mock import AsyncMock, patch

from trading.mcp_client import MCPClient
from trading.models import MCPResponse


class _DummyResponse:
    status_code = 202


class _DummyPostClient:
    def __init__(self, handler):
        self._handler = handler

    async def post(self, session_id, json):
        return await self._handler(session_id, json)


class MCPClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_call_tool_marks_kis_business_error_as_failure(self):
        client = MCPClient()
        client._session_id = "/messages/?session_id=test"

        async def fake_post(session_id, payload):
            client._pending[payload["id"]].set_result({
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "rt_cd": "2",
                            "msg1": "ERROR INVALID INPUT_FILED_SIZE",
                        }),
                    }],
                    "isError": False,
                },
            })
            return _DummyResponse()

        client._post_client = _DummyPostClient(fake_post)
        response = await client._call_tool_inner("inquery-balance", None, 0)

        self.assertFalse(response.success)
        self.assertEqual(response.error, "ERROR INVALID INPUT_FILED_SIZE")
        self.assertEqual(response.data["rt_cd"], "2")

    async def test_place_order_extracts_order_id_from_output_list(self):
        client = MCPClient()
        client._get_exchange_rate_to_krw = AsyncMock(return_value=1300.0)

        with patch(
            "trading.kis_api.place_overseas_order",
            new=AsyncMock(return_value={
                "success": True,
                "rt_cd": "0",
                "output": [
                    {"ODNO": "90123456", "ORD_TMD": "181530"},
                ],
            }),
        ):
            response = await client.place_order("COIN", "BUY", 1, price=196.6, market="NASDAQ")

        self.assertTrue(response.success)
        self.assertEqual(response.data["order_id"], "90123456")

    async def test_get_account_balance_fails_closed_when_overseas_summary_fails(self):
        client = MCPClient()

        with (
            patch(
                "trading.kis_api.get_overseas_present_balance",
                new=AsyncMock(return_value={
                    "success": False,
                    "error": "모의투자 서비스가 지연되고 있습니다.",
                }),
            ),
            patch(
                "trading.kis_api.get_overseas_balance",
                new=AsyncMock(return_value={
                    "success": True,
                    "output1": [{
                        "ovrs_pdno": "NVDA",
                        "ovrs_cblc_qty": "2",
                    }],
                    "output2": [],
                }),
            ) as holdings_mock,
        ):
            response = await client.get_account_balance("NASDAQ")

        self.assertFalse(response.success)
        self.assertIn("inquire-present-balance 실패", response.error)
        holdings_mock.assert_not_awaited()

    async def test_get_account_balance_fails_closed_when_summary_payload_is_incomplete(self):
        client = MCPClient()

        with (
            patch(
                "trading.kis_api.get_overseas_present_balance",
                new=AsyncMock(return_value={
                    "success": True,
                    "output2": [],
                    "output3": {},
                }),
            ),
            patch(
                "trading.kis_api.get_overseas_balance",
                new=AsyncMock(return_value={
                    "success": True,
                    "output1": [{
                        "ovrs_pdno": "NVDA",
                        "ovrs_cblc_qty": "2",
                    }],
                    "output2": [],
                }),
            ),
        ):
            response = await client.get_account_balance("NASDAQ")

        self.assertFalse(response.success)
        self.assertIn("해외 잔고 응답 불완전", response.error)

    async def test_get_account_balance_retries_on_overseas_balance_rate_limit(self):
        client = MCPClient()
        client._rate_limit_overseas_balance = AsyncMock()

        summary_responses = [
            {
                "success": False,
                "rt_cd": "1",
                "msg1": "초당 거래건수를 초과하였습니다.",
            },
            {
                "success": True,
                "output2": [{
                    "crcy_cd": "USD",
                    "frcr_dncl_amt_2": "100",
                    "frcr_drwg_psbl_amt_1": "100",
                    "frcr_evlu_amt2": "200",
                    "frst_bltn_exrt": "1450.0",
                }],
                "output3": {
                    "tot_asst_amt": "300",
                    "tot_evlu_pfls_amt": "0",
                    "evlu_erng_rt1": "0",
                },
            },
        ]

        async def fake_present_balance(market):
            return summary_responses.pop(0)

        with (
            patch(
                "trading.kis_api.get_overseas_present_balance",
                new=AsyncMock(side_effect=fake_present_balance),
            ) as summary_mock,
            patch(
                "trading.kis_api.get_overseas_balance",
                new=AsyncMock(return_value={
                    "success": True,
                    "output1": [],
                    "output2": [],
                }),
            ) as holdings_mock,
        ):
            response = await client.get_account_balance("NASDAQ")

        self.assertTrue(response.success)
        self.assertEqual(summary_mock.await_count, 2)
        holdings_mock.assert_awaited_once()

    async def test_get_exchange_rate_to_krw_uses_matching_currency_row(self):
        client = MCPClient()
        client._rate_limit_overseas_balance = AsyncMock()
        client._call_overseas_balance = AsyncMock(return_value=MCPResponse(
            success=True,
            data={
                "output2": [
                    {"crcy_cd": "HKD", "frst_bltn_exrt": "215.16"},
                    {"crcy_cd": "USD", "frst_bltn_exrt": "1450.0"},
                ],
                "output3": {"tot_asst_amt": "1000"},
            },
        ))

        rate = await client._get_exchange_rate_to_krw("NASDAQ")

        self.assertEqual(rate, 1450.0)

    async def test_get_order_list_normalizes_overseas_fill_fields(self):
        client = MCPClient()
        client._get_exchange_rate_to_krw = AsyncMock(return_value=1450.0)

        with patch(
            "trading.kis_api.get_overseas_order_list",
            new=AsyncMock(return_value={
                "success": True,
                "output": [{
                    "odno": "0000041303",
                    "pdno": "PLTR",
                    "prdt_name": "Palantir",
                    "ovrs_excg_cd": "NASD",
                    "sll_buy_dvsn_cd": "02",
                    "tr_crcy_cd": "USD",
                    "ft_ord_qty": "1000",
                    "ft_ord_unpr3": "154.16",
                    "ft_ccld_qty": "1000",
                    "ft_ccld_unpr3": "154.16",
                    "nccs_qty": "0",
                    "ord_tmd": "225710",
                    "prcs_stat_name": "체결",
                }],
            }),
        ):
            response = await client.get_order_list("NASDAQ")

        self.assertTrue(response.success)
        record = response.data["output"][0]
        self.assertEqual(record["order_id"], "0000041303")
        self.assertEqual(record["symbol"], "PLTR")
        self.assertEqual(record["filled_qty"], 1000)
        self.assertEqual(record["remaining_qty"], 0)
        self.assertEqual(record["filled_price"], 154.16)
        self.assertEqual(record["filled_price_krw"], 154.16 * 1450.0)
        self.assertEqual(record["side"], "BUY")


if __name__ == "__main__":
    unittest.main()
