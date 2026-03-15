"""시장 스캐너 공통 인터페이스.

MarketScanner(주식)와 CryptoScanner(코인)가 동일한 Protocol을 따르도록 한다.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MarketScannerProtocol(Protocol):
    """시장 스캐너 인터페이스 — 스캔 결과 dict를 반환하는 계약."""

    async def scan(
        self,
        *,
        market: str = "",
        cycle_id: str | None = None,
        dynamic_limits: dict[str, Any] | None = None,
        account_snapshot: tuple | None = None,
    ) -> dict[str, Any]:
        """시장 스캔 실행 → 선별된 종목 + 시장 요약 반환.

        Returns:
            {
                "selected": list[dict],      # 선별 종목 리스트
                "market_summary": str,        # 시장 국면 요약
                "market_regime": str,         # BULL/BEAR/SIDEWAYS/THEME
                "available_cash": float,      # 매수 가능 금액
                "scan_source": str,           # 스캔 출처 (volume_rank, watchlist 등)
            }
        """
        ...

    def add_untradeable(self, symbol: str, market: str = "") -> None:
        """매매불가 종목을 런타임 블록리스트에 등록"""
        ...
