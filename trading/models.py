"""거래 관련 Pydantic 모델 (API 통신용, DB 모델 아님)"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from trading.enums import Market, OrderSide, OrderType


class KISTokenInfo(BaseModel):
    """KIS API 토큰 정보"""
    access_token: str
    token_type: str
    expires_at: datetime


class MCPRequest(BaseModel):
    """MCP 서버 요청"""
    tool_name: str
    arguments: dict


class MCPResponse(BaseModel):
    """MCP 서버 응답"""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class CurrentPrice(BaseModel):
    """현재가 정보"""
    symbol: str
    market: Market
    currency: str = "KRW"
    price: float
    price_krw: float = 0.0
    exchange_rate_to_krw: float = 1.0
    change: float
    change_rate: float
    volume: int
    session: str = ""
    timestamp: datetime


class OrderRequest(BaseModel):
    """주문 요청"""
    symbol: str
    market: Market
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Optional[float] = None  # 시장가 주문 시 None
    currency: str = "KRW"
    exchange_rate_to_krw: float = 1.0


class OrderResult(BaseModel):
    """주문 실행 결과"""
    success: bool
    order_id: Optional[str] = None
    message: str
    filled_quantity: int = 0
    filled_price: float = 0.0
    currency: str = "KRW"
    filled_price_krw: float = 0.0


class AccountBalance(BaseModel):
    """계좌 잔고"""
    total_asset: float
    cash: float
    stock_value: float
    total_pnl: float
    total_pnl_rate: float
    raw_total_pnl: float = 0.0
    raw_total_pnl_rate: float = 0.0
    pnl_source: str = "BROKER_SUMMARY"
    market: str = "KRX"
    currency: str = "KRW"
    exchange_rate_to_krw: float = 1.0
    raw_cash: float = 0.0
    effective_cash: float = 0.0
    cash_source: str = "BROKER"
    status_message: str = ""
    is_valid: bool = True  # False이면 조회 실패 상태


class HoldingInfo(BaseModel):
    """보유 종목 정보"""
    symbol: str
    name: str
    market: str = "KRX"
    currency: str = "KRW"
    quantity: int
    avg_buy_price: float
    current_price: float
    pnl: float
    pnl_rate: float
    exchange_rate_to_krw: float = 1.0


class PendingOrderInfo(BaseModel):
    """미체결 주문 정보"""
    order_id: str           # odno (주문번호)
    symbol: str             # pdno (종목코드)
    name: str               # prdt_name (종목명)
    market: str = "KRX"
    currency: str = "KRW"
    side: str               # 매수/매도
    order_qty: int          # 주문수량
    filled_qty: int         # 체결수량
    remaining_qty: int      # 미체결수량
    order_price: float      # 주문단가
    order_time: str         # 주문시각
    exchange_rate_to_krw: float = 1.0


class AccountOverview(BaseModel):
    """관리자 계좌 overview 응답"""
    balance: AccountBalance
    holdings: list[HoldingInfo]
    pending_orders: list[PendingOrderInfo]
