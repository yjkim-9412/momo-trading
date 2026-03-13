"""관리자 대시보드 API — SSE 스트림 + 활동 조회 + 설정 + 리포트 + 계좌"""
import asyncio
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from admin.sse_manager import sse_manager
from core.config import settings
from core.database import get_async_db
from repositories.agent_activity_repository import AgentActivityRepository
from repositories.daily_report_repository import DailyReportRepository
from schemas.activity_schema import ActivityResponse, CycleResponse
from schemas.common import SuccessResponse
from schemas.daily_report_schema import DailyReportResponse
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType
from trading.market_profile import normalize_market_scope
from trading.mcp_client import mcp_client
from trading.models import AccountBalance, HoldingInfo, PendingOrderInfo

router = APIRouter(prefix="/admin", tags=["admin"])


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
        "raw_total_pnl": balance.raw_total_pnl,
        "raw_total_pnl_rate": balance.raw_total_pnl_rate,
        "pnl_source": balance.pnl_source,
        "market": balance.market,
        "currency": balance.currency,
        "exchange_rate_to_krw": balance.exchange_rate_to_krw,
        "status_message": balance.status_message,
        "is_valid": balance.is_valid,
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
            "exchange_rate_to_krw": h.exchange_rate_to_krw,
        }
        for h in holdings
    ]


def _serialize_pending_orders(orders: list[PendingOrderInfo]) -> list[dict[str, object]]:
    return [
        {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "name": o.name,
            "market": o.market,
            "currency": o.currency,
            "side": o.side,
            "order_qty": o.order_qty,
            "filled_qty": o.filled_qty,
            "remaining_qty": o.remaining_qty,
            "order_price": o.order_price,
            "order_time": o.order_time,
        }
        for o in orders
    ]


# ── AI 관심종목 (WebSocket 구독 + 임계값) ──
@router.get("/watchlist")
async def get_watchlist(market: str | None = Query(None)):
    """AI가 실시간 감시 중인 종목 목록 + 임계값"""
    from dataclasses import asdict

    from agent.trading_agent import trading_agent
    from realtime.event_detector import DEFAULT_THRESHOLDS, event_detector
    from realtime.stream_manager import stream_manager

    market_code = market or settings.primary_market_code
    scope = normalize_market_scope(market_code)

    # 1) 보유종목 이름 맵
    holding_map: dict[str, str] = {}
    try:
        holdings = await account_manager.get_holdings(market_code)
        for h in holdings:
            if h.symbol:
                holding_map[h.symbol.upper()] = h.name or ""
    except Exception:
        pass

    # 2) 파이프라인 스냅샷에서 종목명 보충
    name_map: dict[str, str] = dict(holding_map)
    state = trading_agent._market_states.get(scope)
    if state:
        pipeline = getattr(state, "_pipeline_snapshot", None) or {}
        for sym_info in pipeline.get("selected_symbols", []):
            if isinstance(sym_info, dict):
                sym = str(sym_info.get("symbol", "")).upper()
                if sym and sym not in name_map:
                    name_map[sym] = sym_info.get("name", "")

    # 3) scope에 해당하는 desired 종목 수집
    desired_set = stream_manager._desired_by_scope.get(scope, set())
    active_set = set(stream_manager._active_symbols.keys())

    # 4) event_detector 임계값 중 이 scope에 해당하는 것
    scope_prefixes = (f"{scope}:",) if scope == "KRX" else ("NASDAQ:", "NYSE:", "AMEX:", "US:")
    threshold_keys = [
        k for k in event_detector._thresholds
        if any(k.startswith(p) for p in scope_prefixes)
    ]

    # 5) 합집합 구성
    all_symbols: dict[tuple[str, str], bool] = {}  # (market, symbol) -> is_subscribed
    for market_sym, symbol_upper in desired_set:
        all_symbols[(market_sym, symbol_upper)] = (market_sym, symbol_upper) in active_set
    for key in threshold_keys:
        parts = key.split(":", 1)
        if len(parts) == 2:
            mk, sym = parts
            if (mk, sym) not in all_symbols:
                all_symbols[(mk, sym)] = (mk, sym) in active_set

    # 6) 직렬화
    default_dict = asdict(DEFAULT_THRESHOLDS)
    symbols_list = []
    for (mk, sym), is_sub in all_symbols.items():
        th = event_detector._thresholds.get(f"{mk}:{sym}")
        th_data = None
        if th:
            th_dict = asdict(th)
            if th_dict != default_dict:
                th_data = th_dict
        symbols_list.append({
            "symbol": sym,
            "market": mk,
            "name": name_map.get(sym, ""),
            "is_holding": sym in holding_map,
            "is_subscribed": is_sub,
            "thresholds": th_data,
        })

    # 보유종목 우선, 그 다음 임계값 있는 것, 나머지
    symbols_list.sort(key=lambda s: (not s["is_holding"], s["thresholds"] is None, s["symbol"]))

    return SuccessResponse(data={
        "symbols": symbols_list,
        "stream_status": {
            "connected": stream_manager.is_connected,
            "subscription_count": stream_manager.subscription_count,
            "subscription_limit": 41,
        },
    })


# ── SSE 실시간 스트림 ──
@router.get("/stream")
async def sse_stream():
    """SSE 실시간 활동 스트림"""
    client_id, queue = sse_manager.connect()

    async def event_generator():
        try:
            yield f"data: {{\"type\": \"connected\", \"client_id\": \"{client_id}\"}}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {message}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_manager.disconnect(client_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 활동 목록 ──
@router.get("/activities", response_model=SuccessResponse[list[ActivityResponse]])
async def get_activities(
    target_date: str | None = Query(None, description="YYYY-MM-DD"),
    cycle_id: str | None = Query(None),
    activity_type: str | None = Query(None),
    market_scope: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_db),
):
    """활동 로그 목록 조회"""
    repo = AgentActivityRepository(db)

    if cycle_id:
        activities = await repo.get_by_cycle(cycle_id)
    elif target_date:
        d = date.fromisoformat(target_date)
        activities = await repo.get_by_date(d, limit=limit, offset=offset, market_scope=market_scope)
    elif activity_type:
        activities = await repo.get_by_type(activity_type, limit=limit, market_scope=market_scope)
    else:
        from util.time_util import now_kst
        activities = await repo.get_by_date(now_kst().date(), limit=limit, offset=offset, market_scope=market_scope)

    return SuccessResponse(data=activities)


# ── 사이클 목록 ──
@router.get("/cycles", response_model=SuccessResponse[list[CycleResponse]])
async def get_cycles(
    limit: int = Query(20, ge=1, le=100),
    market_scope: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    """최근 사이클 목록"""
    repo = AgentActivityRepository(db)
    cycles = await repo.get_recent_cycles(limit, market_scope=market_scope)
    return SuccessResponse(data=cycles)


# ── 사이클 타임라인 ──
@router.get("/cycles/{cycle_id}/timeline", response_model=SuccessResponse[list[ActivityResponse]])
async def get_cycle_timeline(
    cycle_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """사이클 내 전체 활동 타임라인"""
    repo = AgentActivityRepository(db)
    activities = await repo.get_by_cycle(cycle_id)
    return SuccessResponse(data=activities)


# ── 일일 리포트 목록 ──
@router.get("/reports", response_model=SuccessResponse[list[DailyReportResponse]])
async def get_reports(
    limit: int = Query(30, ge=1, le=100),
    market_scope: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    """일일 리포트 목록"""
    repo = DailyReportRepository(db)
    reports = await repo.get_reports(limit, market_scope=market_scope)
    return SuccessResponse(data=reports)


# ── 특정 날짜 리포트 ──
@router.get("/reports/latest", response_model=SuccessResponse[DailyReportResponse | None])
async def get_latest_report(
    market_scope: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    """최신 리포트"""
    repo = DailyReportRepository(db)
    report = await repo.get_latest(market_scope=market_scope or settings.primary_market_code)
    return SuccessResponse(data=report)


@router.get("/reports/{report_date}", response_model=SuccessResponse[DailyReportResponse | None])
async def get_report_by_date(
    report_date: str,
    market_scope: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    """특정 날짜 리포트"""
    repo = DailyReportRepository(db)
    d = date.fromisoformat(report_date)
    report = await repo.get_by_date(d, market_scope=market_scope or settings.primary_market_code)
    return SuccessResponse(data=report)


# ── 계좌 정보 ──
@router.get("/account/balance")
async def get_account_balance(market: str | None = Query(None)):
    """계좌 잔고 조회"""
    try:
        market_code = market or settings.primary_market_code
        balance = await account_manager.get_balance(market_code)
        return SuccessResponse(data=_serialize_balance(balance))
    except Exception as e:
        logger.error("계좌 잔고 조회 실패: {}", str(e))
        return SuccessResponse(data=None, message=f"잔고 조회 실패: {str(e)[:100]}")


@router.get("/account/holdings")
async def get_account_holdings(market: str | None = Query(None)):
    """보유 종목 조회"""
    try:
        holdings = await account_manager.get_holdings(market or settings.primary_market_code)
        return SuccessResponse(data=_serialize_holdings(holdings))
    except Exception as e:
        logger.error("보유 종목 조회 실패: {}", str(e))
        return SuccessResponse(data=[], message=f"보유 종목 조회 실패: {str(e)[:100]}")


@router.get("/account/pending-orders")
async def get_pending_orders(market: str | None = Query(None)):
    """미체결 주문 조회"""
    try:
        orders = await account_manager.get_pending_orders(market or settings.primary_market_code)
        return SuccessResponse(data=_serialize_pending_orders(orders))
    except Exception as e:
        logger.error("미체결 주문 조회 실패: {}", str(e))
        return SuccessResponse(data=[], message=f"미체결 주문 조회 실패: {str(e)[:100]}")


@router.get("/account/overview")
async def get_account_overview(market: str | None = Query(None)):
    """계좌 overview 조회"""
    try:
        overview = await account_manager.get_account_overview(market or settings.primary_market_code)
        return SuccessResponse(data={
            "balance": _serialize_balance(overview.balance),
            "holdings": _serialize_holdings(overview.holdings),
            "pending_orders": _serialize_pending_orders(overview.pending_orders),
        })
    except Exception as e:
        logger.error("계좌 overview 조회 실패: {}", str(e))
        return SuccessResponse(
            data={
                "balance": None,
                "holdings": [],
                "pending_orders": [],
            },
            message=f"계좌 overview 조회 실패: {str(e)[:100]}",
        )


# ── 설정 조회/변경 ──
MUTABLE_SETTINGS = [
    "TRADING_ENABLED", "AUTONOMY_MODE",
    "RECOMMENDATION_EXPIRE_MIN",
    "SCHEDULER_ENABLED",
    "RISK_APPETITE",
]


@router.get("/settings")
async def get_settings():
    """런타임 설정 조회"""
    data = {}
    for key in MUTABLE_SETTINGS:
        data[key] = getattr(settings, key, None)
    data["ENABLED_MARKET_GROUPS"] = settings.enabled_market_groups
    return SuccessResponse(data=data)


@router.put("/settings")
async def update_settings(updates: dict):
    """런타임 설정 변경 (재시작 불필요)"""
    changed = {}
    for key, value in updates.items():
        if key not in MUTABLE_SETTINGS:
            continue
        old = getattr(settings, key, None)
        # 타입 변환
        if isinstance(old, bool):
            value = str(value).lower() in ("true", "1", "yes")
        elif isinstance(old, int):
            value = int(value)
        elif isinstance(old, float):
            value = float(value)
        setattr(settings, key, value)
        changed[key] = {"old": old, "new": value}
        logger.info("설정 변경: {} = {} → {}", key, old, value)

    if changed:
        await activity_logger.log(
            ActivityType.EVENT, ActivityPhase.PROGRESS,
            f"\u2699\ufe0f 설정 변경: {', '.join(changed.keys())}",
            detail=changed,
        )

    return SuccessResponse(data=changed, message=f"{len(changed)}개 설정 변경됨")


# ── LLM 사용량 ──
@router.get("/llm/usage")
async def get_llm_usage():
    """선택된 LLM provider 사용량 조회"""
    try:
        from analysis.llm.llm_factory import llm_factory

        return SuccessResponse(data=llm_factory.get_llm_usage())
    except Exception as e:
        logger.error("LLM 사용량 조회 실패: {}", str(e))
        return SuccessResponse(data=None, message=f"조회 실패: {str(e)[:100]}")


# ── LLM 상태 ──
@router.get("/llm/status")
async def get_llm_status():
    """LLM 프로바이더 상태 및 설정 조회"""
    from analysis.llm.llm_factory import llm_factory
    return SuccessResponse(data=await llm_factory.get_llm_status())


# ── 시스템 상태 ──
@router.get("/system/status")
async def get_system_status(market: str | None = Query(None)):
    """시스템 전체 상태"""
    from agent.trading_agent import trading_agent
    from scheduler.scheduler import trading_scheduler

    from scheduler.market_calendar import market_calendar

    market_code = market or settings.primary_market_code
    session_schedule = market_calendar.get_session_schedule(market=market_code)

    return SuccessResponse(data={
        "trading_enabled": settings.TRADING_ENABLED,
        "autonomy_mode": settings.AUTONOMY_MODE,
        "mcp_connected": mcp_client.is_connected,
        "scheduler_running": trading_scheduler.is_running,
        "agent_running": trading_agent._running,
        "last_cycle_time": trading_agent.last_cycle_time.isoformat() if trading_agent.last_cycle_time else None,
        "sse_clients": sse_manager.client_count,
        "environment": settings.ENVIRONMENT,
        "primary_market": settings.primary_market_code,
        "market": market_code,
        "market_open": market_calendar.is_trading_hours(market_code),
        "market_session": session_schedule["current_session"],
        "market_sessions": session_schedule["sessions"],
        "market_tz": session_schedule["tz_label"],
        "dst_active": session_schedule["dst_active"],
        "market_holiday": market_calendar.get_holiday_name(market=market_code),
        "next_market_open": market_calendar.next_market_open(market=market_code).strftime("%m/%d %H:%M"),
    })


# ── 에이전트 파이프라인 상태 ──
@router.get("/agent/state")
async def get_agent_state():
    """현재 에이전트 파이프라인 상태 (인메모리 조회, DB 없음)"""
    from agent.trading_agent import trading_agent

    result = {}
    for scope in ("KRX", "US"):
        state = trading_agent._market_states.get(scope)
        if not state:
            result[scope] = {
                "cycle_active": False,
                "cycle_id": None,
                "started_at": None,
                "scanned_count": 0,
                "analyzed_count": 0,
                "selected_symbols": [],
            }
        else:
            pipeline = getattr(state, "_pipeline_snapshot", None) or {}
            result[scope] = {
                "cycle_active": state.cycle_lock.locked(),
                "cycle_id": pipeline.get("cycle_id"),
                "started_at": pipeline.get("started_at"),
                "scanned_count": pipeline.get("scanned_count", 0),
                "analyzed_count": pipeline.get("analyzed_count", 0),
                "selected_symbols": pipeline.get("selected_symbols", []),
            }
    return SuccessResponse(data=result)


# ── 수동 사이클 트리거 ──
@router.post("/agent/trigger")
async def trigger_agent_cycle(market: str | None = Query(None)):
    """수동으로 에이전트 사이클 실행"""
    from agent.trading_agent import trading_agent

    market_code = market or settings.primary_market_code
    market_label = "US" if market_code in ("NASDAQ", "NYSE", "AMEX") else "KRX"
    preview = await trading_agent.preview_cycle(market=market_code)

    if preview.get("skipped"):
        reason = preview.get("reason", "skipped")
        return SuccessResponse(
            data={
                "market": market_code,
                "market_scope": preview.get("market_scope"),
                "trading_date": preview.get("trading_date"),
                "reason": reason,
                "skipped": True,
            },
            message=f"에이전트 사이클 스킵 ({market_label}, {reason})",
        )

    await activity_logger.log(
        ActivityType.EVENT, ActivityPhase.PROGRESS,
        f"\U0001f3ae 수동 사이클 트리거 (관리자, {market_label})",
    )

    # 비동기로 실행 (즉시 응답)
    asyncio.create_task(trading_agent.run_cycle(market=market_code))
    return SuccessResponse(
        data={
            "market": market_code,
            "market_scope": preview.get("market_scope"),
            "trading_date": preview.get("trading_date"),
            "mode": preview.get("mode"),
            "skipped": False,
        },
        message=f"에이전트 사이클이 트리거되었습니다 ({market_label})",
    )


# ── 수동 일일 리포트 생성 ──
@router.post("/reports/generate")
async def generate_report(
    target_date: str | None = Query(None),
    market_scope: str | None = Query(None),
):
    """수동 일일 리포트 생성"""
    from services.daily_report_service import daily_report_service
    d = date.fromisoformat(target_date) if target_date else None
    report = await daily_report_service.generate_daily_report(
        d,
        market_scope=market_scope or settings.primary_market_code,
    )
    if report:
        return SuccessResponse(
            data=DailyReportResponse.model_validate(report),
            message="리포트 생성 완료",
        )
    return SuccessResponse(message="리포트 생성 실패 또는 이미 존재")
