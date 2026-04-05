"""20/60일선 눌림형 전략."""
from __future__ import annotations

from strategy.signal import TradeSignal
from trading.enums import SignalAction, SignalUrgency


class RoadmapPullbackStrategy:
    """20/60일선 눌림형 전략."""

    strategy_type = "ROADMAP_PULLBACK"

    def __init__(self, min_confidence: float = 0.55):
        self.min_confidence = min_confidence

    async def evaluate(self, analysis: dict, market_regime: str = "") -> TradeSignal | None:
        """로드맵 metadata 기준으로 BUY 시그널을 생성한다."""
        recommendation = str(analysis.get("recommendation") or "HOLD").upper()
        confidence = float(analysis.get("confidence") or 0.0)
        if recommendation != "BUY" or confidence < self.min_confidence:
            return None

        current_price = float(analysis.get("current_price") or 0.0)
        roadmap_stage = str(analysis.get("roadmap_stage") or "")
        invalid_price = float(analysis.get("roadmap_invalid_price") or 0.0)
        take_profit_price = float(analysis.get("roadmap_take_profit_price") or 0.0)
        if current_price <= 0 or invalid_price <= 0 or take_profit_price <= current_price:
            return None

        reason = analysis.get("reason") or analysis.get("roadmap_reason") or "20/60일선 눌림형 진입"
        metadata = {
            "market": analysis.get("market", "KRX"),
            "currency": analysis.get("currency", "KRW"),
            "exchange_rate_to_krw": float(analysis.get("exchange_rate_to_krw", 1.0) or 1.0),
            "price_krw": float(analysis.get("price_krw", current_price) or current_price),
            "market_regime": market_regime,
            "roadmap_stage": roadmap_stage,
            "roadmap_anchor_price": analysis.get("roadmap_anchor_price"),
            "roadmap_invalid_price": invalid_price,
            "roadmap_take_profit_price": take_profit_price,
            "roadmap_second_tranche_allowed": bool(
                analysis.get("roadmap_second_tranche_allowed")
            ),
        }
        return TradeSignal(
            symbol=str(analysis.get("symbol") or ""),
            stock_id=str(analysis.get("stock_id") or ""),
            action=SignalAction.BUY,
            strength=confidence,
            suggested_price=current_price,
            target_price=take_profit_price,
            stop_loss_price=invalid_price,
            take_profit_price=take_profit_price,
            urgency=SignalUrgency.WAIT,
            strategy_type=self.strategy_type,
            reason=reason,
            confidence=confidence,
            metadata=metadata,
        )
