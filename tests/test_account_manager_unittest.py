import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.config import settings
from trading.account_manager import AccountManager
from trading.models import AccountBalance, HoldingInfo, MCPResponse, PendingOrderInfo


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
        self.assertAlmostEqual(balance.operating_cash, balance.total_asset - expected_stock_value)
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

    def test_parse_balance_converts_real_us_cash_from_foreign_to_krw(self):
        settings.KIS_ACCOUNT_TYPE = "REAL"
        holdings = [
            HoldingInfo(
                symbol="NVDA",
                name="NVIDIA",
                market="NASDAQ",
                currency="USD",
                quantity=1,
                avg_buy_price=95.0,
                current_price=100.0,
                pnl=5.0,
                pnl_rate=5.26,
                exchange_rate_to_krw=1506.2,
            )
        ]
        data = {
            "output2": [{
                "cash_foreign": "100.000000",
                "orderable_cash_foreign": "100.000000",
                "stock_value": "150620.000000",
                "total_asset": "654619",
                "total_pnl": "0.00000000",
                "total_pnl_rate": "0.0000000000",
                "exchange_rate_to_krw": "1506.20000000",
            }],
        }

        balance = self.manager._parse_balance(data, holdings=holdings, market="NASDAQ")

        self.assertAlmostEqual(balance.cash, 150620.0)
        self.assertAlmostEqual(balance.raw_cash, 150620.0)
        self.assertAlmostEqual(balance.effective_cash, 150620.0)
        self.assertAlmostEqual(balance.cash_foreign, 100.0)
        self.assertAlmostEqual(balance.raw_cash_foreign, 100.0)
        self.assertAlmostEqual(balance.effective_cash_foreign, 100.0)
        self.assertAlmostEqual(balance.stock_value, 150620.0)
        self.assertAlmostEqual(balance.stock_value_foreign, 100.0)
        self.assertAlmostEqual(balance.total_asset, 654619.0)
        self.assertAlmostEqual(balance.total_asset_foreign, 200.0)
        self.assertAlmostEqual(balance.operating_cash, 503999.0)
        self.assertAlmostEqual(balance.operating_cash_foreign, 100.0)
        self.assertEqual(balance.cash_source, "BROKER")
        self.assertEqual(balance.currency, "KRW")
        self.assertAlmostEqual(balance.exchange_rate_to_krw, 1506.2)

    def test_parse_balance_does_not_convert_krw_total_asset_back_to_usd_without_foreign_components(self):
        settings.KIS_ACCOUNT_TYPE = "REAL"
        data = {
            "output2": [{
                "cash_foreign": "0",
                "orderable_cash_foreign": "0",
                "stock_value": "150620.000000",
                "total_asset": "654619",
                "total_pnl": "0.00000000",
                "total_pnl_rate": "0.0000000000",
                "exchange_rate_to_krw": "1506.20000000",
            }],
        }

        balance = self.manager._parse_balance(data, holdings=[], market="NASDAQ")

        self.assertEqual(balance.cash_foreign, 0.0)
        self.assertEqual(balance.effective_cash_foreign, 0.0)
        self.assertEqual(balance.stock_value_foreign, 0.0)
        self.assertEqual(balance.total_asset_foreign, 0.0)

    def test_parse_balance_uses_domestic_deposit_when_orderable_missing(self):
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
        self.assertEqual(balance.operating_cash, 1500000.0)
        self.assertEqual(balance.cash_source, "BROKER_DEPOSIT")
        self.assertEqual(balance.raw_total_pnl, 0.0)
        self.assertEqual(balance.raw_total_pnl_rate, 0.0)
        self.assertEqual(balance.pnl_source, "BROKER_SUMMARY")

    def test_parse_balance_uses_domestic_orderable_cash_and_summary_values(self):
        holdings = [
            HoldingInfo(
                symbol="005930",
                name="삼성전자",
                market="KRX",
                currency="KRW",
                quantity=1000,
                avg_buy_price=100000.0,
                current_price=119431.8,
                pnl=19431800.0,
                pnl_rate=19.43,
                exchange_rate_to_krw=1.0,
            )
        ]
        data = {
            "output2": [{
                "dnca_tot_amt": "10000000",
                "ord_psbl_amt": "1000000",
                "tot_evlu_amt": "98628090",
                "scts_evlu_amt": "88628090",
                "evlu_pfls_smtl_amt": "-1354800",
                "pchs_amt_smtl_amt": "99982890",
            }],
        }

        with patch("trading.account_manager.logger.warning") as warning_mock:
            balance = self.manager._parse_balance(data, holdings=holdings, market="KRX")

        self.assertEqual(balance.cash, 10000000.0)
        self.assertEqual(balance.raw_cash, 10000000.0)
        self.assertEqual(balance.effective_cash, 1000000.0)
        self.assertEqual(balance.operating_cash, 10000000.0)
        self.assertEqual(balance.cash_source, "BROKER_ORDERABLE")
        self.assertEqual(balance.total_asset, 98628090.0)
        self.assertEqual(balance.stock_value, 88628090.0)
        self.assertEqual(balance.total_pnl, -1354800.0)
        self.assertAlmostEqual(balance.total_pnl_rate, (-1354800.0 / 99982890.0) * 100)
        self.assertEqual(balance.pnl_source, "BROKER_SUMMARY")
        warning_mock.assert_called_once()

    def test_parse_balance_marks_error_payload_invalid(self):
        balance = self.manager._parse_balance(
            {"rt_cd": "2", "msg1": "ERROR INVALID INPUT_FILED_SIZE"},
            holdings=[],
            market="KRX",
        )

        self.assertFalse(balance.is_valid)
        self.assertEqual(balance.total_asset, 0.0)
        self.assertIn("INVALID INPUT_FILED_SIZE", balance.status_message)

def test_get_account_snapshot_returns_invalid_balance_on_mcp_failure():
    async def scenario():
        manager = AccountManager()
        response = MCPResponse(
            success=False,
            error="ERROR INVALID INPUT_FILED_SIZE",
            data={"rt_cd": "2", "msg1": "ERROR INVALID INPUT_FILED_SIZE"},
        )

        with patch(
            "trading.account_manager.mcp_client.get_account_balance",
            AsyncMock(return_value=response),
        ):
            return await manager.get_account_snapshot("KRX")

    balance, holdings = asyncio.run(scenario())

    assert balance.is_valid is False
    assert balance.total_asset == 0.0
    assert holdings == []
    assert "INVALID INPUT_FILED_SIZE" in balance.status_message


def test_get_account_overview_uses_sequential_snapshot_then_orders():
    async def scenario():
        manager = AccountManager()
        calls: list[tuple[str, str | None]] = []
        expected_balance = AccountBalance(
            total_asset=1000,
            cash=100,
            raw_cash=100,
            effective_cash=100,
            cash_source="BROKER",
            stock_value=900,
            total_pnl=10,
            total_pnl_rate=1,
            market="NASDAQ",
            currency="KRW",
            exchange_rate_to_krw=1450.0,
        )
        expected_holdings = [
            HoldingInfo(
                symbol="NVDA",
                name="NVIDIA",
                market="NASDAQ",
                currency="USD",
                quantity=1,
                avg_buy_price=100.0,
                current_price=110.0,
                pnl=10.0,
                pnl_rate=10.0,
                exchange_rate_to_krw=1450.0,
            )
        ]
        expected_orders = [
            PendingOrderInfo(
                order_id="A1",
                symbol="NVDA",
                name="NVIDIA",
                market="NASDAQ",
                currency="USD",
                side="BUY",
                order_qty=1,
                filled_qty=0,
                remaining_qty=1,
                order_price=109.0,
                order_time="093000",
                exchange_rate_to_krw=1450.0,
            )
        ]

        async def fake_snapshot(market=None):
            calls.append(("snapshot", market))
            return expected_balance, expected_holdings

        async def fake_orders(market=None):
            calls.append(("orders", market))
            return expected_orders

        with patch.object(manager, "get_account_snapshot", AsyncMock(side_effect=fake_snapshot)):
            with patch.object(manager, "get_pending_orders", AsyncMock(side_effect=fake_orders)):
                overview = await manager.get_account_overview("NASDAQ")

        return overview, calls

    overview, calls = asyncio.run(scenario())

    assert calls == [("snapshot", "NASDAQ"), ("orders", "NASDAQ")]
    assert overview.balance.total_asset == 1000
    assert overview.holdings[0].symbol == "NVDA"
    assert overview.pending_orders[0].order_id == "A1"


def test_parse_pending_orders_reads_overseas_fill_fields():
    manager = AccountManager()
    orders = manager._parse_pending_orders(
        {
            "exchange_rate_to_krw": 1450.0,
            "output": [{
                "order_id": "0000041303",
                "symbol": "PLTR",
                "name": "Palantir",
                "market": "NASDAQ",
                "currency": "USD",
                "side_code": "02",
                "order_qty": "1000",
                "filled_qty": "250",
                "remaining_qty": "750",
                "order_price": "154.16",
                "order_time": "225710",
            }],
        },
        market="NASDAQ",
    )

    assert len(orders) == 1
    assert orders[0].order_id == "0000041303"
    assert orders[0].symbol == "PLTR"
    assert orders[0].filled_qty == 250
    assert orders[0].remaining_qty == 750
    assert orders[0].exchange_rate_to_krw == 1450.0


def test_get_pending_orders_reads_crypto_broker_ledger():
    class _ScalarResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _ExecuteResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _ScalarResult(self._rows)

    class _SessionContext:
        def __init__(self, session):
            self._session = session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def scenario():
        manager = AccountManager()
        now = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
        rows = [
            SimpleNamespace(
                bithumb_order_id="order-2",
                symbol="ETH",
                coin_name="이더리움",
                side="SELL",
                quantity=2.0,
                filled_quantity=0.5,
                requested_price=4200000.0,
                currency="KRW",
                status="PARTIAL",
                status_detail="partial fill",
                submitted_at=now,
                updated_at=now,
                created_at=now,
            ),
            SimpleNamespace(
                bithumb_order_id="order-1",
                symbol="BTC",
                coin_name="비트코인",
                side="BUY",
                quantity=1.0,
                filled_quantity=0.0,
                requested_price=150000000.0,
                currency="KRW",
                status="SUBMITTED",
                status_detail="submitted",
                submitted_at=now,
                updated_at=None,
                created_at=now.replace(hour=11),
            ),
        ]
        session = AsyncMock()
        session.execute.return_value = _ExecuteResult(rows)

        with patch("trading.account_manager.AsyncSessionLocal", return_value=_SessionContext(session)):
            return await manager.get_pending_orders("BITHUMB")

    orders = asyncio.run(scenario())

    assert [order.order_id for order in orders] == ["order-2", "order-1"]
    assert orders[0].side == "매도"
    assert orders[0].remaining_qty == 1.5
    assert orders[0].status == "PARTIAL"
    assert orders[1].side == "매수"
    assert orders[1].remaining_qty == 1.0
    assert orders[1].submitted_at is not None


def test_get_account_snapshot_deduplicates_concurrent_intraday_requests():
    async def scenario():
        manager = AccountManager()
        started = asyncio.Event()
        release = asyncio.Event()
        response = MCPResponse(
            success=True,
            data={
                "output1": [],
                "output2": [{
                    "dnca_tot_amt": "1500000",
                    "tot_evlu_amt": "2500000",
                    "scts_evlu_amt": "1000000",
                }],
            },
        )

        async def fake_get_account_balance(market=None):
            started.set()
            await release.wait()
            return response

        with (
            patch("trading.account_manager.market_calendar.is_trading_hours", return_value=True),
            patch(
                "trading.account_manager.mcp_client.get_account_balance",
                AsyncMock(side_effect=fake_get_account_balance),
            ) as balance_mock,
        ):
            task1 = asyncio.create_task(manager.get_account_snapshot("KRX"))
            await started.wait()
            task2 = asyncio.create_task(manager.get_account_snapshot("KRX"))
            await asyncio.sleep(0)
            assert balance_mock.await_count == 1
            release.set()
            first, second = await asyncio.gather(task1, task2)
            cached = await manager.get_account_snapshot("KRX")

        return balance_mock.await_count, first, second, cached

    await_count, first, second, cached = asyncio.run(scenario())

    assert await_count == 1
    assert first[0].total_asset == 2500000.0
    assert second[0].total_asset == 2500000.0
    assert cached[0].total_asset == 2500000.0


def test_get_account_snapshot_does_not_cache_invalid_intraday_balance():
    async def scenario():
        manager = AccountManager()
        responses = [
            MCPResponse(
                success=False,
                error="초당 거래건수를 초과하였습니다.",
                data={"rt_cd": "1", "msg1": "초당 거래건수를 초과하였습니다."},
            ),
            MCPResponse(
                success=True,
                data={
                    "output1": [],
                    "output2": [{
                        "dnca_tot_amt": "1500000",
                        "tot_evlu_amt": "2500000",
                        "scts_evlu_amt": "1000000",
                    }],
                },
            ),
        ]

        with (
            patch("trading.account_manager.market_calendar.is_trading_hours", return_value=True),
            patch(
                "trading.account_manager.mcp_client.get_account_balance",
                AsyncMock(side_effect=responses),
            ) as balance_mock,
        ):
            first = await manager.get_account_snapshot("KRX")
            second = await manager.get_account_snapshot("KRX")

        return balance_mock.await_count, first, second

    await_count, first, second = asyncio.run(scenario())

    assert await_count == 2
    assert first[0].is_valid is False
    assert second[0].is_valid is True


if __name__ == "__main__":
    unittest.main()
