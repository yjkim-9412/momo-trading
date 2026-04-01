"""실시간 이벤트 감지 — 종목별 AI 설정 임계값 기반"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger

from core.events import Event, EventType, event_bus


@dataclass
class StockThresholds:
    """종목별 감시 임계값 (AI가 종목 선정 시 설정)"""
    surge_pct: float = 3.0        # 급등 기준 (%)
    drop_pct: float = -3.0        # 급락 기준 (%)
    volume_spike_ratio: float = 3.0  # 거래량 급증 배수
    stop_loss: float = 0.0        # 손절 가격
    take_profit: float = 0.0      # 익절 가격 (첫 번째 TP 레벨, 하위 호환)
    trailing_stop_pct: float = 0.0   # 트레일링 스탑 (%, 0이면 미사용)

    # 트레일링 스탑용 고점 추적
    highest_price: float = 0.0

    # 다단계 익절 지원 (ExitPlan 연동)
    exit_plan_id: str | None = None
    tp_levels: list[dict] = field(default_factory=list)
    # tp_levels 예시: [{"price": 155.0, "pct": 50, "level_index": 0}, ...]


# 기본 임계값 (AI 미설정 시 폴백)
DEFAULT_THRESHOLDS = StockThresholds()


class EventDetector:
    """
    실시간 가격 데이터에서 이벤트 감지 — 종목별 임계값 기반

    AI Agent가 종목 선정 시 set_thresholds()로 종목별 기준을 설정하고,
    WebSocket 체결 데이터가 들어올 때마다 해당 기준으로 이벤트를 감지한다.
    """

    def __init__(self):
        # 종목별 임계값 (AI가 설정)
        self._thresholds: dict[str, StockThresholds] = {}

        # 실시간 데이터 캐시
        self._prev_prices: dict[str, float] = {}
        self._volume_history: dict[str, list[int]] = defaultdict(list)

        # 이벤트 중복 발행 방지 (종목별 마지막 이벤트 타입+시간)
        self._last_events: dict[str, tuple[str, float]] = {}
        self.EVENT_DEDUP_SEC = 60  # 같은 이벤트 60초 내 재발행 방지

    @staticmethod
    def _instrument_key(symbol: str, market: str | None = None) -> str:
        market_code = str(market or "KRX").upper()
        return f"{market_code}:{str(symbol).upper()}"

    def set_thresholds(
        self,
        symbol: str,
        market: str | None = None,
        *,
        exit_plan_id: str | None = None,
        tp_levels: list[dict] | None = None,
        **kwargs,
    ) -> None:
        """종목별 감시 임계값 설정 (AI Agent가 호출)

        사용 예:
            event_detector.set_thresholds("005930",
                surge_pct=2.0, drop_pct=-2.0,
                volume_spike_ratio=2.5,
                stop_loss=71000, take_profit=76000,
                trailing_stop_pct=2.0,
            )

        다단계 익절 (ExitPlan 연동):
            event_detector.set_thresholds("AAPL", market="NYSE",
                stop_loss=140.0, trailing_stop_pct=2.0,
                exit_plan_id="abc-123",
                tp_levels=[
                    {"price": 155.0, "pct": 50, "level_index": 0},
                    {"price": 165.0, "pct": 100, "level_index": 1},
                ],
            )
        """
        import math

        # 값 검증: NaN, None, 숫자가 아닌 값 필터링
        validated = {}
        for k, v in kwargs.items():
            if isinstance(v, (int, float)) and not math.isnan(v):
                validated[k] = v
            else:
                logger.warning("유효하지 않은 임계값 무시: {} {} = {}", symbol, k, v)

        instrument_key = self._instrument_key(symbol, market)

        if instrument_key in self._thresholds:
            th = self._thresholds[instrument_key]
            for k, v in validated.items():
                if hasattr(th, k):
                    setattr(th, k, v)
        else:
            self._thresholds[instrument_key] = StockThresholds(**validated)

        # trailing_stop 설정 시 highest_price를 stop_loss 기반으로 초기화
        th = self._thresholds[instrument_key]
        if 0 < th.trailing_stop_pct < 100 and th.highest_price == 0 and th.stop_loss > 0:
            # stop_loss = highest × (1 - pct/100) → highest = stop_loss / (1 - pct/100)
            th.highest_price = th.stop_loss / (1 - th.trailing_stop_pct / 100)

        # 다단계 익절 설정
        if exit_plan_id is not None:
            th.exit_plan_id = exit_plan_id
        if tp_levels is not None:
            th.tp_levels = sorted(tp_levels, key=lambda x: x.get("price", 0))
            # take_profit을 첫 번째 미트리거 TP 레벨로 설정 (하위 호환)
            if th.tp_levels:
                th.take_profit = th.tp_levels[0]["price"]

        logger.debug("임계값 설정: {} → {}", instrument_key, self._thresholds[instrument_key])

    def get_thresholds(self, symbol: str, market: str | None = None) -> StockThresholds:
        return self._thresholds.get(self._instrument_key(symbol, market), DEFAULT_THRESHOLDS)

    def set_stop_loss(self, symbol: str, price: float, market: str | None = None) -> None:
        self.set_thresholds(symbol, market=market, stop_loss=price)

    def set_take_profit(self, symbol: str, price: float, market: str | None = None) -> None:
        self.set_thresholds(symbol, market=market, take_profit=price)

    def remove_levels(self, symbol: str, market: str | None = None) -> None:
        self._thresholds.pop(self._instrument_key(symbol, market), None)

    def clear_trade_thresholds(self, symbol: str, market: str | None = None) -> None:
        """매매용 임계값만 제거하고 스캔 감시는 유지한다."""
        instrument_key = self._instrument_key(symbol, market)
        thresholds = self._thresholds.get(instrument_key)
        if thresholds is None:
            return

        thresholds.stop_loss = 0.0
        thresholds.take_profit = 0.0
        thresholds.trailing_stop_pct = 0.0
        thresholds.highest_price = 0.0
        thresholds.exit_plan_id = None
        thresholds.tp_levels = []

    def advance_tp_level(self, symbol: str, market: str | None = None) -> None:
        """현재 첫 번째 TP 레벨을 제거하고 다음 레벨로 이동 (부분 익절 후 호출)."""
        instrument_key = self._instrument_key(symbol, market)
        th = self._thresholds.get(instrument_key)
        if th is None or not th.tp_levels:
            return
        th.tp_levels.pop(0)
        if th.tp_levels:
            th.take_profit = th.tp_levels[0]["price"]
            logger.info("다음 TP 레벨 활성화: {} → {:,.2f}", instrument_key, th.take_profit)
        else:
            th.take_profit = 0.0
            logger.info("모든 TP 레벨 소진: {}", instrument_key)

    async def restore_from_db(self) -> int:
        """DB에서 활성 ExitPlan을 로드하여 임계값을 복원한다. 복원된 plan 수를 반환."""
        import json

        try:
            from strategy.exit_plan_manager import exit_plan_manager
        except ImportError:
            logger.warning("exit_plan_manager import 실패, ExitPlan 복원 건너뜀")
            return 0

        plans = await exit_plan_manager.get_all_active_plans()
        restored = 0
        for plan in plans:
            try:
                levels = json.loads(plan.levels)
                sl_price = exit_plan_manager.get_stop_loss_price(levels)
                active_tp = exit_plan_manager.get_active_tp_levels(levels)

                tp_levels_for_detector = [
                    {"price": l["price"], "pct": l["pct"], "level_index": i}
                    for i, l in enumerate(active_tp)
                ]

                self.set_thresholds(
                    plan.symbol,
                    market=plan.market,
                    stop_loss=sl_price,
                    trailing_stop_pct=plan.trailing_stop_pct,
                    highest_price=plan.highest_price,
                    exit_plan_id=plan.id,
                    tp_levels=tp_levels_for_detector,
                )
                restored += 1
            except Exception:
                logger.exception("ExitPlan 복원 실패: plan_id=%s symbol=%s", plan.id, plan.symbol)

        if restored:
            logger.info("ExitPlan 복원 완료: %d건", restored)
        return restored

    def clear_all(self) -> None:
        """전체 초기화 (장 시작 시)"""
        self._thresholds.clear()
        self._prev_prices.clear()
        self._volume_history.clear()
        self._last_events.clear()

    @property
    def monitored_symbols(self) -> list[str]:
        return list(self._thresholds.keys())

    async def on_price_update(self, data: dict) -> None:
        """실시간 가격 업데이트 처리 + 이벤트 감지"""
        symbol = data.get("symbol", "")
        market = data.get("market", "KRX")
        price = data.get("price", 0)
        volume = data.get("volume", 0)
        change_rate = data.get("change_rate", 0)

        if not symbol or price <= 0:
            return

        th = self.get_thresholds(symbol, market=market)
        instrument_key = self._instrument_key(symbol, market)

        # 가격 업데이트 이벤트 발행
        await event_bus.publish(Event(
            type=EventType.PRICE_UPDATE,
            data=data,
            source="event_detector",
        ))

        # 트레일링 스탑 고점 갱신
        if th.trailing_stop_pct > 0 and price > th.highest_price:
            th.highest_price = price
            # 트레일링 스탑 가격 = 고점 × (1 - trailing_pct/100)
            new_stop = price * (1 - th.trailing_stop_pct / 100)
            if new_stop > th.stop_loss:
                th.stop_loss = new_stop
                logger.debug("트레일링 스탑 상향: {} → 손절 {:,.0f}원 (고점 {:,.0f})",
                             symbol, new_stop, price)

        # 거래량 급증 감지
        await self._check_volume_spike(instrument_key, volume, th, data)

        # 급등/급락 감지
        await self._check_price_movement(instrument_key, price, change_rate, th, data)

        # 손절/익절 감지
        await self._check_stop_take(instrument_key, price, th, data)

        # 캐시 업데이트
        self._prev_prices[instrument_key] = price
        self._volume_history[instrument_key].append(volume)
        if len(self._volume_history[instrument_key]) > 20:
            self._volume_history[instrument_key] = self._volume_history[instrument_key][-20:]

    async def _check_volume_spike(
        self, symbol: str, volume: int, th: StockThresholds, data: dict,
    ) -> None:
        history = self._volume_history.get(symbol, [])
        if len(history) < 5:
            return

        avg_volume = sum(history[-10:]) / len(history[-10:])
        if avg_volume > 0 and volume > avg_volume * th.volume_spike_ratio:
            if not self._should_dedup(symbol, "VOLUME_SPIKE"):
                spike_ratio = volume / avg_volume
                logger.info("거래량 급증: {} ({:.1f}배, 기준 {:.1f}배)",
                            symbol, spike_ratio, th.volume_spike_ratio)
                await event_bus.publish(Event(
                    type=EventType.VOLUME_SPIKE,
                    data={**data, "avg_volume": avg_volume, "spike_ratio": spike_ratio},
                    source="event_detector",
                ))

    async def _check_price_movement(
        self, symbol: str, price: float, change_rate: float,
        th: StockThresholds, data: dict,
    ) -> None:
        if change_rate >= th.surge_pct:
            if not self._should_dedup(symbol, "PRICE_SURGE"):
                logger.info("급등: {} ({:+.2f}%, 기준 {:.1f}%)",
                            symbol, change_rate, th.surge_pct)
                await event_bus.publish(Event(
                    type=EventType.PRICE_SURGE,
                    data=data,
                    source="event_detector",
                ))
        elif change_rate <= th.drop_pct:
            if not self._should_dedup(symbol, "PRICE_DROP"):
                logger.info("급락: {} ({:+.2f}%, 기준 {:.1f}%)",
                            symbol, change_rate, th.drop_pct)
                await event_bus.publish(Event(
                    type=EventType.PRICE_DROP,
                    data=data,
                    source="event_detector",
                ))

    async def _check_stop_take(
        self, symbol: str, price: float, th: StockThresholds, data: dict,
    ) -> None:
        # 손절 체크 (항상 전량)
        if th.stop_loss > 0 and price <= th.stop_loss:
            if not self._should_dedup(symbol, "STOP_LOSS"):
                logger.warning("손절선 도달: {} (현재 {:,.0f}, 손절 {:,.0f})",
                               symbol, price, th.stop_loss)
                await event_bus.publish(Event(
                    type=EventType.STOP_LOSS_HIT,
                    data={
                        **data,
                        "stop_loss_price": th.stop_loss,
                        "exit_plan_id": th.exit_plan_id,
                    },
                    source="event_detector",
                ))

        # 다단계 익절 체크
        if th.tp_levels:
            for tp_level in th.tp_levels:
                tp_price = tp_level.get("price", 0)
                if tp_price > 0 and price >= tp_price:
                    dedup_key = f"TAKE_PROFIT_L{tp_level.get('level_index', 0)}"
                    if not self._should_dedup(symbol, dedup_key):
                        logger.info(
                            "익절선 도달 (L{}): {} (현재 {:,.2f}, 익절 {:,.2f}, 청산 {}%)",
                            tp_level.get("level_index", 0), symbol, price, tp_price, tp_level.get("pct", 100),
                        )
                        await event_bus.publish(Event(
                            type=EventType.TAKE_PROFIT_HIT,
                            data={
                                **data,
                                "take_profit_price": tp_price,
                                "exit_plan_id": th.exit_plan_id,
                                "level_index": tp_level.get("level_index", 0),
                                "sell_pct": tp_level.get("pct", 100),
                            },
                            source="event_detector",
                        ))
                    # 첫 번째 미트리거 레벨만 체크 (순서대로)
                    break
        elif th.take_profit > 0 and price >= th.take_profit:
            # 하위 호환: tp_levels 없으면 기존 단일 TP 로직
            if not self._should_dedup(symbol, "TAKE_PROFIT"):
                logger.info("익절선 도달: {} (현재 {:,.0f}, 익절 {:,.0f})",
                            symbol, price, th.take_profit)
                await event_bus.publish(Event(
                    type=EventType.TAKE_PROFIT_HIT,
                    data={
                        **data,
                        "take_profit_price": th.take_profit,
                        "exit_plan_id": th.exit_plan_id,
                        "sell_pct": 100,
                    },
                    source="event_detector",
                ))

    def _should_dedup(self, symbol: str, event_type: str) -> bool:
        """같은 종목+이벤트 중복 발행 방지"""
        import time
        key = f"{symbol}:{event_type}"
        now = time.time()
        last = self._last_events.get(key)
        if last and now - last[1] < self.EVENT_DEDUP_SEC:
            return True
        self._last_events[key] = (event_type, now)
        return False


event_detector = EventDetector()
