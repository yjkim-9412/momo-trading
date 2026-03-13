import json
import unittest
from unittest.mock import AsyncMock, patch

from trading.mcp_client import MCPClient


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


if __name__ == "__main__":
    unittest.main()
