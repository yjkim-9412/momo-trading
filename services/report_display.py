"""일일 리포트용 통화/금액 표시 헬퍼"""
from __future__ import annotations

from dataclasses import dataclass

from models.trade_result import TradeResult
from trading.models import AccountBalance, HoldingInfo


@dataclass(frozen=True)
class ReportBalanceMetrics:
    """리포트 표시 기준 계좌 메트릭"""

    report_currency: str
    total_asset: float
    cash: float
    stock_value: float
    total_pnl: float
    total_pnl_rate: float
    unrealized_pnl: float
    cash_ratio: float

    @property
    def total_asset_text(self) -> str:
        return format_report_amount(self.total_asset, self.report_currency)

    @property
    def cash_text(self) -> str:
        return format_report_amount(self.cash, self.report_currency)

    @property
    def stock_value_text(self) -> str:
        return format_report_amount(self.stock_value, self.report_currency)

    @property
    def total_pnl_text(self) -> str:
        return format_report_amount(self.total_pnl, self.report_currency, signed=True)

    @property
    def unrealized_pnl_text(self) -> str:
        return format_report_amount(self.unrealized_pnl, self.report_currency, signed=True)


def report_currency_for_scope(scope: str) -> str:
    """리포트 scope별 기준 통화"""
    return "USD" if str(scope or "").upper() == "US" else "KRW"


def build_report_balance_metrics(
    scope: str,
    balance: AccountBalance | None,
    holdings: list[HoldingInfo] | None,
) -> ReportBalanceMetrics:
    """시장별 리포트 표시 기준 계좌 메트릭 계산"""
    report_currency = report_currency_for_scope(scope)
    scoped_holdings = list(holdings or [])

    if report_currency == "USD":
        total_asset = float(getattr(balance, "total_asset_foreign", 0.0) or 0.0)
        cash = float(
            getattr(balance, "effective_cash_foreign", 0.0)
            or getattr(balance, "cash_foreign", 0.0)
            or 0.0
        )
        stock_value = float(getattr(balance, "stock_value_foreign", 0.0) or 0.0)
        total_pnl = float(getattr(balance, "raw_total_pnl", 0.0) or 0.0)
        total_pnl_rate = float(getattr(balance, "raw_total_pnl_rate", 0.0) or 0.0)
        unrealized_pnl = sum(float(getattr(holding, "pnl", 0.0) or 0.0) for holding in scoped_holdings)
        if total_asset <= 0 and (cash > 0 or stock_value > 0):
            total_asset = cash + stock_value
    else:
        total_asset = float(getattr(balance, "total_asset", 0.0) or 0.0)
        cash = float(
            getattr(balance, "effective_cash", 0.0)
            or getattr(balance, "cash", 0.0)
            or 0.0
        )
        stock_value = float(getattr(balance, "stock_value", 0.0) or 0.0)
        total_pnl = float(getattr(balance, "total_pnl", 0.0) or 0.0)
        total_pnl_rate = float(getattr(balance, "total_pnl_rate", 0.0) or 0.0)
        unrealized_pnl = sum(float(getattr(holding, "pnl", 0.0) or 0.0) for holding in scoped_holdings)

    cash_ratio = (cash / total_asset) * 100 if total_asset > 0 else 0.0
    return ReportBalanceMetrics(
        report_currency=report_currency,
        total_asset=total_asset,
        cash=cash,
        stock_value=stock_value,
        total_pnl=total_pnl,
        total_pnl_rate=total_pnl_rate,
        unrealized_pnl=unrealized_pnl,
        cash_ratio=cash_ratio,
    )


def summarize_closed_trade_stats(trades: list[TradeResult], scope: str) -> dict[str, float]:
    """청산 거래 기준 승률/손익 요약"""
    closed_trades = [trade for trade in trades if getattr(trade, "exit_at", None) is not None]
    total_trades = len(closed_trades)
    wins = sum(1 for trade in closed_trades if getattr(trade, "is_win", False))
    losses = total_trades - wins
    win_rate = (wins / total_trades) if total_trades > 0 else 0.0
    total_pnl = sum_trade_pnl(closed_trades, scope)
    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }


def sum_trade_pnl(trades: list[TradeResult], scope: str) -> float:
    """리포트 기준 통화로 거래 손익 합계 계산"""
    use_raw_pnl = report_currency_for_scope(scope) == "USD"
    field_name = "raw_pnl" if use_raw_pnl else "pnl"
    return sum(float(getattr(trade, field_name, 0.0) or 0.0) for trade in trades)


def format_trade_price(amount: float | None, currency: str) -> str:
    """체결가를 시장 통화 기준 문자열로 포맷"""
    if amount is None:
        return "?"
    value = float(amount)
    if value <= 0:
        return "?"
    return format_report_amount(value, currency)


def format_trade_pnl(trade: TradeResult, scope: str) -> str:
    """거래 손익 문자열 포맷"""
    report_currency = report_currency_for_scope(scope)
    amount = getattr(trade, "raw_pnl" if report_currency == "USD" else "pnl", 0.0)
    amount_text = format_report_amount(amount, report_currency, signed=True)
    return_pct = getattr(trade, "return_pct", None)
    if return_pct is None:
        return amount_text
    return f"{amount_text} ({float(return_pct):+.1f}%)"


def format_report_amount(amount: float | None, currency: str, *, signed: bool = False) -> str:
    """리포트용 금액 문자열 포맷"""
    if amount is None:
        return "조회값 없음"

    value = float(amount)
    prefix = ""
    if signed and value >= 0:
        prefix = "+"

    if str(currency).upper() == "USD":
        return f"{prefix}{value:,.2f} USD"
    return f"{prefix}{value:,.0f}원"
