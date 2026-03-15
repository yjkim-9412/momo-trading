"""활동 로거 — DB 저장 + SSE 브로드캐스트 (싱글턴, DI 비의존)"""
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from uuid import uuid4

from loguru import logger

from admin.sse_manager import sse_manager
from core.database import AsyncSessionLocal
from models.agent_activity import AgentActivityLog
from models.coin_activity_log import CoinActivityLog
from trading.enums import ActivityPhase, ActivityType
from trading.market_profile import MARKET_SCOPE_CRYPTO, normalize_market_scope
from util.time_util import now_kst

_activity_market_scope: ContextVar[str | None] = ContextVar("activity_market_scope", default=None)
_activity_trading_date: ContextVar[date | None] = ContextVar("activity_trading_date", default=None)


class ActivityLogger:
    """
    에이전트 활동을 DB에 기록하고 SSE로 실시간 전송.
    자체 세션을 생성하므로 DI 없이 어디서든 호출 가능.
    """

    async def log(
        self,
        activity_type: ActivityType | str,
        phase: ActivityPhase | str,
        summary: str,
        *,
        cycle_id: str | None = None,
        market_scope: str | None = None,
        trading_date: date | None = None,
        stock_id: str | None = None,
        symbol: str | None = None,
        detail: dict | None = None,
        llm_provider: str | None = None,
        llm_tier: str | None = None,
        execution_time_ms: int | None = None,
        confidence: float | None = None,
        error_message: str | None = None,
    ) -> AgentActivityLog | CoinActivityLog | None:
        """활동 기록 → DB 저장 + SSE 브로드캐스트"""
        detail_json = json.dumps(detail, ensure_ascii=False, default=str) if detail else None
        ts = now_kst()
        resolved_scope = normalize_market_scope(market_scope) if market_scope else _activity_market_scope.get()
        resolved_trading_date = trading_date or _activity_trading_date.get()

        if resolved_scope == MARKET_SCOPE_CRYPTO:
            entry = CoinActivityLog(
                created_at=ts,
                cycle_id=cycle_id,
                trading_date=resolved_trading_date,
                activity_type=activity_type,
                phase=phase,
                coin_asset_id=stock_id,
                symbol=symbol,
                summary=summary,
                detail=detail_json,
                llm_provider=llm_provider,
                llm_tier=llm_tier,
                execution_time_ms=execution_time_ms,
                confidence=confidence,
                error_message=error_message,
            )
        else:
            entry = AgentActivityLog(
                created_at=ts,
                cycle_id=cycle_id,
                market_scope=resolved_scope,
                trading_date=resolved_trading_date,
                activity_type=activity_type,
                phase=phase,
                stock_id=stock_id,
                symbol=symbol,
                summary=summary,
                detail=detail_json,
                llm_provider=llm_provider,
                llm_tier=llm_tier,
                execution_time_ms=execution_time_ms,
                confidence=confidence,
                error_message=error_message,
            )

        # DB 저장 (자체 세션)
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    session.add(entry)
        except Exception as e:
            logger.error("활동 로그 DB 저장 실패: {}", str(e))

        # SSE 브로드캐스트 (CRYPTO → coin_sse_manager, 그 외 → 주식 sse_manager)
        broadcast_target = sse_manager
        if resolved_scope == MARKET_SCOPE_CRYPTO:
            try:
                from api.routes.admin_coin import coin_sse_manager
                broadcast_target = coin_sse_manager
            except ImportError:
                pass
        try:
            await broadcast_target.broadcast({
                "type": "activity",
                "data": {
                    "id": entry.id,
                    "cycle_id": cycle_id,
                    "market_scope": resolved_scope,
                    "trading_date": resolved_trading_date.isoformat() if resolved_trading_date else None,
                    "activity_type": activity_type,
                    "phase": phase,
                    "symbol": symbol,
                    "summary": summary,
                    "detail": detail_json,
                    "llm_provider": llm_provider,
                    "llm_tier": llm_tier,
                    "execution_time_ms": execution_time_ms,
                    "confidence": confidence,
                    "error_message": error_message,
                    "created_at": ts.isoformat(),
                },
            })
        except Exception as e:
            logger.error("SSE 브로드캐스트 실패: {}", str(e))

        return entry

    def start_cycle(self) -> str:
        """새 사이클 ID 생성"""
        return str(uuid4())

    @contextmanager
    def context(
        self,
        *,
        market_scope: str | None = None,
        trading_date: date | None = None,
    ):
        """활동 로그 기본 scope/date를 현재 async context에 바인딩"""
        scope_token = None
        date_token = None
        if market_scope is not None:
            scope_token = _activity_market_scope.set(normalize_market_scope(market_scope))
        if trading_date is not None:
            date_token = _activity_trading_date.set(trading_date)
        try:
            yield
        finally:
            if date_token is not None:
                _activity_trading_date.reset(date_token)
            if scope_token is not None:
                _activity_market_scope.reset(scope_token)

    @staticmethod
    def timer() -> float:
        """실행 시간 측정용 타이머 시작"""
        return time.time()

    @staticmethod
    def elapsed_ms(start: float) -> int:
        """타이머로부터 경과 ms"""
        return int((time.time() - start) * 1000)


activity_logger = ActivityLogger()
