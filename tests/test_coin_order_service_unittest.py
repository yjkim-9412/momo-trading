import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from exceptions.common import ServiceException
from schemas.coin_order_schema import CoinOrderPlaceRequest, CoinOrderPreviewRequest, CoinOrderPreviewResponse
from services.coin_order_service import CoinOrderService, _BrokerErrorInfo
from trading.enums import OrderSide, OrderType
from trading.models import MCPResponse


class CoinOrderServicePreviewTest(unittest.IsolatedAsyncioTestCase):
    async def test_preview_limit_buy_rounds_quantity_up_to_cover_requested_amount(self):
        service = CoinOrderService(AsyncMock())
        request = CoinOrderPreviewRequest(
            symbol="BTC",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            amount_krw=5_000.0,
            limit_price=10_456_000.0,
        )

        with (
            patch(
                "services.coin_order_service.bithumb_client.get_current_price",
                AsyncMock(return_value=MCPResponse(success=True, data={"price": 104_558_000.0})),
            ),
            patch(
                "services.coin_order_service.bithumb_client.get_orderable_amount",
                AsyncMock(
                    return_value=MCPResponse(
                        success=True,
                        data={
                            "available_krw": 70_000.974539,
                            "orderable_quantity": 0.00669012,
                            "bid_fee_rate": 0.0025,
                        },
                    )
                ),
            ),
        ):
            preview = await service.preview_order(request)

        self.assertTrue(preview.placeable)
        self.assertAlmostEqual(preview.normalized_quantity, 0.0004782, places=8)
        self.assertAlmostEqual(preview.normalized_amount_krw, 5_000.0592, places=4)
        self.assertEqual(preview.broker_order_type, "LIMIT")
        self.assertEqual(preview.broker_payload_preview["ord_type"], "limit")

    async def test_preview_sell_reports_insufficient_available_quantity(self):
        service = CoinOrderService(AsyncMock())
        request = CoinOrderPreviewRequest(
            symbol="BTC",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=0.2,
        )

        with (
            patch(
                "services.coin_order_service.bithumb_client.get_current_price",
                AsyncMock(return_value=MCPResponse(success=True, data={"price": 104_558_000.0})),
            ),
            patch(
                "services.coin_order_service.bithumb_client.get_holdings",
                AsyncMock(
                    return_value=MCPResponse(
                        success=True,
                        data={
                            "holdings": [
                                {
                                    "symbol": "BTC",
                                    "available_quantity": 0.1,
                                    "locked_quantity": 0.0,
                                }
                            ]
                        },
                    )
                ),
            ),
        ):
            preview = await service.preview_order(request)

        self.assertFalse(preview.placeable)
        self.assertTrue(
            any("보유 가능 수량 부족" in error for error in preview.validation_errors)
        )


class CoinOrderServiceExecutionTest(unittest.IsolatedAsyncioTestCase):
    def _preview(self) -> CoinOrderPreviewResponse:
        return CoinOrderPreviewResponse(
            symbol="BTC",
            market="BITHUMB",
            side="BUY",
            order_type="MARKET",
            broker_order_type="PRICE",
            placeable=True,
            reference_price=104_558_000.0,
            requested_amount_krw=5_000.0,
            normalized_amount_krw=5_000.0,
            normalized_quantity=0.00004782,
            broker_payload_preview={"ord_type": "price", "price": "5000"},
        )

    async def test_place_order_maps_under_price_limit_error_to_bad_request(self):
        service = CoinOrderService(AsyncMock())
        request = CoinOrderPlaceRequest(
            symbol="BTC",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            amount_krw=5_000.0,
            limit_price=5_000.0,
        )

        with (
            patch.object(service, "preview_order", AsyncMock(return_value=self._preview())),
            patch.object(service, "_log_order_error", AsyncMock()),
            patch(
                "services.coin_order_service.bithumb_client.place_order",
                AsyncMock(
                    return_value=MCPResponse(
                        success=False,
                        error="[under_price_limit_bid] 주문가격은 최소 10457000.00 이상으로 주문 가능합니다.",
                        data={
                            "error": {
                                "name": "under_price_limit_bid",
                                "message": "주문가격은 최소 10457000.00 이상으로 주문 가능합니다.",
                            }
                        },
                    )
                ),
            ),
        ):
            with self.assertRaises(ServiceException) as ctx:
                await service.place_order(request)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.data["error_category"], "validation")
        self.assertEqual(ctx.exception.data["broker_error_code"], "under_price_limit_bid")

    async def test_place_order_records_manual_source_on_success(self):
        service = CoinOrderService(AsyncMock())
        request = CoinOrderPlaceRequest(
            symbol="BTC",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount_krw=5_000.0,
        )
        payload = {
            "order_id": "order-1",
            "symbol": "BTC",
            "side": "BUY",
            "state": "WAIT",
            "order_type": "PRICE",
            "requested_amount_krw": 5_000.0,
            "order_qty": 0.00004782,
            "filled_qty": 0.0,
            "remaining_qty": 0.00004782,
            "filled_price": 0.0,
        }

        with (
            patch.object(service, "preview_order", AsyncMock(return_value=self._preview())),
            patch.object(service, "_poll_broker_order", AsyncMock(return_value=(payload, None))),
            patch.object(service, "_upsert_broker_order", AsyncMock()) as upsert_mock,
            patch("services.coin_order_service.account_manager.invalidate_cache"),
            patch("services.coin_order_service.activity_logger.log", AsyncMock()),
            patch(
                "services.coin_order_service.bithumb_client.place_order",
                AsyncMock(return_value=MCPResponse(success=True, data={"order_id": "order-1"})),
            ),
        ):
            result = await service.place_order(request)

        self.assertEqual(result.order_id, "order-1")
        self.assertEqual(result.source, "MANUAL_API")
        self.assertEqual(result.status, "SUBMITTED")
        self.assertEqual(upsert_mock.await_args.kwargs["source"], "MANUAL_API")

    async def test_get_order_status_uses_ledger_fallback_when_broker_lost_order(self):
        service = CoinOrderService(AsyncMock())
        ledger = SimpleNamespace(
            bithumb_order_id="order-1",
            symbol="BTC",
            side="BUY",
            status="SUBMITTED",
            order_type="PRICE",
            source="MANUAL_API",
            requested_price=0.0,
            requested_amount_krw=5_000.0,
            quantity=0.00004782,
            filled_quantity=0.0,
            filled_price=0.0,
            submitted_at=datetime(2026, 3, 15, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 15, 0, 1, tzinfo=timezone.utc),
            status_detail="WAIT",
        )

        with (
            patch.object(service, "_get_ledger_order", AsyncMock(return_value=ledger)),
            patch.object(
                service,
                "_poll_broker_order",
                AsyncMock(return_value=(None, _BrokerErrorInfo("order_state", "order_not_found", "주문 없음"))),
            ),
        ):
            result = await service.get_order_status("order-1")

        self.assertFalse(result.broker_synced)
        self.assertEqual(result.source, "MANUAL_API")
        self.assertEqual(result.status, "SUBMITTED")
        self.assertEqual(result.order_id, "order-1")

    async def test_cancel_order_rejects_already_filled_order(self):
        service = CoinOrderService(AsyncMock())
        payload = {
            "symbol": "BTC",
            "side": "BUY",
            "state": "DONE",
            "order_type": "PRICE",
            "order_qty": 0.00004782,
            "filled_qty": 0.00004782,
            "remaining_qty": 0.0,
            "requested_amount_krw": 5_000.0,
            "filled_price": 104_500_000.0,
        }

        with (
            patch.object(service, "_get_ledger_order", AsyncMock(return_value=None)),
            patch.object(service, "_poll_broker_order", AsyncMock(return_value=(payload, None))),
        ):
            with self.assertRaises(ServiceException) as ctx:
                await service.cancel_order("order-1")

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.data["error_category"], "order_state")
