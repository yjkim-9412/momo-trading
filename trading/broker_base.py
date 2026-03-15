"""브로커 클라이언트 공통 인터페이스.

KIS, Bithumb 등 브로커 구현체가 따르는 Protocol 계약을 정의한다.
기존 `analysis/llm/base.py`의 LLMProviderProtocol 패턴을 따른다.
"""
from __future__ import annotations

from typing import Any, Callable, Coroutine, Protocol, runtime_checkable

from trading.models import MCPResponse


@runtime_checkable
class BrokerClient(Protocol):
    """브로커 클라이언트 인터페이스 — 시세 조회·주문·잔고 조회 계약.

    KIS MCPClient와 BithumbClient가 이 Protocol을 만족한다.
    """

    async def get_current_price(self, symbol: str, market: str = "") -> MCPResponse:
        """종목 현재가 조회"""
        ...

    async def get_daily_price(
        self,
        symbol: str,
        market: str = "",
        *,
        period: str = "D",
        count: int = 60,
    ) -> MCPResponse:
        """일봉(캔들) 데이터 조회"""
        ...

    async def get_minute_price(
        self,
        symbol: str,
        market: str = "",
        *,
        interval: int = 5,
        count: int = 60,
    ) -> MCPResponse:
        """분봉(캔들) 데이터 조회"""
        ...

    async def get_account_balance(self, market: str = "") -> MCPResponse:
        """계좌 잔고 조회"""
        ...

    async def get_holdings(self, market: str = "") -> MCPResponse:
        """보유 종목(포지션) 목록 조회"""
        ...

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None = None,
        market: str = "",
    ) -> MCPResponse:
        """주문 실행"""
        ...

    async def cancel_order(self, order_id: str, market: str = "", **kwargs: Any) -> MCPResponse:
        """주문 취소"""
        ...

    async def get_orderable_amount(
        self,
        symbol: str,
        price: float,
        market: str = "",
    ) -> MCPResponse:
        """종목별 주문 가능 금액/수량 조회"""
        ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """시장 데이터 제공자 — 스캐닝용 벌크 데이터 인터페이스."""

    async def get_market_overview(self, market: str = "") -> MCPResponse:
        """전체 시장 종목 요약 (거래량·등락률 등)"""
        ...

    async def get_volume_rank(self, market: str = "", **kwargs: Any) -> MCPResponse:
        """거래량 상위 종목"""
        ...

    async def get_surge_data(self, market: str = "", **kwargs: Any) -> MCPResponse:
        """급등/급락 종목 데이터"""
        ...


@runtime_checkable
class RealtimeProvider(Protocol):
    """실시간 시세 제공자 — WebSocket 연결 인터페이스."""

    async def connect(self) -> None:
        """WebSocket 연결"""
        ...

    async def disconnect(self) -> None:
        """WebSocket 종료"""
        ...

    async def subscribe(self, symbols: list[str], market: str = "") -> None:
        """종목 실시간 구독"""
        ...

    async def unsubscribe(self, symbols: list[str], market: str = "") -> None:
        """종목 구독 해제"""
        ...

    def set_callback(
        self,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """시세 수신 콜백 등록"""
        ...
