"""코인 관리자 대시보드 API — 주식 admin과 독립된 /admin-coin 전용 라우트"""
import asyncio
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from admin.sse_manager import SSEManager
from core.config import settings
from core.database import get_async_db
from repositories.agent_activity_repository import AgentActivityRepository
from repositories.daily_report_repository import DailyReportRepository
from scheduler.market_calendar import market_calendar
from schemas.activity_schema import (
    ActivityFeedCursor,
    ActivityFeedResponse,
    ActivityResponse,
)
from schemas.common import SuccessResponse
from schemas.daily_report_schema import DailyReportResponse
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType
from trading.market_profile import MARKET_SCOPE_CRYPTO
from trading.models import AccountBalance, HoldingInfo

router = APIRouter(prefix="/coin", tags=["coin"])

# 코인 전용 SSE 매니저 (주식 SSE와 독립)
coin_sse_manager = SSEManager()

_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "admin" / "static"


# ── 코인 admin 페이지 서빙 ──
@router.get("/admin")
async def coin_admin_page():
    """코인 관리자 대시보드 페이지"""
    html_path = _STATIC_DIR / "coin.html"
    if not html_path.exists():
        return {"error": "coin.html not found"}
    return FileResponse(html_path, media_type="text/html")


# ── 계좌 정보 ──
@router.get("/account/balance")
async def get_coin_balance():
    """빗썸 계좌 잔고 조회"""
    try:
        balance = await account_manager.get_balance("BITHUMB")
        return SuccessResponse(data=_serialize_balance(balance))
    except Exception as e:
        logger.error("코인 잔고 조회 실패: {}", str(e))
        return SuccessResponse(data=None, message=f"잔고 조회 실패: {str(e)[:100]}")


@router.get("/account/holdings")
async def get_coin_holdings():
    """빗썸 보유 코인 조회"""
    try:
        holdings = await account_manager.get_holdings("BITHUMB")
        return SuccessResponse(data=_serialize_holdings(holdings))
    except Exception as e:
        logger.error("보유 코인 조회 실패: {}", str(e))
        return SuccessResponse(data=[], message=f"보유 코인 조회 실패: {str(e)[:100]}")


@router.get("/account/overview")
async def get_coin_overview():
    """코인 계좌 overview"""
    try:
        overview = await account_manager.get_account_overview("BITHUMB")
        return SuccessResponse(data={
            "balance": _serialize_balance(overview.balance),
            "holdings": _serialize_holdings(overview.holdings),
            "pending_orders": [],
        })
    except Exception as e:
        logger.error("코인 overview 조회 실패: {}", str(e))
        return SuccessResponse(
            data={"balance": None, "holdings": [], "pending_orders": []},
            message=f"코인 overview 조회 실패: {str(e)[:100]}",
        )


# ── 활동 피드 ──
@router.get("/activities/feed", response_model=SuccessResponse[ActivityFeedResponse])
async def get_coin_activity_feed(
    limit: int = Query(100, ge=1, le=200),
    target_date: str | None = Query(None, description="YYYY-MM-DD"),
    before_created_at: datetime | None = Query(None),
    before_id: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    """크립토 활동 피드"""
    repo = AgentActivityRepository(db)
    resolved_date = (
        date.fromisoformat(target_date)
        if target_date
        else market_calendar.market_date(market="BITHUMB")
    )
    items, has_more = await repo.get_feed_page(
        target_date=resolved_date,
        market_scope=MARKET_SCOPE_CRYPTO,
        limit=limit,
        before_created_at=before_created_at,
        before_id=before_id,
    )
    next_cursor = None
    if has_more and items:
        last_item = items[-1]
        next_cursor = ActivityFeedCursor(
            before_created_at=last_item.created_at,
            before_id=last_item.id,
        )
    return SuccessResponse(
        data=ActivityFeedResponse(
            items=items,
            resolved_trading_date=resolved_date,
            has_more=has_more,
            next_cursor=next_cursor,
        )
    )


# ── SSE 스트림 (코인 전용) ──
@router.get("/stream")
async def coin_sse_stream():
    """코인 전용 SSE 실시간 스트림"""
    client_id, queue = coin_sse_manager.connect()

    async def event_generator():
        try:
            yield f'data: {{"type": "connected", "client_id": "{client_id}"}}\n\n'
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {message}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            coin_sse_manager.disconnect(client_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── AI 감시 코인 목록 ──
@router.get("/watchlist")
async def get_coin_watchlist():
    """AI 감시 중인 코인 목록"""
    from agent.trading_agent import trading_agent

    state = trading_agent._market_states.get(MARKET_SCOPE_CRYPTO)
    symbols_list = []

    if state:
        for sym_info in getattr(state, "last_selected_watchlist", []):
            sym = str(sym_info.get("symbol", "")).upper()
            if sym:
                symbols_list.append({
                    "symbol": sym,
                    "market": "BITHUMB",
                    "name": str(sym_info.get("name", "") or ""),
                    "price": sym_info.get("price"),
                    "change_rate": sym_info.get("change_rate"),
                    "volume": sym_info.get("volume"),
                    "strategy_type": sym_info.get("strategy_type", ""),
                })

    return SuccessResponse(data={"symbols": symbols_list})


# ── 추천 목록 (SEMI_AUTO) ──
@router.get("/recommendations")
async def get_coin_recommendations(
    status: str = Query("PENDING"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_async_db),
):
    """코인 AI 추천 목록 (SEMI_AUTO 모드)"""
    from repositories.recommendation_repository import RecommendationRepository

    repo = RecommendationRepository(db)
    recs = await repo.get_by_status(
        status=status,
        market_scope=MARKET_SCOPE_CRYPTO,
        limit=limit,
    )
    return SuccessResponse(data=recs)


@router.post("/recommendations/{rec_id}/approve")
async def approve_coin_recommendation(
    rec_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """코인 추천 승인 → 주문 실행"""
    from services.recommendation_service import recommendation_service

    result = await recommendation_service.approve(rec_id, db=db)
    return SuccessResponse(data=result)


@router.post("/recommendations/{rec_id}/reject")
async def reject_coin_recommendation(
    rec_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """코인 추천 거절"""
    from services.recommendation_service import recommendation_service

    result = await recommendation_service.reject(rec_id, db=db)
    return SuccessResponse(data=result)


# ── 시스템 상태 ──
@router.get("/system/status")
async def get_coin_system_status():
    """코인 시스템 상태"""
    from agent.trading_agent import trading_agent

    session_schedule = market_calendar.get_session_schedule(market="BITHUMB")
    cycle_runtime = trading_agent.get_cycle_runtime_snapshot("BITHUMB")

    return SuccessResponse(data={
        "crypto_enabled": settings.CRYPTO_ENABLED,
        "crypto_trading_enabled": settings.CRYPTO_TRADING_ENABLED,
        "crypto_autonomy_mode": settings.CRYPTO_AUTONOMY_MODE,
        "market": "BITHUMB",
        "market_scope": MARKET_SCOPE_CRYPTO,
        "market_open": True,  # 24/7
        "market_session": session_schedule["current_session"],
        "market_sessions": session_schedule["sessions"],
        "last_cycle_time": trading_agent.last_cycle_time.isoformat() if trading_agent.last_cycle_time else None,
        "last_cycle_attempt_at": cycle_runtime.get("last_cycle_attempt_at"),
        "last_cycle_status": cycle_runtime.get("last_cycle_status"),
        "scan_interval_hours": settings.CRYPTO_SCAN_INTERVAL_HOURS,
        "watchlist_symbols": settings.crypto_watchlist_symbols,
        "coin_sse_clients": coin_sse_manager.client_count,
    })


# ── 설정 조회/변경 ──
COIN_MUTABLE_SETTINGS = [
    "CRYPTO_ENABLED",
    "CRYPTO_TRADING_ENABLED",
    "CRYPTO_AUTONOMY_MODE",
    "CRYPTO_RECOMMENDATION_EXPIRE_MIN",
    "CRYPTO_SCAN_INTERVAL_HOURS",
    "CRYPTO_MAX_POSITION_PCT",
    "CRYPTO_MIN_CASH_RATIO",
    "CRYPTO_MAX_SINGLE_ORDER_KRW",
]


@router.get("/settings")
async def get_coin_settings():
    """코인 런타임 설정 조회"""
    data = {}
    for key in COIN_MUTABLE_SETTINGS:
        data[key] = getattr(settings, key, None)
    return SuccessResponse(data=data)


@router.put("/settings")
async def update_coin_settings(updates: dict):
    """코인 런타임 설정 변경"""
    changed = {}
    for key, value in updates.items():
        if key not in COIN_MUTABLE_SETTINGS:
            continue
        old = getattr(settings, key, None)
        if isinstance(old, bool):
            value = str(value).lower() in ("true", "1", "yes")
        elif isinstance(old, int):
            value = int(value)
        elif isinstance(old, float):
            value = float(value)
        setattr(settings, key, value)
        changed[key] = {"old": old, "new": value}
        logger.info("코인 설정 변경: {} = {} → {}", key, old, value)

    if changed:
        await activity_logger.log(
            ActivityType.EVENT, ActivityPhase.PROGRESS,
            f"\u2699\ufe0f 코인 설정 변경: {', '.join(changed.keys())}",
            detail=changed,
            market_scope=MARKET_SCOPE_CRYPTO,
        )

    return SuccessResponse(data=changed, message=f"{len(changed)}개 코인 설정 변경됨")


# ── 수동 스캔 트리거 ──
@router.post("/agent/trigger")
async def trigger_coin_cycle():
    """수동 코인 스캔 사이클 트리거"""
    from agent.trading_agent import trading_agent

    if not settings.CRYPTO_ENABLED:
        return SuccessResponse(
            data={"skipped": True, "reason": "CRYPTO_ENABLED=false"},
            message="코인 기능이 비활성화되어 있습니다",
        )

    preview = await trading_agent.preview_cycle(market="BITHUMB")
    if preview.get("skipped"):
        return SuccessResponse(
            data={"skipped": True, "reason": preview.get("reason", "skipped")},
            message=f"코인 스캔 스킵: {preview.get('reason', 'skipped')}",
        )

    await activity_logger.log(
        ActivityType.EVENT, ActivityPhase.PROGRESS,
        "\U0001f3ae 수동 코인 스캔 트리거 (관리자)",
        market_scope=MARKET_SCOPE_CRYPTO,
    )

    async def _run_crypto_cycle():
        result = await trading_agent.run_cycle(market="BITHUMB")
        if result.get("skipped"):
            logger.info("수동 코인 스캔 스킵: {}", result.get("reason", "skipped"))

    task = asyncio.create_task(_run_crypto_cycle())
    task.add_done_callback(lambda t: (
        logger.error("수동 코인 스캔 실패: {}", str(t.exception()))
        if not t.cancelled() and t.exception()
        else None
    ))

    return SuccessResponse(
        data={"market": "BITHUMB", "market_scope": MARKET_SCOPE_CRYPTO, "skipped": False},
        message="코인 스캔 사이클이 트리거되었습니다",
    )


# ── 리포트 ──
@router.get("/reports", response_model=SuccessResponse[list[DailyReportResponse]])
async def get_coin_reports(
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """코인 일일 리포트 목록"""
    repo = DailyReportRepository(db)
    reports = await repo.get_reports(limit, market_scope=MARKET_SCOPE_CRYPTO)
    return SuccessResponse(data=reports)


@router.get("/reports/latest", response_model=SuccessResponse[DailyReportResponse | None])
async def get_latest_coin_report(db: AsyncSession = Depends(get_async_db)):
    """최신 코인 리포트"""
    repo = DailyReportRepository(db)
    report = await repo.get_latest(market_scope=MARKET_SCOPE_CRYPTO)
    return SuccessResponse(data=report)


# ── 직렬화 헬퍼 ──
def _serialize_balance(balance: AccountBalance) -> dict[str, object]:
    return {
        "total_asset": balance.total_asset,
        "cash": balance.cash,
        "raw_cash": balance.raw_cash,
        "effective_cash": balance.effective_cash,
        "cash_source": balance.cash_source,
        "stock_value": balance.stock_value,
        "total_pnl": balance.total_pnl,
        "total_pnl_rate": balance.total_pnl_rate,
        "market": balance.market,
        "currency": balance.currency,
        "is_valid": balance.is_valid,
        "status_message": balance.status_message,
    }


def _serialize_holdings(holdings: list[HoldingInfo]) -> list[dict[str, object]]:
    return [
        {
            "symbol": h.symbol,
            "name": h.name,
            "market": h.market,
            "currency": h.currency,
            "quantity": h.quantity,
            "avg_buy_price": h.avg_buy_price,
            "current_price": h.current_price,
            "pnl": h.pnl,
            "pnl_rate": h.pnl_rate,
        }
        for h in holdings
    ]
