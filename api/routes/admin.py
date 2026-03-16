"""관리자 대시보드 API — SSE 스트림 + 활동 조회 + 설정 + 리포트 + 계좌"""
import asyncio
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.sse_manager import sse_manager
from core.config import settings
from core.database import get_async_db
from models.broker_order import BrokerOrder
from repositories.agent_activity_repository import AgentActivityRepository
from repositories.daily_report_repository import DailyReportRepository
from scheduler.market_calendar import market_calendar
from schemas.activity_schema import (
    ActivityFeedCursor,
    ActivityFeedResponse,
    ActivityResponse,
    CycleResponse,
)
from schemas.common import SuccessResponse
from schemas.daily_report_schema import (
    DailyReportResponse,
    ReportComparisonResponse,
    ReportRefreshConfirmRequest,
)
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType
from trading.market_profile import normalize_market_scope
from trading.mcp_client import mcp_client
from trading.models import AccountBalance, HoldingInfo, PendingOrderInfo

router = APIRouter(prefix="/admin", tags=["admin"])


def _serialize_balance(balance: AccountBalance) -> dict[str, object]:
    return balance.model_dump()


def _serialize_holdings(holdings: list[HoldingInfo]) -> list[dict[str, object]]:
    return [h.model_dump() for h in holdings]


def _serialize_pending_orders(orders: list[PendingOrderInfo]) -> list[dict[str, object]]:
    return [o.model_dump() for o in orders]


def _serialize_broker_orders(orders: list[BrokerOrder]) -> list[dict[str, object]]:
    return [
        {
            "id": order.id,
            "cycle_id": order.cycle_id,
            "kis_order_id": order.kis_order_id,
            "market": order.market,
            "symbol": order.symbol,
            "stock_name": order.stock_name,
            "side": order.side,
            "status": order.status,
            "quantity": order.quantity,
            "requested_price": order.requested_price,
            "requested_price_krw": order.requested_price_krw,
            "filled_quantity": order.filled_quantity,
            "filled_price": order.filled_price,
            "filled_price_krw": order.filled_price_krw,
            "currency": order.currency,
            "exchange_rate_to_krw": order.exchange_rate_to_krw,
            "status_detail": order.status_detail,
            "error_message": order.error_message,
            "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            "filled_at": order.filled_at.isoformat() if order.filled_at else None,
            "updated_at": order.updated_at.isoformat() if order.updated_at else None,
        }
        for order in orders
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

    # 1) 보유종목 이름 맵 + 데이터
    holding_names: dict[tuple[str, str], str] = {}
    holding_keys: set[tuple[str, str]] = set()
    holding_data: dict[tuple[str, str], object] = {}
    try:
        holdings = await account_manager.get_holdings(market_code)
        for h in holdings:
            if h.symbol:
                key = (str(h.market or market_code).upper(), h.symbol.upper())
                holding_names[key] = h.name or ""
                holding_keys.add(key)
                holding_data[key] = h
    except Exception:
        pass

    # 2) 최근 선정 종목/파이프라인 스냅샷에서 종목명 보충
    name_map: dict[tuple[str, str], str] = dict(holding_names)
    selected_keys: set[tuple[str, str]] = set()
    state = trading_agent._market_states.get(scope)
    if state:
        for sym_info in getattr(state, "last_selected_watchlist", []):
            sym = str(sym_info.get("symbol", "")).upper()
            mk = str(sym_info.get("market", market_code)).upper()
            if not sym:
                continue
            key = (mk, sym)
            selected_keys.add(key)
            if key not in name_map:
                name_map[key] = str(sym_info.get("name", "") or "")
        pipeline = getattr(state, "_pipeline_snapshot", None) or {}
        for sym_info in pipeline.get("selected_symbols", []):
            if isinstance(sym_info, dict):
                sym = str(sym_info.get("symbol", "")).upper()
                mk = str(sym_info.get("market", market_code)).upper()
                key = (mk, sym)
                if sym and key not in name_map:
                    name_map[key] = str(sym_info.get("name", "") or "")

    # 2-b) 이름 미해소 심볼 → 최근 활동 로그 summary에서 [종목명] 추출
    import re as _re

    empty_name_keys = [k for k in name_map if not name_map[k]]
    if empty_name_keys:
        try:
            from core.database import AsyncSessionLocal
            from models.agent_activity import AgentActivityLog

            async with AsyncSessionLocal() as _db:
                for mk, sym in empty_name_keys:
                    result = await _db.execute(
                        select(AgentActivityLog.summary)
                        .where(
                            AgentActivityLog.symbol == sym,
                            AgentActivityLog.market_scope == scope,
                        )
                        .order_by(AgentActivityLog.created_at.desc())
                        .limit(10)
                    )
                    for (summary,) in result:
                        m = _re.match(r'[^\[]*\[([^\]]+)\]', summary or '')
                        if m and not _re.match(r'^TIER\d', m.group(1), _re.IGNORECASE):
                            name_map[(mk, sym)] = m.group(1)
                            break
        except Exception as e:
            logger.debug("종목명 보강(활동로그) 실패: {}", str(e))

    # 3) scope에 해당하는 desired 종목 수집
    desired_set = stream_manager.desired_keys(scope)
    active_set = stream_manager.active_keys(scope)

    # 4) event_detector 임계값 중 이 scope에 해당하는 것
    scope_prefixes = (f"{scope}:",) if scope == "KRX" else ("NASDAQ:", "NYSE:", "AMEX:", "US:")
    threshold_keys = [
        k for k in event_detector._thresholds
        if any(k.startswith(p) for p in scope_prefixes)
    ]

    # 5) 합집합 구성
    all_symbols: dict[tuple[str, str], dict[str, bool]] = {}
    for market_sym, symbol_upper in desired_set:
        key = (market_sym, symbol_upper)
        all_symbols[key] = {
            "in_desired_set": True,
            "selected_in_last_cycle": key in selected_keys,
        }
    for key in threshold_keys:
        parts = key.split(":", 1)
        if len(parts) == 2:
            mk, sym = parts
            all_symbols.setdefault(
                (mk, sym),
                {
                    "in_desired_set": (mk, sym) in desired_set,
                    "selected_in_last_cycle": (mk, sym) in selected_keys,
                },
            )
    for key in selected_keys:
        all_symbols.setdefault(
            key,
            {
                "in_desired_set": key in desired_set,
                "selected_in_last_cycle": True,
            },
        )

    # 6) 직렬화
    default_dict = asdict(DEFAULT_THRESHOLDS)
    symbols_list = []
    for (mk, sym), flags in all_symbols.items():
        th = event_detector._thresholds.get(f"{mk}:{sym}")
        is_holding = (mk, sym) in holding_keys
        th_data = None
        if th:
            th_dict = asdict(th)
            if th_dict != default_dict:
                th_data = th_dict
        holding_info = None
        if is_holding and (mk, sym) in holding_data:
            hd = holding_data[(mk, sym)]
            holding_info = {
                "pnl_rate": hd.pnl_rate,
                "pnl": hd.pnl,
                "current_price": hd.current_price,
                "avg_buy_price": hd.avg_buy_price,
                "quantity": hd.quantity,
                "eval_amount": hd.quantity * hd.current_price,
            }
        symbols_list.append({
            "symbol": sym,
            "market": mk,
            "name": name_map.get((mk, sym), ""),
            "is_holding": is_holding,
            "is_subscribed": (mk, sym) in active_set,
            "in_desired_set": flags["in_desired_set"],
            "selected_in_last_cycle": flags["selected_in_last_cycle"],
            "has_thresholds": th_data is not None,
            "thresholds": th_data,
            "holding_info": holding_info,
        })

    # 보유종목 우선, 그 다음 최근 선정, 실제 desired, threshold-only 순
    symbols_list.sort(
        key=lambda s: (
            not s["is_holding"],
            not s["selected_in_last_cycle"],
            not s["in_desired_set"],
            not s["has_thresholds"],
            s["symbol"],
        )
    )

    return SuccessResponse(data={
        "symbols": symbols_list,
        "stream_status": stream_manager.stream_status(scope),
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
        resolved_date = (
            market_calendar.market_date(market=market_scope)
            if market_scope
            else now_kst().date()
        )
        activities = await repo.get_by_date(
            resolved_date,
            limit=limit,
            offset=offset,
            market_scope=market_scope,
        )

    return SuccessResponse(data=activities)


@router.get("/activities/feed", response_model=SuccessResponse[ActivityFeedResponse])
async def get_activity_feed(
    market_scope: str = Query(...),
    limit: int = Query(100, ge=1, le=200),
    target_date: str | None = Query(None, description="YYYY-MM-DD"),
    before_created_at: datetime | None = Query(None),
    before_id: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    """실시간 피드 bootstrap / pagination 전용 활동 조회"""
    repo = AgentActivityRepository(db)
    resolved_scope = normalize_market_scope(market_scope)
    resolved_date = (
        date.fromisoformat(target_date)
        if target_date
        else market_calendar.market_date(market=resolved_scope)
    )
    items, has_more = await repo.get_feed_page(
        target_date=resolved_date,
        market_scope=resolved_scope,
        limit=limit,
        before_created_at=before_created_at,
        before_id=before_id,
    )

    # 종목명 보강: 1) stocks 테이블 → 2) 보유종목 → 3) summary에서 [종목명] 추출
    import re as _re

    symbol_set = {item.symbol for item in items if item.symbol}
    name_map: dict[str, str] = {}
    if symbol_set:
        from models.stock import Stock
        result = await db.execute(
            select(Stock.symbol, Stock.name).where(Stock.symbol.in_(symbol_set))
        )
        name_map = {row.symbol: row.name for row in result}
        if name_map:
            logger.debug("종목명 보강(stocks): {}", list(name_map.keys()))

    if symbol_set - name_map.keys():
        try:
            holdings = await account_manager.get_holdings(resolved_scope)
            for h in holdings:
                if h.symbol and h.symbol not in name_map and h.name:
                    name_map[h.symbol] = h.name
            remaining = symbol_set - name_map.keys()
            if not remaining:
                logger.debug("종목명 보강(보유종목): 전체 해소")
        except Exception as e:
            logger.debug("종목명 보강(보유종목) 조회 실패: {}", str(e))

    for item in items:
        if item.symbol and item.symbol not in name_map and item.summary:
            m = _re.match(r'[^\[]*\[([^\]]+)\]', item.summary)
            if m and not _re.match(r'^TIER\d', m.group(1), _re.IGNORECASE):
                name_map[item.symbol] = m.group(1)

    unresolved = symbol_set - name_map.keys()
    if unresolved:
        logger.warning("종목명 미해소 심볼: {} (stocks=0, holdings/summary fallback 실패)", unresolved)

    enriched_items = []
    for item in items:
        resp = ActivityResponse.model_validate(item)
        if item.symbol and item.symbol in name_map:
            resp.name = name_map[item.symbol]
        enriched_items.append(resp)

    next_cursor = None
    if has_more and items:
        last_item = items[-1]
        next_cursor = ActivityFeedCursor(
            before_created_at=last_item.created_at,
            before_id=last_item.id,
        )
    return SuccessResponse(
        data=ActivityFeedResponse(
            items=enriched_items,
            resolved_trading_date=resolved_date,
            has_more=has_more,
            next_cursor=next_cursor,
        )
    )


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


@router.get("/account/broker-orders")
async def get_broker_orders(
    market: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
):
    """자동매매 브로커 주문 이력 조회"""
    stmt = select(BrokerOrder).order_by(BrokerOrder.created_at.desc()).limit(limit)
    if market:
        stmt = stmt.where(BrokerOrder.market == market)
    rows = (await db.execute(stmt)).scalars().all()
    return SuccessResponse(data=_serialize_broker_orders(list(rows)))


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
    from realtime.monitor import realtime_monitor
    from realtime.stream_manager import stream_manager
    from scheduler.scheduler import trading_scheduler

    from scheduler.market_calendar import market_calendar

    market_code = market or settings.primary_market_code
    session_schedule = market_calendar.get_session_schedule(market=market_code)
    cycle_runtime = trading_agent.get_cycle_runtime_snapshot(market_code)

    return SuccessResponse(data={
        "trading_enabled": settings.TRADING_ENABLED,
        "autonomy_mode": settings.AUTONOMY_MODE,
        "mcp_connected": mcp_client.is_connected,
        "scheduler_running": trading_scheduler.is_running,
        "agent_running": trading_agent._running,
        "last_cycle_time": trading_agent.last_cycle_time.isoformat() if trading_agent.last_cycle_time else None,
        "last_cycle_attempt_at": cycle_runtime["last_cycle_attempt_at"],
        "last_cycle_status": cycle_runtime["last_cycle_status"],
        "last_cycle_error": cycle_runtime["last_cycle_error"],
        "realtime_monitor_running": realtime_monitor.is_running,
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
        "realtime": stream_manager.stream_status(market_code),
    })


# ── 스케줄 타임라인 ──
@router.get("/schedule/timeline")
async def get_schedule_timeline(market: str | None = Query(None)):
    """AI 동적 재스캔 상태 + 정규 스케줄 타임라인"""
    from scheduler.scheduler import trading_scheduler
    from scheduler.market_calendar import market_calendar
    from trading.market_profile import is_us_market, market_scope, normalize_market

    market_code = normalize_market(market or settings.primary_market_code)
    scope = market_scope(market_code)
    is_us = is_us_market(market_code)
    now_local = trading_scheduler._market_now(market_code)

    # ── Adaptive state ──
    state = trading_scheduler._adaptive_state(market_code)
    remaining = trading_scheduler._remaining_scheduled_budget(market_code)
    next_run_at = state.next_adaptive_run_at
    next_run_in_minutes = None
    if next_run_at:
        delta = (next_run_at - now_local).total_seconds()
        next_run_in_minutes = max(0, int(delta / 60))

    adaptive_data = {
        "enabled": settings.AI_DYNAMIC_RESCAN_ENABLED,
        "cycles_used": state.scheduled_cycle_count_today,
        "cycles_max": settings.AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION,
        "cycles_remaining": remaining,
        "next_run_at": next_run_at.isoformat() if next_run_at else None,
        "next_run_in_minutes": next_run_in_minutes,
        "last_hint": state.last_schedule_hint or None,
        "last_run_at": state.last_run_at.isoformat() if state.last_run_at else None,
        "last_error": state.last_error,
    }

    # ── Fixed jobs ──
    schedule = trading_scheduler._market_schedule_profile(market_code)
    mkt_cfg = settings.get_market_config(market_code)
    pre_h, pre_m = schedule.prep_time.hour, schedule.prep_time.minute
    open_h, open_m = schedule.open_scan_time.hour, schedule.open_scan_time.minute
    force_h = mkt_cfg["force_liquidation_hour"]
    force_m = mkt_cfg["force_liquidation_minute"]
    post_h, post_m = (16, 10) if is_us else (15, 40)
    sync_h, sync_m = (16, 30) if is_us else (16, 0)
    data_h, data_m = (17, 0) if is_us else (16, 30)
    holdings_range = trading_scheduler._holdings_check_hours(market_code)

    job_defs = [
        ("prep", "장 시작 전 준비", pre_h, pre_m),
        ("scan", "장 시작 스캔 + 매매", open_h, open_m),
        ("holdings", f"보유종목 점검 ({holdings_range}시 매 :30)", None, 30),
        ("liquidation", "장 마감 전 청산", force_h, force_m),
        ("review", "장 마감 성과 리뷰", post_h, post_m),
        ("sync", "포트폴리오 정산", sync_h, sync_m),
        ("data", "일봉 데이터 수집", data_h, data_m),
    ]

    current_hm = now_local.hour * 60 + now_local.minute
    found_next = False
    fixed_jobs = []
    for category, name, h, m in job_defs:
        if h is None:
            # holdings_check: 범위 기반, 첫/끝 시간으로 상태 판정
            parts = holdings_range.split("-")
            start_h, end_h = int(parts[0]), int(parts[1])
            job_start_hm = start_h * 60 + 30
            job_end_hm = end_h * 60 + 30
            if current_hm > job_end_hm:
                status = "done"
            elif current_hm >= job_start_hm and not found_next:
                status = "next"
                found_next = True
            else:
                status = "upcoming" if found_next else "next"
                if status == "next":
                    found_next = True
            time_str = f"{start_h:02d}:30~{end_h:02d}:30"
        else:
            job_hm = h * 60 + m
            if current_hm > job_hm + 5:
                status = "done"
            elif not found_next:
                status = "next"
                found_next = True
            else:
                status = "upcoming"
            time_str = f"{h:02d}:{m:02d}"

        fixed_jobs.append({
            "category": category,
            "name": name,
            "time": time_str,
            "status": status,
        })

    return SuccessResponse(data={
        "market": market_code,
        "market_scope": scope,
        "adaptive": adaptive_data,
        "fixed_jobs": fixed_jobs,
        "server_time_local": now_local.isoformat(),
    })


# ── 에이전트 파이프라인 상태 ──
@router.get("/agent/state")
async def get_agent_state():
    """현재 에이전트 파이프라인 상태 (인메모리 조회, DB 없음)"""
    from agent.trading_agent import trading_agent

    result = {}
    scopes = ["KRX", "US"]
    if settings.CRYPTO_ENABLED:
        scopes.append("CRYPTO")
    for scope in scopes:
        state = trading_agent._market_states.get(scope)
        if not state:
            result[scope] = {
                "cycle_active": False,
                "cycle_id": None,
                "started_at": None,
                "phase": None,
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
                "phase": pipeline.get("phase"),
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

    async def _run_manual_cycle() -> None:
        from services.watchlist_sync import reconcile_market_watchlist

        result = await trading_agent.run_cycle(market=market_code)
        if result.get("skipped"):
            logger.info("수동 사이클 스킵 ({}): {}", market_code, result.get("reason", "skipped"))
            return
        await reconcile_market_watchlist(market_code)

    # 비동기로 실행 (즉시 응답)
    task = asyncio.create_task(_run_manual_cycle())

    def _log_cycle_task_result(done_task: asyncio.Task) -> None:
        if done_task.cancelled():
            logger.warning("수동 사이클 태스크 취소: {}", market_code)
            return
        try:
            exc = done_task.exception()
        except Exception as e:
            logger.error("수동 사이클 태스크 확인 실패: {}", str(e))
            return
        if exc is not None:
            logger.error("수동 사이클 비동기 실행 실패 ({}): {}", market_code, str(exc))

    task.add_done_callback(_log_cycle_task_result)
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
    refresh: bool = Query(False),
):
    """수동 일일 리포트 생성 (refresh=True 시 기존 리포트와 비교)"""
    from services.daily_report_service import daily_report_service

    d = date.fromisoformat(target_date) if target_date else None
    scope = market_scope or settings.primary_market_code

    if refresh:
        result = await daily_report_service.regenerate_daily_report(d, market_scope=scope)
        existing_resp = (
            DailyReportResponse.model_validate(result["existing"])
            if result["existing"]
            else None
        )
        refreshed_resp = DailyReportResponse.model_validate(result["refreshed"])
        return SuccessResponse(
            data=ReportComparisonResponse(
                existing=existing_resp,
                refreshed=refreshed_resp,
                recommendation=result["recommendation"],
                comparison=result["comparison"],
            ),
            message="리포트 비교 완료",
        )

    report = await daily_report_service.generate_daily_report(d, market_scope=scope)
    if report:
        return SuccessResponse(
            data=DailyReportResponse.model_validate(report),
            message="리포트 생성 완료",
        )
    return SuccessResponse(message="리포트 생성 실패 또는 이미 존재")


@router.post("/reports/confirm-refresh")
async def confirm_refresh_report(
    payload: ReportRefreshConfirmRequest | None = Body(None),
    report_date: str | None = Query(None),
    market_scope: str | None = Query(None),
    choice: str | None = Query(None, description="'existing' or 'refreshed'"),
    db: AsyncSession = Depends(get_async_db),
):
    """리포트 갱신 확정 — refreshed 선택 시 DB 저장, existing 선택 시 기존 유지"""
    from services.daily_report_service import daily_report_service

    resolved_report_date = payload.report_date.isoformat() if payload else report_date
    resolved_market_scope = payload.market_scope if payload else market_scope
    resolved_choice = payload.choice if payload else choice
    if not resolved_report_date or not resolved_market_scope or not resolved_choice:
        return SuccessResponse(data=None, message="report_date, market_scope, choice는 필수입니다")

    d = date.fromisoformat(resolved_report_date)
    scope = normalize_market_scope(resolved_market_scope)

    if resolved_choice == "existing":
        repo = DailyReportRepository(db)
        existing = await repo.get_by_date(d, market_scope=scope)
        if existing:
            return SuccessResponse(
                data=DailyReportResponse.model_validate(existing),
                message="기존 리포트 유지",
            )
        return SuccessResponse(data=None, message="기존 리포트 없음")

    if resolved_choice == "refreshed":
        # 새 데이터를 다시 생성하여 저장
        result = await daily_report_service.regenerate_daily_report(d, market_scope=scope)
        refreshed = result["refreshed"]

        # DailyReport 객체에서 저장할 필드 추출
        refreshed_data = {
            "total_cycles": refreshed.total_cycles,
            "total_analyses": refreshed.total_analyses,
            "total_recommendations": refreshed.total_recommendations,
            "total_orders": refreshed.total_orders,
            "buy_count": refreshed.buy_count,
            "sell_count": refreshed.sell_count,
            "win_count": refreshed.win_count,
            "loss_count": refreshed.loss_count,
            "total_pnl": refreshed.total_pnl,
            "unrealized_pnl": refreshed.unrealized_pnl,
            "open_position_count": refreshed.open_position_count,
            "market_summary": refreshed.market_summary,
            "performance_review": refreshed.performance_review,
            "lessons_learned": refreshed.lessons_learned,
            "next_day_plan": refreshed.next_day_plan,
            "top_picks": refreshed.top_picks,
            "strategy_stats": refreshed.strategy_stats,
        }

        report = await daily_report_service.apply_refreshed_report(
            report_date=d,
            market_scope=scope,
            refreshed_data=refreshed_data,
        )
        return SuccessResponse(
            data=DailyReportResponse.model_validate(report),
            message="갱신된 리포트 저장 완료",
        )

    return SuccessResponse(data=None, message=f"잘못된 choice 값: {resolved_choice}")
