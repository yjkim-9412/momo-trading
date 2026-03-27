import pytest

from trading.account_manager import account_manager
from trading.models import AccountBalance, AccountOverview, HoldingInfo, PendingOrderInfo


@pytest.mark.asyncio
async def test_admin_account_balance_exposes_effective_cash(client, monkeypatch):
    async def fake_get_balance(market=None):
        return AccountBalance(
            total_asset=369166161,
            total_asset_foreign=254597.35,
            cash=0,
            cash_foreign=0,
            raw_cash=0,
            raw_cash_foreign=0,
            effective_cash=368635000,
            effective_cash_foreign=254231.03,
            operating_cash=368635000,
            operating_cash_foreign=254231.03,
            cash_source="TOTAL_ASSET_PROXY",
            stock_value=531161,
            stock_value_foreign=366.32,
            total_pnl=-249.441,
            total_pnl_rate=-0.05,
            raw_total_pnl=7497.903,
            raw_total_pnl_rate=1.23,
            pnl_source="HOLDINGS_SUM",
            market=market or "NASDAQ",
            currency="KRW",
            exchange_rate_to_krw=1450.0,
            status_message="모의투자 조회할 내역(자료)이 없습니다.",
        )

    monkeypatch.setattr(account_manager, "get_balance", fake_get_balance)

    response = await client.get("/api/v1/admin/account/balance?market=NASDAQ")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["cash"] == 0
    assert payload["raw_cash"] == 0
    assert payload["effective_cash"] == 368635000
    assert payload["operating_cash"] == 368635000
    assert payload["total_asset_foreign"] == 254597.35
    assert payload["effective_cash_foreign"] == 254231.03
    assert payload["stock_value_foreign"] == 366.32
    assert payload["cash_source"] == "TOTAL_ASSET_PROXY"
    assert payload["total_pnl"] == -249.441
    assert payload["raw_total_pnl"] == 7497.903
    assert payload["raw_total_pnl_rate"] == 1.23
    assert payload["pnl_source"] == "HOLDINGS_SUM"
    assert payload["is_valid"] is True
    assert "모의투자" in payload["status_message"]


@pytest.mark.asyncio
async def test_admin_account_balance_exposes_invalid_state(client, monkeypatch):
    async def fake_get_balance(market=None):
        return AccountBalance(
            total_asset=0,
            cash=0,
            raw_cash=0,
            effective_cash=0,
            cash_source="BROKER",
            stock_value=0,
            total_pnl=0,
            total_pnl_rate=0,
            market=market or "KRX",
            currency="KRW",
            exchange_rate_to_krw=1.0,
            status_message="ERROR INVALID INPUT_FILED_SIZE",
            is_valid=False,
        )

    monkeypatch.setattr(account_manager, "get_balance", fake_get_balance)

    response = await client.get("/api/v1/admin/account/balance?market=KRX")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["is_valid"] is False
    assert "INVALID INPUT_FILED_SIZE" in payload["status_message"]


@pytest.mark.asyncio
async def test_admin_account_overview_returns_combined_payload(client, monkeypatch):
    async def fake_get_account_overview(market=None):
        return AccountOverview(
            balance=AccountBalance(
                total_asset=369166161,
                total_asset_foreign=254597.35,
                cash=0,
                cash_foreign=0,
                raw_cash=0,
                raw_cash_foreign=0,
                effective_cash=368635000,
                effective_cash_foreign=254231.03,
                operating_cash=368635000,
                operating_cash_foreign=254231.03,
                cash_source="TOTAL_ASSET_PROXY",
                stock_value=531161,
                stock_value_foreign=366.32,
                total_pnl=-249.441,
                total_pnl_rate=-0.05,
                raw_total_pnl=7497.903,
                raw_total_pnl_rate=1.23,
                pnl_source="HOLDINGS_SUM",
                market=market or "NASDAQ",
                currency="KRW",
                exchange_rate_to_krw=1450.0,
                status_message="모의투자 조회할 내역(자료)이 없습니다.",
            ),
            holdings=[
                HoldingInfo(
                    symbol="NVDA",
                    name="엔비디아",
                    market=market or "NASDAQ",
                    currency="USD",
                    quantity=2,
                    avg_buy_price=182.785,
                    current_price=183.14,
                    pnl=0.71,
                    pnl_rate=0.19,
                    exchange_rate_to_krw=1450.0,
                )
            ],
            pending_orders=[
                PendingOrderInfo(
                    order_id="123456",
                    symbol="NVDA",
                    name="엔비디아",
                    market=market or "NASDAQ",
                    currency="USD",
                    side="BUY",
                    order_qty=2,
                    filled_qty=1,
                    remaining_qty=1,
                    order_price=183.0,
                    order_time="093001",
                    exchange_rate_to_krw=1450.0,
                )
            ],
        )

    monkeypatch.setattr(account_manager, "get_account_overview", fake_get_account_overview)

    response = await client.get("/api/v1/admin/account/overview?market=NASDAQ")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["balance"]["effective_cash"] == 368635000
    assert payload["balance"]["operating_cash"] == 368635000
    assert payload["balance"]["total_asset_foreign"] == 254597.35
    assert payload["balance"]["effective_cash_foreign"] == 254231.03
    assert payload["balance"]["stock_value_foreign"] == 366.32
    assert payload["balance"]["market"] == "NASDAQ"
    assert payload["holdings"][0]["symbol"] == "NVDA"
    assert payload["pending_orders"][0]["order_id"] == "123456"


@pytest.mark.asyncio
async def test_admin_account_overview_exposes_invalid_balance_state(client, monkeypatch):
    async def fake_get_account_overview(market=None):
        return AccountOverview(
            balance=AccountBalance(
                total_asset=0,
                cash=0,
                raw_cash=0,
                effective_cash=0,
                cash_source="BROKER",
                stock_value=0,
                total_pnl=0,
                total_pnl_rate=0,
                market=market or "KRX",
                currency="KRW",
                exchange_rate_to_krw=1.0,
                status_message="ERROR INVALID INPUT_FILED_SIZE",
                is_valid=False,
            ),
            holdings=[],
            pending_orders=[],
        )

    monkeypatch.setattr(account_manager, "get_account_overview", fake_get_account_overview)

    response = await client.get("/api/v1/admin/account/overview?market=KRX")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["balance"]["is_valid"] is False
    assert payload["holdings"] == []
    assert payload["pending_orders"] == []
