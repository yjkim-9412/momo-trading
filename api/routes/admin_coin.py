"""코인 관리자 대시보드 API — 주식 admin과 독립된 /admin-coin 전용 라우트"""
import asyncio
from dataclasses import asdict
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import func, select

from admin.sse_manager import SSEManager
from core.config import settings
from core.database import get_async_db, get_async_db_with_transaction
from models.coin_activity_log import CoinActivityLog
from models.coin_broker_order import CoinBrokerOrder
from models.coin_recommendation import CoinRecommendation
from repositories.daily_report_repository import DailyReportRepository
from scheduler.market_calendar import market_calendar
from schemas.coin_order_schema import (
    CoinOrderCancelResponse,
    CoinOrderExecutionResponse,
    CoinOrderPlaceRequest,
    CoinOrderPreviewRequest,
    CoinOrderPreviewResponse,
    CoinOrderStatusResponse,
)
from schemas.activity_schema import ActivityFeedCursor, ActivityFeedResponse
from schemas.common import SuccessResponse
from schemas.daily_report_schema import DailyReportResponse
from services.activity_logger import activity_logger
from services.coin_order_service import CoinOrderService
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType
from trading.market_profile import MARKET_SCOPE_CRYPTO
from trading.models import AccountBalance, HoldingInfo, PendingOrderInfo
from util.time_util import now_kst

router = APIRouter(prefix="/admin-coin", tags=["admin-coin"])

# 코인 전용 SSE 매니저 (주식 SSE와 독립)
coin_sse_manager = SSEManager()
APP_STARTED_AT = now_kst()

# 페이지 서빙은 main.py에서 /admin-coin 경로로 처리


def _crypto_market_code() -> str:
    return settings.crypto_primary_market_code


# ── 계좌 정보 ──
@router.get("/account/balance")
async def get_coin_balance():
    """빗썸 계좌 잔고 조회"""
    try:
        balance = await account_manager.get_balance(_crypto_market_code())
        return SuccessResponse(data=_serialize_balance(balance))
    except Exception as e:
        logger.error("코인 잔고 조회 실패: {}", str(e))
        return SuccessResponse(data=None, message=f"잔고 조회 실패: {str(e)[:100]}")


@router.get("/account/holdings")
async def get_coin_holdings():
    """빗썸 보유 코인 조회"""
    try:
        holdings = await account_manager.get_holdings(_crypto_market_code())
        return SuccessResponse(data=_serialize_holdings(holdings))
    except Exception as e:
        logger.error("보유 코인 조회 실패: {}", str(e))
        return SuccessResponse(data=[], message=f"보유 코인 조회 실패: {str(e)[:100]}")


@router.get("/account/overview")
async def get_coin_overview():
    """코인 계좌 overview"""
    try:
        overview = await account_manager.get_account_overview(_crypto_market_code())
        return SuccessResponse(data={
            "balance": _serialize_balance(overview.balance),
            "holdings": _serialize_holdings(overview.holdings),
            "pending_orders": _serialize_pending_orders(overview.pending_orders),
        })
    except Exception as e:
        logger.error("코인 overview 조회 실패: {}", str(e))
        return SuccessResponse(
            data={"balance": None, "holdings": [], "pending_orders": []},
            message=f"코인 overview 조회 실패: {str(e)[:100]}",
        )


@router.get("/account/pending-orders")
async def get_coin_pending_orders():
    """코인 미체결 주문 조회"""
    try:
        orders = await account_manager.get_pending_orders(_crypto_market_code())
        return SuccessResponse(data=_serialize_pending_orders(orders))
    except Exception as e:
        logger.error("코인 미체결 주문 조회 실패: {}", str(e))
        return SuccessResponse(data=[], message=f"코인 미체결 주문 조회 실패: {str(e)[:100]}")


# ── 수동 코인 주문 API ──
@router.post("/orders/preview", response_model=SuccessResponse[CoinOrderPreviewResponse])
async def preview_coin_order(
    request: CoinOrderPreviewRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """코인 수동 주문 미리보기"""
    service = CoinOrderService(db)
    preview = await service.preview_order(request)
    return SuccessResponse(data=preview, message="코인 주문 미리보기 계산 완료")


@router.post("/orders", response_model=SuccessResponse[CoinOrderExecutionResponse])
async def place_coin_order(
    request: CoinOrderPlaceRequest,
    db: AsyncSession = Depends(get_async_db_with_transaction),
):
    """코인 수동 주문 실행"""
    service = CoinOrderService(db)
    result = await service.place_order(request)
    return SuccessResponse(data=result, message=result.message)


@router.get("/orders/{order_id}", response_model=SuccessResponse[CoinOrderStatusResponse])
async def get_coin_order(
    order_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """코인 단건 주문 상태 조회"""
    service = CoinOrderService(db)
    result = await service.get_order_status(order_id)
    return SuccessResponse(data=result)


@router.delete("/orders/{order_id}", response_model=SuccessResponse[CoinOrderCancelResponse])
async def cancel_coin_order(
    order_id: str,
    db: AsyncSession = Depends(get_async_db_with_transaction),
):
    """코인 주문 취소"""
    service = CoinOrderService(db)
    result = await service.cancel_order(order_id)
    return SuccessResponse(data=result, message=result.message)


# ── 활동 피드 ──
@router.get("/activities/feed", response_model=SuccessResponse[ActivityFeedResponse])
async def get_coin_activity_feed(
    limit: int = Query(100, ge=1, le=200),
    target_date: str | None = Query(None, description="YYYY-MM-DD"),
    before_created_at: datetime | None = Query(None),
    before_id: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    """크립토 활동 피드 (coin_activity_logs 직접 쿼리)"""
    resolved_date = (
        date.fromisoformat(target_date)
        if target_date
        else market_calendar.market_date(market=MARKET_SCOPE_CRYPTO)
    )
    stmt = (
        select(CoinActivityLog)
        .where(CoinActivityLog.trading_date == resolved_date)
        .order_by(CoinActivityLog.created_at.desc())
        .limit(limit + 1)
    )
    if before_created_at and before_id:
        stmt = stmt.where(
            (CoinActivityLog.created_at < before_created_at)
            | ((CoinActivityLog.created_at == before_created_at) & (CoinActivityLog.id < before_id))
        )
    rows = (await db.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = None
    if has_more and items:
        last_item = items[-1]
        next_cursor = ActivityFeedCursor(
            before_created_at=last_item.created_at,
            before_id=last_item.id,
        )
    return SuccessResponse(data=ActivityFeedResponse(
        items=[_serialize_activity(a) for a in items],
        resolved_trading_date=resolved_date,
        has_more=has_more,
        next_cursor=next_cursor,
    ))


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
    from realtime.coin_stream_manager import coin_stream_manager
    from realtime.event_detector import DEFAULT_THRESHOLDS, event_detector

    state = trading_agent._market_states.get(MARKET_SCOPE_CRYPTO)
    default_thresholds = asdict(DEFAULT_THRESHOLDS)
    crypto_market = _crypto_market_code()
    holdings = await account_manager.get_holdings(crypto_market)
    holding_keys = {
        (str(h.market or crypto_market).upper(), str(h.symbol or "").upper())
        for h in holdings
        if getattr(h, "symbol", None)
    }
    name_map = {
        (str(h.market or crypto_market).upper(), str(h.symbol or "").upper()): str(h.name or "")
        for h in holdings
        if getattr(h, "symbol", None)
    }
    selected_map: dict[tuple[str, str], dict] = {}
    all_symbols: dict[tuple[str, str], dict[str, bool]] = {}

    for sym_info in getattr(state, "last_selected_watchlist", []) if state else []:
        market_code = str(sym_info.get("market", crypto_market) or crypto_market).upper()
        symbol = str(sym_info.get("symbol", "")).upper()
        if not symbol:
            continue
        key = (market_code, symbol)
        selected_map[key] = {
            "name": str(sym_info.get("name", "") or ""),
            "price": sym_info.get("price"),
            "change_rate": sym_info.get("change_rate"),
            "volume": sym_info.get("volume"),
            "trade_value": sym_info.get("trade_value"),
            "strategy_type": sym_info.get("strategy_type", ""),
            "reason": str(sym_info.get("reason", "") or ""),
            "scan_source": str(sym_info.get("scan_source", "") or ""),
        }
        all_symbols.setdefault(key, {
            "selected_in_last_cycle": False,
            "in_desired_set": False,
        })
        all_symbols[key]["selected_in_last_cycle"] = True

    desired_set = coin_stream_manager.desired_keys(MARKET_SCOPE_CRYPTO)
    active_set = coin_stream_manager.active_keys(MARKET_SCOPE_CRYPTO)
    for key in desired_set:
        all_symbols.setdefault(key, {
            "selected_in_last_cycle": False,
            "in_desired_set": False,
        })
        all_symbols[key]["in_desired_set"] = True

    for key in holding_keys:
        all_symbols.setdefault(key, {
            "selected_in_last_cycle": False,
            "in_desired_set": False,
        })

    for instrument_key in event_detector.monitored_symbols:
        if not instrument_key.startswith(f"{crypto_market}:"):
            continue
        _, symbol = instrument_key.split(":", 1)
        key = (crypto_market, symbol)
        all_symbols.setdefault(key, {
            "selected_in_last_cycle": False,
            "in_desired_set": False,
        })

    symbols_list = []
    for (market_code, symbol), flags in all_symbols.items():
        threshold_obj = event_detector._thresholds.get(f"{market_code}:{symbol}")
        threshold_data = None
        if threshold_obj:
            threshold_dict = asdict(threshold_obj)
            if threshold_dict != default_thresholds:
                threshold_data = threshold_dict
        meta = selected_map.get((market_code, symbol), {})
        symbols_list.append({
            "symbol": symbol,
            "market": market_code,
            "name": meta.get("name") or name_map.get((market_code, symbol), ""),
            "price": meta.get("price"),
            "change_rate": meta.get("change_rate"),
            "volume": meta.get("volume"),
            "trade_value": meta.get("trade_value"),
            "strategy_type": meta.get("strategy_type", ""),
            "reason": meta.get("reason", ""),
            "scan_source": meta.get("scan_source", ""),
            "is_holding": (market_code, symbol) in holding_keys,
            "is_subscribed": (market_code, symbol) in active_set,
            "in_desired_set": flags["in_desired_set"],
            "selected_in_last_cycle": flags["selected_in_last_cycle"],
            "has_thresholds": threshold_data is not None,
            "thresholds": threshold_data,
        })

    symbols_list.sort(
        key=lambda item: (
            not item["is_holding"],
            not item["selected_in_last_cycle"],
            not item["in_desired_set"],
            not item["has_thresholds"],
            item["symbol"],
        )
    )
    return SuccessResponse(data={
        "symbols": symbols_list,
        "stream_status": coin_stream_manager.stream_status(MARKET_SCOPE_CRYPTO),
    })


# ── 추천 목록 (SEMI_AUTO) ──
@router.get("/recommendations")
async def get_coin_recommendations(
    status: str = Query("PENDING"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_async_db),
):
    """코인 AI 추천 목록 (coin_recommendations 직접 쿼리)"""
    stmt = (
        select(CoinRecommendation)
        .where(CoinRecommendation.status == status.upper())
        .order_by(CoinRecommendation.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return SuccessResponse(data=[_serialize_recommendation(r) for r in rows])


@router.post("/recommendations/{rec_id}/approve")
async def approve_coin_recommendation(
    rec_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """코인 추천 승인 → 주문 실행"""
    stmt = select(CoinRecommendation).where(CoinRecommendation.id == rec_id)
    rec = (await db.execute(stmt)).scalar_one_or_none()
    if not rec:
        return SuccessResponse(data=None, message="추천을 찾을 수 없습니다")
    if rec.status != "PENDING":
        return SuccessResponse(data=None, message=f"이미 {rec.status} 상태입니다")
    rec.status = "APPROVED"
    rec.approved_at = now_kst()
    await db.commit()
    return SuccessResponse(data={"id": rec.id, "status": rec.status}, message="승인 완료")


@router.post("/recommendations/{rec_id}/reject")
async def reject_coin_recommendation(
    rec_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """코인 추천 거절"""
    stmt = select(CoinRecommendation).where(CoinRecommendation.id == rec_id)
    rec = (await db.execute(stmt)).scalar_one_or_none()
    if not rec:
        return SuccessResponse(data=None, message="추천을 찾을 수 없습니다")
    if rec.status != "PENDING":
        return SuccessResponse(data=None, message=f"이미 {rec.status} 상태입니다")
    rec.status = "REJECTED"
    await db.commit()
    return SuccessResponse(data={"id": rec.id, "status": rec.status}, message="거절 완료")


# ── 시스템 상태 ──
@router.get("/system/status")
async def get_coin_system_status(db: AsyncSession = Depends(get_async_db)):
    """코인 시스템 상태"""
    from agent.trading_agent import trading_agent
    from realtime.coin_monitor import coin_realtime_monitor
    from realtime.coin_stream_manager import coin_stream_manager
    from scheduler.scheduler import trading_scheduler
    from trading.bithumb_client import bithumb_client

    crypto_market = _crypto_market_code()
    session_schedule = market_calendar.get_session_schedule(market=crypto_market)
    cycle_runtime = trading_agent.get_cycle_runtime_snapshot(crypto_market)
    runtime = trading_agent._market_states.get(MARKET_SCOPE_CRYPTO)
    watchlist = list(getattr(runtime, "last_selected_watchlist", []) or [])
    trading_date = market_calendar.market_date(market=MARKET_SCOPE_CRYPTO)
    day_start, day_end = market_calendar.market_day_bounds(
        market=MARKET_SCOPE_CRYPTO,
        trading_date=trading_date,
    )
    today_filled_order_count = await db.scalar(
        select(func.count(CoinBrokerOrder.id)).where(
            CoinBrokerOrder.filled_quantity > 0,
            CoinBrokerOrder.filled_at.is_not(None),
            CoinBrokerOrder.filled_at >= day_start,
            CoinBrokerOrder.filled_at <= day_end,
        )
    )
    uptime_seconds = max(0, int((now_kst() - APP_STARTED_AT).total_seconds()))
    discovery_cache = {
        "enabled": bool(settings.CRYPTO_DYNAMIC_DISCOVERY_ENABLED),
        **bithumb_client.get_discovery_cache_status(),
    }

    return SuccessResponse(data={
        "crypto_enabled": settings.CRYPTO_ENABLED,
        "crypto_primary_market": crypto_market,
        "crypto_trading_enabled": settings.CRYPTO_TRADING_ENABLED,
        "crypto_autonomy_mode": settings.CRYPTO_AUTONOMY_MODE,
        "crypto_dynamic_discovery_enabled": settings.CRYPTO_DYNAMIC_DISCOVERY_ENABLED,
        "trading_enabled": settings.CRYPTO_TRADING_ENABLED,
        "autonomy_mode": settings.CRYPTO_AUTONOMY_MODE,
        "scheduler_running": trading_scheduler.is_running,
        "agent_running": trading_agent._running,
        "market": crypto_market,
        "market_scope": MARKET_SCOPE_CRYPTO,
        "market_open": True,  # 24/7
        "market_session": session_schedule["current_session"],
        "market_sessions": session_schedule["sessions"],
        "last_cycle_time": trading_agent.last_cycle_time.isoformat() if trading_agent.last_cycle_time else None,
        "last_cycle_attempt_at": cycle_runtime.get("last_cycle_attempt_at"),
        "last_cycle_status": cycle_runtime.get("last_cycle_status"),
        "last_cycle_error": cycle_runtime.get("last_cycle_error"),
        "realtime_monitor_running": coin_realtime_monitor.is_running,
        "scan_interval_hours": settings.CRYPTO_SCAN_INTERVAL_HOURS,
        "watchlist_symbols": settings.crypto_watchlist_symbols,
        "watchlist_count": len(watchlist),
        "discovery_cache": discovery_cache,
        "coin_sse_clients": coin_sse_manager.client_count,
        "sse_clients": coin_sse_manager.client_count,
        "app_started_at": APP_STARTED_AT.isoformat(),
        "uptime_seconds": uptime_seconds,
        "today_filled_order_count": int(today_filled_order_count or 0),
        "realtime": coin_stream_manager.stream_status(MARKET_SCOPE_CRYPTO),
        "private_sync": coin_stream_manager.private_sync_status(),
    })


@router.get("/agent/state")
async def get_coin_agent_state():
    """현재 코인 에이전트 파이프라인 상태"""
    from agent.trading_agent import trading_agent

    runtime = trading_agent._market_states.get(MARKET_SCOPE_CRYPTO)
    if not runtime:
        return SuccessResponse(data={
            "cycle_active": False,
            "cycle_id": None,
            "started_at": None,
            "phase": None,
            "scanned_count": 0,
            "analyzed_count": 0,
            "selected_symbols": [],
        })

    pipeline = getattr(runtime, "_pipeline_snapshot", None) or {}
    return SuccessResponse(data={
        "cycle_active": runtime.cycle_lock.locked(),
        "cycle_id": pipeline.get("cycle_id"),
        "started_at": pipeline.get("started_at"),
        "phase": pipeline.get("phase"),
        "scanned_count": pipeline.get("scanned_count", 0),
        "analyzed_count": pipeline.get("analyzed_count", 0),
        "selected_symbols": pipeline.get("selected_symbols", []),
    })


# ── 설정 조회/변경 ──
COIN_MUTABLE_SETTINGS = [
    "CRYPTO_ENABLED",
    "CRYPTO_PRIMARY_MARKET",
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

    crypto_market = _crypto_market_code()
    preview = await trading_agent.preview_cycle(market=crypto_market)
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
        result = await trading_agent.run_cycle(market=crypto_market)
        if result.get("skipped"):
            logger.info("수동 코인 스캔 스킵: {}", result.get("reason", "skipped"))
            return
        try:
            from services.watchlist_sync import reconcile_market_watchlist

            synced_symbols = await reconcile_market_watchlist(crypto_market)
            logger.info("수동 코인 스캔 후 실시간 감시 갱신: {}종목", len(synced_symbols))
        except Exception as e:
            logger.warning("수동 코인 스캔 후 실시간 감시 갱신 실패: {}", str(e))

    task = asyncio.create_task(_run_crypto_cycle())
    task.add_done_callback(lambda t: (
        logger.error("수동 코인 스캔 실패: {}", str(t.exception()))
        if not t.cancelled() and t.exception()
        else None
    ))

    return SuccessResponse(
        data={"market": crypto_market, "market_scope": MARKET_SCOPE_CRYPTO, "skipped": False},
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
    return balance.model_dump()


def _serialize_activity(a: CoinActivityLog) -> dict[str, object]:
    return {
        "id": a.id,
        "cycle_id": a.cycle_id,
        "activity_type": a.activity_type,
        "phase": a.phase,
        "symbol": a.symbol,
        "summary": a.summary,
        "detail": a.detail,
        "llm_provider": a.llm_provider,
        "llm_tier": a.llm_tier,
        "execution_time_ms": a.execution_time_ms,
        "confidence": a.confidence,
        "error_message": a.error_message,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _serialize_recommendation(r: CoinRecommendation) -> dict[str, object]:
    return {
        "id": r.id,
        "coin_asset_id": r.coin_asset_id,
        "action": r.action,
        "suggested_price": r.suggested_price,
        "suggested_quantity": r.suggested_quantity,
        "suggested_amount_krw": r.suggested_amount_krw,
        "reason": r.reason,
        "confidence": r.confidence,
        "status": r.status,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_holdings(holdings: list[HoldingInfo]) -> list[dict[str, object]]:
    return [h.model_dump() for h in holdings]


def _serialize_pending_orders(orders: list[PendingOrderInfo]) -> list[dict[str, object]]:
    return [o.model_dump() for o in orders]
