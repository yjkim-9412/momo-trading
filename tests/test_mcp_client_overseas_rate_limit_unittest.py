import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from trading.mcp_client import MCPClient


class OverseasQuoteRateLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_current_price_retries_on_business_rate_limit(self):
        client = MCPClient()
        client._rate_limit_overseas_quote = AsyncMock()
        client._get_exchange_rate_to_krw = AsyncMock(return_value=1300.0)

        responses = [
            {
                "success": False,
                "rt_cd": "1",
                "msg1": "초당 거래건수를 초과하였습니다.",
            },
            {
                "success": True,
                "rt_cd": "0",
                "output": {
                    "last": "182.25",
                    "tvol": "12345",
                    "ovrs_item_name": "NVIDIA",
                },
            },
        ]

        async def fake_get_overseas_price(symbol, market):
            return responses.pop(0)

        with (
            patch("trading.kis_api.get_overseas_price", new=AsyncMock(side_effect=fake_get_overseas_price)) as price_mock,
        ):
            response = await client.get_current_price("NVDA", market="NASDAQ")

        self.assertTrue(response.success)
        self.assertEqual(response.data["price"], 182.25)
        self.assertEqual(response.data["volume"], 12345)
        self.assertEqual(price_mock.await_count, 2)

    async def test_overseas_quote_calls_are_serialized_across_quote_types(self):
        client = MCPClient()
        client._rate_limit_overseas_quote = AsyncMock()
        client._get_exchange_rate_to_krw = AsyncMock(return_value=1300.0)

        active_calls = 0
        max_active_calls = 0
        first_call_started = asyncio.Event()
        release_first_call = asyncio.Event()
        second_call_started = asyncio.Event()

        async def tracked_response(kind: str) -> dict:
            nonlocal active_calls, max_active_calls
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)

            if not first_call_started.is_set():
                first_call_started.set()
                await release_first_call.wait()
            else:
                second_call_started.set()

            try:
                if kind == "price":
                    return {
                        "success": True,
                        "rt_cd": "0",
                        "output": {
                            "last": "182.25",
                            "tvol": "12345",
                            "ovrs_item_name": "NVIDIA",
                        },
                    }
                if kind == "daily":
                    return {
                        "success": True,
                        "rt_cd": "0",
                        "output2": [{
                            "xymd": "20260313",
                            "open": "180.0",
                            "high": "183.0",
                            "low": "179.5",
                            "clos": "182.25",
                            "tvol": "12345",
                        }],
                    }
                return {
                    "success": True,
                    "rt_cd": "0",
                    "output2": [{
                        "xymd": "20260313173000",
                        "open": "181.0",
                        "high": "182.5",
                        "low": "180.9",
                        "clos": "182.25",
                        "tvol": "1200",
                    }],
                }
            finally:
                active_calls -= 1

        async def price_side_effect(symbol, market):
            return await tracked_response("price")

        async def daily_side_effect(symbol, market):
            return await tracked_response("daily")

        async def minute_side_effect(symbol, market, period="5"):
            return await tracked_response("minute")

        with (
            patch("trading.kis_api.get_overseas_price", new=AsyncMock(side_effect=price_side_effect)),
            patch("trading.kis_api.get_overseas_daily_price", new=AsyncMock(side_effect=daily_side_effect)),
            patch("trading.kis_api.get_overseas_minute_chart", new=AsyncMock(side_effect=minute_side_effect)),
        ):
            price_task = asyncio.create_task(client.get_current_price("NVDA", market="NASDAQ"))
            daily_task = asyncio.create_task(client.get_daily_price("NVDA", count=60, market="NASDAQ"))
            minute_task = asyncio.create_task(client.get_minute_price("NVDA", period="5", market="NASDAQ"))

            await first_call_started.wait()
            await asyncio.sleep(0)
            self.assertFalse(second_call_started.is_set())

            release_first_call.set()
            responses = await asyncio.gather(price_task, daily_task, minute_task)

        self.assertTrue(all(response.success for response in responses))
        self.assertEqual(max_active_calls, 1)
        self.assertTrue(second_call_started.is_set())

    async def test_get_daily_price_sorts_overseas_bars_oldest_first(self):
        client = MCPClient()
        client._rate_limit_overseas_quote = AsyncMock()

        mock_response = {
            "success": True,
            "rt_cd": "0",
            "output2": [
                {"xymd": "20260313", "open": "103", "high": "105", "low": "101", "clos": "104", "tvol": "300"},
                {"xymd": "20260311", "open": "99", "high": "101", "low": "98", "clos": "100", "tvol": "100"},
                {"xymd": "20260312", "open": "101", "high": "103", "low": "100", "clos": "102", "tvol": "200"},
            ],
        }

        with patch("trading.kis_api.get_overseas_daily_price", new=AsyncMock(return_value=mock_response)):
            response = await client.get_daily_price("NVDA", count=60, market="NASDAQ")

        self.assertTrue(response.success)
        self.assertEqual(
            [item["date"] for item in response.data["prices"]],
            ["20260311", "20260312", "20260313"],
        )

    async def test_get_minute_price_sorts_overseas_bars_oldest_first(self):
        client = MCPClient()
        client._rate_limit_overseas_quote = AsyncMock()

        mock_response = {
            "success": True,
            "rt_cd": "0",
            "output2": [
                {"xymd": "20260313093200", "open": "103", "high": "104", "low": "102", "clos": "103", "tvol": "300"},
                {"xymd": "20260313093000", "open": "100", "high": "101", "low": "99", "clos": "100", "tvol": "100"},
                {"xymd": "20260313093100", "open": "101", "high": "103", "low": "100", "clos": "102", "tvol": "200"},
            ],
        }

        with patch("trading.kis_api.get_overseas_minute_chart", new=AsyncMock(return_value=mock_response)):
            response = await client.get_minute_price("NVDA", period="5", market="NASDAQ")

        self.assertTrue(response.success)
        self.assertEqual(
            [item["time"] for item in response.data["prices"]],
            ["20260313093000", "20260313093100", "20260313093200"],
        )


class OverseasBalanceRateLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_account_balance_serializes_summary_then_holdings(self):
        client = MCPClient()
        client._rate_limit_overseas_balance = AsyncMock()

        active_calls = 0
        max_active_calls = 0
        summary_started = asyncio.Event()
        release_summary = asyncio.Event()
        holdings_started = asyncio.Event()

        async def present_balance_side_effect(market):
            nonlocal active_calls, max_active_calls
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            summary_started.set()
            await release_summary.wait()
            active_calls -= 1
            return {
                "success": True,
                "rt_cd": "0",
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
            }

        async def holdings_side_effect(market):
            nonlocal active_calls, max_active_calls
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
            holdings_started.set()
            active_calls -= 1
            return {
                "success": True,
                "rt_cd": "0",
                "output1": [],
                "output2": [],
            }

        with (
            patch(
                "trading.kis_api.get_overseas_present_balance",
                new=AsyncMock(side_effect=present_balance_side_effect),
            ),
            patch(
                "trading.kis_api.get_overseas_balance",
                new=AsyncMock(side_effect=holdings_side_effect),
            ),
        ):
            task = asyncio.create_task(client.get_account_balance("NASDAQ"))
            await summary_started.wait()
            await asyncio.sleep(0)
            self.assertFalse(holdings_started.is_set())
            release_summary.set()
            response = await task

        self.assertTrue(response.success)
        self.assertEqual(max_active_calls, 1)
        self.assertTrue(holdings_started.is_set())


if __name__ == "__main__":
    unittest.main()
