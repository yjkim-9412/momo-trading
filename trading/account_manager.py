"""MCP/REST를 통한 잔고·보유·미체결 조회"""
from __future__ import annotations

from loguru import logger

from core.config import settings
from scheduler.market_calendar import market_calendar
from trading.mcp_client import mcp_client
from trading.market_profile import is_us_market, market_currency, normalize_market
from trading.models import AccountBalance, AccountOverview, HoldingInfo, PendingOrderInfo


class AccountManager:
    """계좌 관리 (MCP/REST 통신)"""

    def __init__(self):
        self._balance_cache: dict[str, AccountBalance] = {}
        self._holdings_cache: dict[str, list[HoldingInfo]] = {}
        self._pending_orders_cache: dict[str, list[PendingOrderInfo]] = {}

    def invalidate_cache(self) -> None:
        """체결 이후 캐시를 비운다."""
        self._balance_cache.clear()
        self._holdings_cache.clear()
        self._pending_orders_cache.clear()

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if value in (None, ""):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            if value in (None, ""):
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _first_value(source: dict, *keys: str):
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
        return None

    def _empty_balance(self, market: str, *, status_message: str = "") -> AccountBalance:
        normalized_market = normalize_market(market)
        return AccountBalance(
            total_asset=0,
            cash=0,
            stock_value=0,
            total_pnl=0,
            total_pnl_rate=0,
            raw_total_pnl=0,
            raw_total_pnl_rate=0,
            pnl_source="BROKER_SUMMARY",
            market=normalized_market,
            currency="KRW",
            exchange_rate_to_krw=1.0,
            raw_cash=0,
            effective_cash=0,
            cash_source="BROKER",
            status_message=status_message,
            is_valid=False,
        )

    @staticmethod
    def _holding_amount_to_krw(holding: HoldingInfo, amount: float) -> float:
        """보유 종목 금액을 KRW로 환산한다."""
        if holding.currency == "KRW":
            return amount
        exchange_rate = holding.exchange_rate_to_krw if holding.exchange_rate_to_krw > 0 else 1.0
        return amount * exchange_rate

    def _resolve_total_pnl(
        self,
        market: str,
        total_pnl: float,
        total_pnl_rate: float,
        holdings: list[HoldingInfo] | None,
    ) -> tuple[float, float, float, float, str]:
        """시장별 손익 표시 정책을 적용한다."""
        raw_total_pnl = total_pnl
        raw_total_pnl_rate = total_pnl_rate

        if not settings.is_paper_trading or not is_us_market(market):
            return total_pnl, total_pnl_rate, raw_total_pnl, raw_total_pnl_rate, "BROKER_SUMMARY"

        if not holdings:
            return 0.0, 0.0, raw_total_pnl, raw_total_pnl_rate, "HOLDINGS_SUM"

        normalized_total_pnl = sum(
            self._holding_amount_to_krw(holding, holding.pnl)
            for holding in holdings
        )
        purchase_amount = sum(
            self._holding_amount_to_krw(holding, holding.avg_buy_price * holding.quantity)
            for holding in holdings
        )
        normalized_total_pnl_rate = (normalized_total_pnl / purchase_amount) * 100 if purchase_amount > 0 else 0.0

        if abs(raw_total_pnl - normalized_total_pnl) >= 1:
            logger.info(
                "[{}] 해외 모의투자 손익 정규화: normalized={:,.3f}, raw={:,.3f}, holdings={}",
                market,
                normalized_total_pnl,
                raw_total_pnl,
                len(holdings),
            )

        return (
            normalized_total_pnl,
            normalized_total_pnl_rate,
            raw_total_pnl,
            raw_total_pnl_rate,
            "HOLDINGS_SUM",
        )

    def _resolve_effective_cash(
        self,
        market: str,
        cash: float,
        total_asset: float,
        stock_value: float,
    ) -> tuple[float, str]:
        """거래 판단에 사용할 현금을 시장별 정책으로 정규화한다."""
        if not settings.is_paper_trading or not is_us_market(market):
            return cash, "BROKER"

        if cash > 0:
            return cash, "BROKER"

        proxy_cash = max(total_asset - stock_value, 0.0)
        if proxy_cash > 0:
            logger.info(
                "[{}] 해외 모의투자 현금 프록시 사용: raw_cash={:,.0f}, effective_cash={:,.0f}",
                market,
                cash,
                proxy_cash,
            )
            return proxy_cash, "TOTAL_ASSET_PROXY"

        return cash, "BROKER"

    def _parse_balance(
        self,
        data: dict,
        holdings: list[HoldingInfo] | None = None,
        market: str = "KRX",
    ) -> AccountBalance:
        """원본 응답을 KRW 기준 AccountBalance로 정규화한다."""
        normalized_market = normalize_market(market)
        output2 = data.get("output2", [])
        if isinstance(output2, dict):
            output2 = [output2]

        if output2 and isinstance(output2, list):
            summary = output2[0]
            is_error_payload = str(data.get("rt_cd") or "") not in ("", "0")
            cash = self._to_float(
                self._first_value(
                    summary,
                    "cash",
                    "dnca_tot_amt",
                    "ord_psbl_amt",
                    "ovrs_ord_psbl_amt",
                    "frcr_dncl_amt_2",
                    "frcr_ord_psbl_amt1",
                )
            )

            if holdings:
                stock_value = sum(
                    (holding.current_price * holding.exchange_rate_to_krw) * holding.quantity
                    if holding.currency != "KRW" else holding.current_price * holding.quantity
                    for holding in holdings
                )
            else:
                stock_value = self._to_float(
                    self._first_value(
                        summary,
                        "stock_value",
                        "scts_evlu_amt",
                        "ovrs_stck_evlu_amt",
                        "frcr_evlu_amt2",
                    )
                )

            total_asset = self._to_float(
                self._first_value(
                    summary,
                    "total_asset",
                    "tot_evlu_amt",
                    "tot_asst_amt",
                ),
                default=cash + stock_value,
            )
            total_pnl = self._to_float(
                self._first_value(
                    summary,
                    "total_pnl",
                    "evlu_pfls_smtl_amt",
                    "tot_evlu_pfls_amt",
                    "frcr_evlu_pfls_amt",
                )
            )
            total_pnl_rate = self._to_float(
                self._first_value(summary, "total_pnl_rate", "evlu_pfls_rt", "tot_pftrt")
            )
            purchase_amount = self._to_float(
                self._first_value(summary, "pchs_amt_smtl_amt", "frcr_pchs_amt1")
            )
            if total_pnl_rate == 0.0 and purchase_amount > 0:
                total_pnl_rate = (total_pnl / purchase_amount) * 100
            (
                total_pnl,
                total_pnl_rate,
                raw_total_pnl,
                raw_total_pnl_rate,
                pnl_source,
            ) = self._resolve_total_pnl(
                normalized_market,
                total_pnl,
                total_pnl_rate,
                holdings,
            )

            exchange_rate = self._to_float(
                self._first_value(summary, "exchange_rate_to_krw", "bass_exrt", "frst_bltn_exrt"),
                1.0,
            )
            effective_cash, cash_source = self._resolve_effective_cash(
                normalized_market,
                cash,
                total_asset,
                stock_value,
            )

            return AccountBalance(
                total_asset=total_asset,
                cash=cash,
                stock_value=stock_value,
                total_pnl=total_pnl,
                total_pnl_rate=total_pnl_rate,
                raw_total_pnl=raw_total_pnl,
                raw_total_pnl_rate=raw_total_pnl_rate,
                pnl_source=pnl_source,
                market=normalized_market,
                currency="KRW",
                exchange_rate_to_krw=exchange_rate,
                raw_cash=cash,
                effective_cash=effective_cash,
                cash_source=cash_source,
                status_message=str(data.get("msg1") or ""),
                is_valid=not is_error_payload,
            )

        cash = self._to_float(data.get("cash", 0))
        total_asset = self._to_float(data.get("total_asset", 0))
        stock_value = self._to_float(data.get("stock_value", 0))
        effective_cash, cash_source = self._resolve_effective_cash(
            normalized_market,
            cash,
            total_asset,
            stock_value,
        )
        total_pnl = self._to_float(data.get("total_pnl", 0))
        total_pnl_rate = self._to_float(data.get("total_pnl_rate", 0))
        (
            total_pnl,
            total_pnl_rate,
            raw_total_pnl,
            raw_total_pnl_rate,
            pnl_source,
        ) = self._resolve_total_pnl(
            normalized_market,
            total_pnl,
            total_pnl_rate,
            holdings,
        )
        is_error_payload = str(data.get("rt_cd") or "") not in ("", "0")
        return AccountBalance(
            total_asset=total_asset,
            cash=cash,
            stock_value=stock_value,
            total_pnl=total_pnl,
            total_pnl_rate=total_pnl_rate,
            raw_total_pnl=raw_total_pnl,
            raw_total_pnl_rate=raw_total_pnl_rate,
            pnl_source=pnl_source,
            market=normalized_market,
            currency=data.get("currency", "KRW"),
            exchange_rate_to_krw=self._to_float(data.get("exchange_rate_to_krw", 1.0), 1.0),
            raw_cash=cash,
            effective_cash=effective_cash,
            cash_source=cash_source,
            status_message=str(data.get("msg1") or ""),
            is_valid=bool(data) and not is_error_payload,
        )

    def _parse_holdings(self, data: dict, market: str = "KRX") -> list[HoldingInfo]:
        """원본 응답을 HoldingInfo 리스트로 정규화한다."""
        holdings: list[HoldingInfo] = []
        default_market = normalize_market(market)
        default_currency = market_currency(default_market)
        default_exchange_rate = self._to_float(data.get("exchange_rate_to_krw", 1.0), 1.0)

        output1 = data.get("output1", [])
        if isinstance(output1, dict):
            output1 = [output1]

        if output1 and isinstance(output1, list):
            for item in output1:
                qty = self._to_int(
                    self._first_value(
                        item,
                        "hldg_qty",
                        "ovrs_cblc_qty",
                        "cblc_qty13",
                        "quantity",
                        "qty",
                    )
                )
                if qty <= 0:
                    continue

                holding_market = normalize_market(
                    self._first_value(item, "market", "ovrs_excg_cd", "excg_dvsn_cd") or default_market,
                    default=default_market,
                )
                currency = self._first_value(item, "currency", "tr_crcy_cd", "crcy_cd") or market_currency(holding_market)
                exchange_rate = self._to_float(
                    self._first_value(item, "exchange_rate_to_krw", "bass_exrt", "frst_bltn_exrt"),
                    default_exchange_rate if currency != "KRW" else 1.0,
                )

                holdings.append(HoldingInfo(
                    symbol=self._first_value(item, "pdno", "ovrs_pdno", "symbol", "item_cd") or "",
                    name=self._first_value(item, "prdt_name", "ovrs_item_name", "item_name", "name") or "",
                    market=holding_market,
                    currency=currency,
                    quantity=qty,
                    avg_buy_price=self._to_float(
                        self._first_value(item, "pchs_avg_pric", "avg_buy_price", "avg_unpr", "frcr_pchs_unpr")
                    ),
                    current_price=self._to_float(
                        self._first_value(item, "prpr", "ovrs_now_pric1", "now_pric2", "current_price", "last")
                    ),
                    pnl=self._to_float(
                        self._first_value(item, "evlu_pfls_amt", "frcr_evlu_pfls_amt", "pnl")
                    ),
                    pnl_rate=self._to_float(
                        self._first_value(item, "evlu_pfls_rt", "evlu_pfls_rt1", "pnl_rate")
                    ),
                    exchange_rate_to_krw=exchange_rate,
                ))
            return holdings

        for item in data.get("holdings", []):
            holdings.append(HoldingInfo(
                symbol=item.get("symbol", ""),
                name=item.get("name", ""),
                market=normalize_market(item.get("market") or default_market),
                currency=item.get("currency", default_currency),
                quantity=self._to_int(item.get("quantity", 0)),
                avg_buy_price=self._to_float(item.get("avg_buy_price", 0)),
                current_price=self._to_float(item.get("current_price", 0)),
                pnl=self._to_float(item.get("pnl", 0)),
                pnl_rate=self._to_float(item.get("pnl_rate", 0)),
                exchange_rate_to_krw=self._to_float(item.get("exchange_rate_to_krw", default_exchange_rate), default_exchange_rate),
            ))
        return holdings

    def _parse_pending_orders(self, data: dict, market: str = "KRX") -> list[PendingOrderInfo]:
        """원본 응답을 PendingOrderInfo 리스트로 정규화한다."""
        orders: list[PendingOrderInfo] = []
        output = data.get("output", [])
        if isinstance(output, dict):
            output = [output]
        if not output or not isinstance(output, list):
            return orders

        for item in output:
            remaining_qty = self._to_int(
                self._first_value(item, "rmn_qty", "nccs_qty", "remaining_qty")
            )
            if remaining_qty <= 0:
                continue

            side_code = self._first_value(item, "sll_buy_dvsn_cd", "side_code", "side") or ""
            side = "매수" if side_code in ("02", "BUY", "매수") else "매도"
            orders.append(PendingOrderInfo(
                order_id=self._first_value(item, "odno", "order_id") or "",
                symbol=self._first_value(item, "pdno", "ovrs_pdno", "symbol") or "",
                name=self._first_value(item, "prdt_name", "ovrs_item_name", "name") or "",
                market=normalize_market(
                    self._first_value(item, "market", "ovrs_excg_cd") or market,
                    default=market,
                ),
                currency=self._first_value(item, "currency", "tr_crcy_cd", "crcy_cd") or market_currency(market),
                side=side,
                order_qty=self._to_int(self._first_value(item, "ord_qty", "order_qty")),
                filled_qty=self._to_int(self._first_value(item, "tot_ccld_qty", "filled_qty", "ccld_qty")),
                remaining_qty=remaining_qty,
                order_price=self._to_float(self._first_value(item, "ord_unpr", "ovrs_ord_unpr", "order_price")),
                order_time=self._first_value(item, "ord_tmd", "order_time") or "",
                exchange_rate_to_krw=self._to_float(item.get("exchange_rate_to_krw", data.get("exchange_rate_to_krw", 1.0)), 1.0),
            ))
        return orders

    async def get_account_snapshot(self, market: str | None = None) -> tuple[AccountBalance, list[HoldingInfo]]:
        """잔고와 보유종목을 단일 호출로 조회한다."""
        market_code = normalize_market(market or settings.primary_market_code)
        if not market_calendar.is_trading_hours(market_code):
            if market_code in self._balance_cache and market_code in self._holdings_cache:
                logger.debug("장외 시간 → 계좌 스냅샷 캐시 반환")
                return self._balance_cache[market_code], self._holdings_cache[market_code]

        response = await mcp_client.get_account_balance(market=market_code)
        if not response.success:
            logger.warning("계좌 조회 실패: {}", response.error)
            error_message = str((response.data or {}).get("msg1") or response.error or "")
            balance = self._empty_balance(market_code, status_message=error_message)
            holdings: list[HoldingInfo] = []
            return balance, holdings

        data = response.data or {}
        logger.debug("계좌 응답: {}", str(data)[:500])

        holdings = self._parse_holdings(data, market=market_code)
        balance = self._parse_balance(data, holdings=holdings, market=market_code)
        self._balance_cache[market_code] = balance
        self._holdings_cache[market_code] = holdings
        return balance, holdings

    async def get_balance(self, market: str | None = None) -> AccountBalance:
        """계좌 잔고 조회"""
        balance, _ = await self.get_account_snapshot(market=market)
        return balance

    async def get_holdings(self, market: str | None = None) -> list[HoldingInfo]:
        """보유 종목 목록 조회"""
        _, holdings = await self.get_account_snapshot(market=market)
        return holdings

    async def get_pending_orders(self, market: str | None = None) -> list[PendingOrderInfo]:
        """미체결 주문 목록 조회"""
        market_code = normalize_market(market or settings.primary_market_code)
        if not market_calendar.is_trading_hours(market_code):
            if market_code in self._pending_orders_cache:
                logger.debug("장외 시간 → 미체결 주문 캐시 반환")
                return self._pending_orders_cache[market_code]

        response = await mcp_client.get_order_list(market=market_code)
        if not response.success:
            logger.warning("미체결 주문 조회 실패: {}", response.error)
            return self._pending_orders_cache.get(market_code, [])

        data = response.data or {}
        orders = self._parse_pending_orders(data, market=market_code)
        self._pending_orders_cache[market_code] = orders
        return orders

    async def get_account_overview(self, market: str | None = None) -> AccountOverview:
        """관리자 대시보드용 계좌 overview를 직렬 호출로 조회한다."""
        market_code = normalize_market(market or settings.primary_market_code)
        balance, holdings = await self.get_account_snapshot(market=market_code)
        pending_orders = await self.get_pending_orders(market=market_code)
        return AccountOverview(
            balance=balance,
            holdings=holdings,
            pending_orders=pending_orders,
        )

    async def get_available_cash(self, market: str | None = None) -> float:
        """투자 가용 현금 조회"""
        balance = await self.get_balance(market=market)
        return balance.effective_cash


account_manager = AccountManager()
