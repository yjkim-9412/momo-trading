import asyncio
import json

from trading.mcp_client import MCPClient


class _DummyResponse:
    status_code = 202


class _DummyPostClient:
    def __init__(self, handler):
        self._handler = handler

    async def post(self, session_id, json):
        return await self._handler(session_id, json)


def test_call_tool_marks_kis_business_error_as_failure():
    async def scenario():
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
        return await client._call_tool_inner("inquery-balance", None, 0)

    response = asyncio.run(scenario())

    assert response.success is False
    assert response.error == "ERROR INVALID INPUT_FILED_SIZE"
    assert response.data["rt_cd"] == "2"
