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

    async def test_get_orderable_amount_uses_foreign_amount_when_krw_amount_is_zero(self):
        client = MCPClient()
        client._call_overseas_balance = AsyncMock(return_value=MCPResponse(
            success=True,
            data={
                "output": {
                    "frcr_ord_psbl_amt1": "6602.449718",
                    "ovrs_ord_psbl_amt": "0",
                    "ovrs_max_ord_psbl_qty": "33",
                    "ord_psbl_qty": "0",
                },
            },
        ))
        client._get_exchange_rate_to_krw = AsyncMock(return_value=1479.8)

        response = await client.get_orderable_amount("COIN", 196.12, market="NASDAQ")

        self.assertTrue(response.success)
        self.assertEqual(response.data["orderable_amount_source"], "INQUIRE_PSAMOUNT")
        self.assertEqual(response.data["orderable_qty"], 33)
        self.assertEqual(response.data["orderable_amount_foreign"], 6602.449718)
        self.assertAlmostEqual(
            response.data["orderable_amount_krw"],
            round(6602.449718 * 1479.8, 4),
        )

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


class MCPClientHybridScanTest(unittest.IsolatedAsyncioTestCase):
    """US 동적 발굴 + 워치리스트 하이브리드 병합 테스트"""

    def _make_stock(self, symbol, volume=100, change_rate=1.0, scan_source="WATCHLIST"):
        return {
            "symbol": symbol,
            "name": symbol,
            "market": "NASDAQ",
            "currency": "USD",
            "price": 10.0,
            "current_price": 10.0,
            "change": 0.5,
            "change_rate": change_rate,
            "volume": volume,
            "scan_source": scan_source,
        }

    async def test_hybrid_volume_rank_merges_discovery_and_watchlist(self):
        """US_DYNAMIC_DISCOVERY_ENABLED=True: MCP 성공 시 동적 결과 + 워치리스트 병합"""
        client = MCPClient()

        discovery_items = [{"symb": "TSLA", "last": "250", "tvol": "9999", "rate": "3.0"}]
        watchlist_stocks = [self._make_stock("SOFI", volume=500)]

        with (
            patch.object(client, "call_any_tool", new=AsyncMock(return_value=MCPResponse(
                success=True,
                data={"output1": discovery_items},
            ))),
            patch.object(client, "_build_watchlist_scan", new=AsyncMock(return_value=watchlist_stocks)),
            patch("trading.mcp_client.settings") as mock_settings,
        ):
            mock_settings.US_DYNAMIC_DISCOVERY_ENABLED = True
            response = await client.get_volume_rank(market="NASDAQ")

        self.assertTrue(response.success)
        symbols = [s["symbol"] for s in response.data["stocks"]]
        self.assertIn("TSLA", symbols)
        self.assertIn("SOFI", symbols)

    async def test_hybrid_volume_rank_watchlist_only_when_mcp_fails(self):
        """US_DYNAMIC_DISCOVERY_ENABLED=True: MCP 실패 시 워치리스트만 반환"""
        client = MCPClient()

        watchlist_stocks = [self._make_stock("PLTR", volume=300)]

        with (
            patch.object(client, "call_any_tool", new=AsyncMock(return_value=MCPResponse(
                success=False,
                error="MCP 도구 실패",
            ))),
            patch.object(client, "_build_watchlist_scan", new=AsyncMock(return_value=watchlist_stocks)),
            patch("trading.mcp_client.settings") as mock_settings,
        ):
            mock_settings.US_DYNAMIC_DISCOVERY_ENABLED = True
            response = await client.get_volume_rank(market="NASDAQ")

        self.assertTrue(response.success)
        symbols = [s["symbol"] for s in response.data["stocks"]]
        self.assertEqual(symbols, ["PLTR"])

    async def test_volume_rank_watchlist_only_when_discovery_disabled(self):
        """US_DYNAMIC_DISCOVERY_ENABLED=False: 워치리스트만 사용"""
        client = MCPClient()

        watchlist_stocks = [self._make_stock("NIO", volume=200)]

        with (
            patch.object(client, "call_any_tool", new=AsyncMock()) as mcp_mock,
            patch.object(client, "_build_watchlist_scan", new=AsyncMock(return_value=watchlist_stocks)),
            patch("trading.mcp_client.settings") as mock_settings,
        ):
            mock_settings.US_DYNAMIC_DISCOVERY_ENABLED = False
            response = await client.get_volume_rank(market="NASDAQ")

        mcp_mock.assert_not_awaited()
        self.assertTrue(response.success)
        self.assertEqual(response.data["stocks"][0]["symbol"], "NIO")

    async def test_merge_deduplicates_symbols(self):
        """동적 발굴과 워치리스트에 같은 심볼이 있으면 중복 없이 병합"""
        discovery = [
            {"symbol": "AAPL", "volume": 1000, "scan_source": "DISCOVERY"},
            {"symbol": "TSLA", "volume": 900, "scan_source": "DISCOVERY"},
        ]
        watchlist = [
            {"symbol": "AAPL", "volume": 500, "scan_source": "WATCHLIST"},  # 중복
            {"symbol": "SOFI", "volume": 400, "scan_source": "WATCHLIST"},
        ]
        merged = MCPClient._merge_discovery_and_watchlist(discovery, watchlist)
        symbols = [s["symbol"] for s in merged]
        self.assertEqual(symbols, ["AAPL", "TSLA", "SOFI"])
        # AAPL은 discovery 버전 유지 (volume=1000)
        self.assertEqual(merged[0]["volume"], 1000)
        self.assertEqual(merged[0]["scan_source"], "DISCOVERY")

    async def test_hybrid_volume_rank_applies_cap_after_sorting(self):
        """미국 거래량 스캔은 병합 후 정렬하고 마지막에만 cap을 적용한다"""
        client = MCPClient()
        discovery_items = [
            {"symb": f"D{i:02d}", "last": "10", "tvol": str(500 - i), "rate": "1.0"}
            for i in range(35)
        ]
        watchlist_stocks = [self._make_stock("SOFI", volume=9999)]

        with (
            patch.object(client, "call_any_tool", new=AsyncMock(return_value=MCPResponse(
                success=True,
                data={"output1": discovery_items},
            ))),
            patch.object(client, "_build_watchlist_scan", new=AsyncMock(return_value=watchlist_stocks)),
            patch("trading.mcp_client.settings") as mock_settings,
        ):
            mock_settings.US_DYNAMIC_DISCOVERY_ENABLED = True
            response = await client.get_volume_rank(market="NASDAQ")

        self.assertTrue(response.success)
        stocks = response.data["stocks"]
        self.assertEqual(len(stocks), 30)
        self.assertEqual(stocks[0]["symbol"], "SOFI")
        self.assertEqual(stocks[0]["scan_source"], "WATCHLIST")

    async def test_hybrid_fluctuation_rank_merges_and_sorts(self):
        """등락률 하이브리드 스캔: 병합 후 change_rate 기준 정렬"""
        client = MCPClient()

        discovery_items = [{"symb": "MARA", "last": "20", "tvol": "800", "rate": "5.0"}]
        watchlist_stocks = [self._make_stock("RIOT", volume=300, change_rate=8.0)]

        with (
            patch.object(client, "call_any_tool", new=AsyncMock(return_value=MCPResponse(
                success=True,
                data={"output1": discovery_items},
            ))),
            patch.object(client, "_build_watchlist_scan", new=AsyncMock(return_value=watchlist_stocks)),
            patch("trading.mcp_client.settings") as mock_settings,
        ):
            mock_settings.US_DYNAMIC_DISCOVERY_ENABLED = True
            response = await client.get_fluctuation_rank(market="NASDAQ", sort="top")

        self.assertTrue(response.success)
        stocks = response.data["stocks"]
        # change_rate 내림차순: RIOT(8.0) > MARA(5.0)
        self.assertEqual(stocks[0]["symbol"], "RIOT")

    async def test_hybrid_fluctuation_rank_applies_cap_after_sorting(self):
        """미국 등락률 스캔도 병합 후 정렬하고 마지막에만 cap을 적용한다"""
        client = MCPClient()
        discovery_items = [
            {"symb": f"M{i:02d}", "last": "10", "tvol": "1000", "rate": str(50 - i)}
            for i in range(35)
        ]
        watchlist_stocks = [self._make_stock("AAPL", volume=1200, change_rate=99.0)]

        with (
            patch.object(client, "call_any_tool", new=AsyncMock(return_value=MCPResponse(
                success=True,
                data={"output1": discovery_items},
            ))),
            patch.object(client, "_build_watchlist_scan", new=AsyncMock(return_value=watchlist_stocks)),
            patch("trading.mcp_client.settings") as mock_settings,
        ):
            mock_settings.US_DYNAMIC_DISCOVERY_ENABLED = True
            response = await client.get_fluctuation_rank(market="NASDAQ", sort="top")

        self.assertTrue(response.success)
        stocks = response.data["stocks"]
        self.assertEqual(len(stocks), 30)
        self.assertEqual(stocks[0]["symbol"], "AAPL")
        self.assertEqual(stocks[0]["scan_source"], "WATCHLIST")


if __name__ == "__main__":
    unittest.main()
