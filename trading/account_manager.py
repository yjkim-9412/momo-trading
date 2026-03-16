"""MCP/REST를 통한 잔고·보유·미체결 조회"""
from __future__ import annotations

import asyncio
import time

from loguru import logger
from sqlalchemy import func, select

from core.config import settings
from core.database import AsyncSessionLocal
from models.coin_broker_order import CoinBrokerOrder
from scheduler.market_calendar import market_calendar
from trading.mcp_client import mcp_client
from trading.market_profile import is_crypto_market, is_us_market, market_currency, normalize_market
from trading.models import AccountBalance, AccountOverview, HoldingInfo, PendingOrderInfo, coin_side_label

_INTRADAY_SNAPSHOT_TTL_SECONDS = 2.0


class AccountManager:
    """계좌 관리 (MCP/REST 통신)"""

    _CRYPTO_OPEN_ORDER_STATUSES = {"PENDING", "SUBMITTED", "OPEN", "PARTIAL"}

    def __init__(self):
        self._balance_cache: dict[str, AccountBalance] = {}
        self._holdings_cache: dict[str, list[HoldingInfo]] = {}
        self._pending_orders_cache: dict[str, list[PendingOrderInfo]] = {}
        self._snapshot_cached_at: dict[str, float] = {}
        self._snapshot_inflight: dict[str, asyncio.Task[tuple[AccountBalance, list[HoldingInfo]]]] = {}
        self._snapshot_lock = asyncio.Lock()

    def invalidate_cache(self) -> None:
        """체결 이후 캐시를 비운다."""
        self._balance_cache.clear()
        self._holdings_cache.clear()
        self._pending_orders_cache.clear()
        self._snapshot_cached_at.clear()

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

    def _calculate_holdings_stock_value(self, holdings: list[HoldingInfo] | None) -> float:
        """보유 종목 현재가 합계를 KRW로 계산한다."""
        if not holdings:
            return 0.0
        return sum(
            self._holding_amount_to_krw(holding, holding.current_price * holding.quantity)
            for holding in holdings
        )

    def _resolve_krx_cash_values(self, summary: dict) -> tuple[float, float, str]:
        """국내장 현금성 지표를 예수금/가용현금 기준으로 정리한다."""
        deposit_cash = self._to_float(
            self._first_value(summary, "dnca_tot_amt", "cash")
        )
        orderable_value = self._first_value(summary, "ord_psbl_amt")
        if orderable_value is not None:
            return deposit_cash, self._to_float(orderable_value), "BROKER_ORDERABLE"
        return deposit_cash, deposit_cash, "BROKER_DEPOSIT"

    def _log_krx_balance_mismatch(
        self,
        market: str,
        holdings: list[HoldingInfo] | None,
        *,
        summary_stock_value: float,
        total_asset: float,
        cash: float,
        effective_cash: float,
        total_pnl: float,
    ) -> None:
        """국내장 요약값과 보유종목 합산값이 어긋나면 경고 로그를 남긴다."""
        if not holdings:
            return

        holdings_stock_value = self._calculate_holdings_stock_value(holdings)
        if abs(summary_stock_value - holdings_stock_value) < 1:
            return

        logger.warning(
            "[{}] 국내 잔고 요약/보유평가 불일치: summary_stock_value={:,.0f}, holdings_stock_value={:,.0f}, total_asset={:,.0f}, deposit_cash={:,.0f}, effective_cash={:,.0f}, total_pnl={:,.0f}, holdings={}",
            market,
            summary_stock_value,
            holdings_stock_value,
            total_asset,
            cash,
            effective_cash,
            total_pnl,
            len(holdings),
        )

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
            if normalized_market == "KRX":
                cash, effective_cash, cash_source = self._resolve_krx_cash_values(summary)
                stock_value = self._to_float(
                    self._first_value(
                        summary,
                        "stock_value",
                        "scts_evlu_amt",
                        "evlu_amt_smtl_amt",
                    ),
                    default=self._calculate_holdings_stock_value(holdings),
                )
                total_asset = self._to_float(
                    self._first_value(
                        summary,
                        "total_asset",
                        "tot_evlu_amt",
                    ),
                    default=cash + stock_value,
                )
                total_pnl = self._to_float(
                    self._first_value(
                        summary,
                        "total_pnl",
                        "evlu_pfls_smtl_amt",
                    )
                )
                total_pnl_rate = self._to_float(
                    self._first_value(summary, "total_pnl_rate", "evlu_pfls_rt")
                )
                purchase_amount = self._to_float(self._first_value(summary, "pchs_amt_smtl_amt"))
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
                self._log_krx_balance_mismatch(
                    normalized_market,
                    holdings,
                    summary_stock_value=stock_value,
                    total_asset=total_asset,
                    cash=cash,
                    effective_cash=effective_cash,
                    total_pnl=total_pnl,
                )

                return AccountBalance(
                    total_asset=total_asset,
                    cash=cash,
                    stock_value=stock_value,
                    locked_krw=0.0,
                    total_pnl=total_pnl,
                    total_pnl_rate=total_pnl_rate,
                    raw_total_pnl=raw_total_pnl,
                    raw_total_pnl_rate=raw_total_pnl_rate,
                    pnl_source=pnl_source,
                    market=normalized_market,
                    currency="KRW",
                    exchange_rate_to_krw=1.0,
                    raw_cash=cash,
                    effective_cash=effective_cash,
                    cash_source=cash_source,
                    purchase_amount=purchase_amount,
                    status_message=str(data.get("msg1") or ""),
                    is_valid=not is_error_payload,
                )

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

            stock_value = self._calculate_holdings_stock_value(holdings)
            if stock_value <= 0:
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
                locked_krw=0.0,
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
        locked_krw = self._to_float(data.get("locked_krw", 0))
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
            locked_krw=locked_krw,
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
                self._first_value(item, "remaining_qty", "rmn_qty", "nccs_qty")
            )
            if remaining_qty <= 0:
                continue

            side_code = self._first_value(item, "side_code", "sll_buy_dvsn_cd", "side") or ""
            side = "매수" if side_code in ("02", "BUY", "매수") else "매도"
            orders.append(PendingOrderInfo(
                order_id=self._first_value(item, "order_id", "odno") or "",
                symbol=self._first_value(item, "symbol", "pdno", "ovrs_pdno") or "",
                name=self._first_value(item, "name", "prdt_name", "ovrs_item_name") or "",
                market=normalize_market(
                    self._first_value(item, "market", "ovrs_excg_cd") or market,
                    default=market,
                ),
                currency=self._first_value(item, "currency", "tr_crcy_cd", "crcy_cd") or market_currency(market),
                side=side,
                order_qty=self._to_int(self._first_value(item, "order_qty", "ft_ord_qty", "ord_qty")),
                filled_qty=self._to_int(
                    self._first_value(item, "filled_qty", "ft_ccld_qty", "tot_ccld_qty", "ccld_qty")
                ),
                remaining_qty=remaining_qty,
                order_price=self._to_float(
                    self._first_value(item, "order_price", "ft_ord_unpr3", "ord_unpr", "ovrs_ord_unpr")
                ),
                order_time=self._first_value(item, "order_time", "ord_tmd", "thco_ord_tmd") or "",
                exchange_rate_to_krw=self._to_float(item.get("exchange_rate_to_krw", data.get("exchange_rate_to_krw", 1.0)), 1.0),
                status=str(item.get("status") or ""),
                status_detail=str(item.get("status_detail") or ""),
                submitted_at=str(item.get("submitted_at") or "") or None,
                updated_at=str(item.get("updated_at") or "") or None,
            ))
        return orders

    async def _fetch_crypto_pending_orders(self, market_code: str) -> list[PendingOrderInfo]:
        """코인 미체결 주문을 coin_broker_orders 원장에서 조회한다."""
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(CoinBrokerOrder)
                    .where(CoinBrokerOrder.status.in_(self._CRYPTO_OPEN_ORDER_STATUSES))
                    .order_by(
                        func.coalesce(CoinBrokerOrder.updated_at, CoinBrokerOrder.created_at).desc()
                    )
                )
            ).scalars().all()

        pending_orders: list[PendingOrderInfo] = []
        for row in rows:
            remaining_qty = max(
                self._to_float(row.quantity) - self._to_float(row.filled_quantity),
                0.0,
            )
            if remaining_qty <= 0:
                continue

            submitted_at = row.submitted_at or row.created_at
            updated_at = row.updated_at or row.created_at
            pending_orders.append(
                PendingOrderInfo(
                    order_id=row.bithumb_order_id,
                    symbol=row.symbol,
                    name=row.coin_name or row.symbol,
                    market=market_code,
                    currency=row.currency or "KRW",
                    side=coin_side_label(row.side),
                    order_qty=self._to_float(row.quantity),
                    filled_qty=self._to_float(row.filled_quantity),
                    remaining_qty=remaining_qty,
                    order_price=self._to_float(row.requested_price),
                    order_time=submitted_at.strftime("%H%M%S") if submitted_at else "",
                    exchange_rate_to_krw=1.0,
                    status=str(row.status or ""),
                    status_detail=str(row.status_detail or ""),
                    submitted_at=submitted_at.isoformat() if submitted_at else None,
                    updated_at=updated_at.isoformat() if updated_at else None,
                )
            )

        self._pending_orders_cache[market_code] = pending_orders
        return pending_orders

    def _has_fresh_snapshot_cache(self, market: str, now: float | None = None) -> bool:
        cached_balance = self._balance_cache.get(market)
        if cached_balance is None or not cached_balance.is_valid:
            return False
        if market not in self._holdings_cache:
            return False
        cached_at = self._snapshot_cached_at.get(market)
        if cached_at is None:
            return False
        current = time.monotonic() if now is None else now
        return (current - cached_at) <= _INTRADAY_SNAPSHOT_TTL_SECONDS

    async def _fetch_account_snapshot(self, market_code: str) -> tuple[AccountBalance, list[HoldingInfo]]:
        if is_crypto_market(market_code):
            return await self._fetch_crypto_snapshot(market_code)

        response = await mcp_client.get_account_balance(market=market_code)
        if not response.success:
            logger.warning("계좌 조회 실패: {}", response.error)
            error_message = str((response.data or {}).get("msg1") or response.error or "")
            balance = self._empty_balance(market_code, status_message=error_message)
            return balance, []

        data = response.data or {}
        logger.debug("계좌 응답: {}", str(data)[:500])

        holdings = self._parse_holdings(data, market=market_code)
        balance = self._parse_balance(data, holdings=holdings, market=market_code)
        if balance.is_valid:
            self._balance_cache[market_code] = balance
            self._holdings_cache[market_code] = holdings
            self._snapshot_cached_at[market_code] = time.monotonic()
        return balance, holdings

    async def _fetch_crypto_snapshot(self, market_code: str) -> tuple[AccountBalance, list[HoldingInfo]]:
        """크립토 계좌 스냅샷: bithumb_client 잔고 1회 조회로 잔고 + 보유 모두 추출."""
        from trading.bithumb_client import bithumb_client

        balance_resp = await bithumb_client.get_account_balance(market=market_code)
        if not balance_resp.success:
            logger.warning("[{}] 크립토 잔고 조회 실패: {}", market_code, balance_resp.error)
            return self._empty_balance(market_code, status_message=balance_resp.error or ""), []

        bal_data = balance_resp.data or {}
        logger.debug("[{}] 크립토 잔고 응답: {}", market_code, str(bal_data)[:500])

        # holdings_summary는 get_account_balance 응답에 이미 포함되어 있다.
        # 별도 get_holdings() 호출을 제거하여 동일 API 이중 호출을 방지한다.
        raw_summary = bal_data.get("holdings_summary") or []
        holdings: list[HoldingInfo] = []
        for item in raw_summary:
            sym = item.get("currency", "")
            qty = self._to_float(item.get("balance", 0)) + self._to_float(item.get("locked", 0))
            if qty <= 0:
                continue
            avg = self._to_float(item.get("avg_buy_price", 0))
            cur_price = self._to_float(item.get("current_price", 0))
            pnl = (cur_price - avg) * qty if cur_price > 0 and avg > 0 else 0.0
            pnl_rate = ((cur_price / avg) - 1) * 100 if avg > 0 and cur_price > 0 else 0.0
            holdings.append(HoldingInfo(
                symbol=sym,
                name=sym,
                market=market_code,
                currency="KRW",
                quantity=qty,
                avg_buy_price=avg,
                current_price=cur_price,
                pnl=round(pnl, 2),
                pnl_rate=round(pnl_rate, 2),
                exchange_rate_to_krw=1.0,
            ))

        total_asset = self._to_float(bal_data.get("total_asset", 0))
        cash = self._to_float(bal_data.get("cash", 0))
        stock_value = self._to_float(bal_data.get("stock_value", 0))

        balance = AccountBalance(
            total_asset=total_asset,
            cash=cash,
            stock_value=stock_value,
            total_pnl=self._to_float(bal_data.get("total_pnl", 0)),
            total_pnl_rate=self._to_float(bal_data.get("total_pnl_rate", 0)),
            raw_total_pnl=self._to_float(bal_data.get("total_pnl", 0)),
            raw_total_pnl_rate=self._to_float(bal_data.get("total_pnl_rate", 0)),
            pnl_source="BROKER_SUMMARY",
            market=market_code,
            currency="KRW",
            exchange_rate_to_krw=1.0,
            raw_cash=cash,
            effective_cash=cash,
            cash_source="BROKER",
            status_message="",
            is_valid=True,
        )

        self._balance_cache[market_code] = balance
        self._holdings_cache[market_code] = holdings
        self._snapshot_cached_at[market_code] = time.monotonic()
        return balance, holdings

    async def get_account_snapshot(self, market: str | None = None) -> tuple[AccountBalance, list[HoldingInfo]]:
        """잔고와 보유종목을 단일 호출로 조회한다."""
        market_code = normalize_market(market or settings.primary_market_code)
        if not market_calendar.is_trading_hours(market_code):
            if market_code in self._balance_cache and market_code in self._holdings_cache:
                logger.debug("장외 시간 → 계좌 스냅샷 캐시 반환")
                return self._balance_cache[market_code], self._holdings_cache[market_code]

        now = time.monotonic()
        if self._has_fresh_snapshot_cache(market_code, now):
            logger.debug("[{}] 장중 계좌 스냅샷 TTL 캐시 반환", market_code)
            return self._balance_cache[market_code], self._holdings_cache[market_code]

        async with self._snapshot_lock:
            if self._has_fresh_snapshot_cache(market_code, now):
                logger.debug("[{}] 장중 계좌 스냅샷 TTL 캐시 반환", market_code)
                return self._balance_cache[market_code], self._holdings_cache[market_code]
            inflight = self._snapshot_inflight.get(market_code)
            if inflight is None:
                inflight = asyncio.create_task(self._fetch_account_snapshot(market_code))
                self._snapshot_inflight[market_code] = inflight
            else:
                logger.debug("[{}] 계좌 스냅샷 진행 중 요청 합류", market_code)

        try:
            return await inflight
        finally:
            async with self._snapshot_lock:
                if self._snapshot_inflight.get(market_code) is inflight and inflight.done():
                    self._snapshot_inflight.pop(market_code, None)

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
        if is_crypto_market(market_code):
            return await self._fetch_crypto_pending_orders(market_code)

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
