"""EventMixin: 실시간 이벤트 핸들러 (VOLUME_SPIKE, PRICE_SURGE/DROP, STOP_LOSS, TAKE_PROFIT)"""

from loguru import logger

from agent.decision_maker import decision_maker
from core.config import settings
from core.events import Event
from realtime.event_detector import event_detector
from scheduler.market_calendar import market_calendar
from services.activity_logger import activity_logger
from trading.enums import ActivityPhase, ActivityType
from trading.market_profile import is_crypto_market, market_scope, market_timezone, normalize_market
from trading.mcp_client import mcp_client
from trading.quantity_policy import format_quantity_with_unit


class EventMixin:
    """실시간 이벤트 핸들러 Mixin"""

    async def _on_market_event(self, event: Event) -> None:
        """실시간 시장 이벤트 → 즉시 해당 종목 분석/매매"""
        if not self._running:
            return

        symbol = event.data.get("symbol", "")
        market_code = normalize_market(event.data.get("market", settings.primary_market_code))
        scope = market_scope(market_code)
        event_state = self._get_state(scope)
        trading_date = self._refresh_runtime_date(event_state, scope)

        # 장마감 청산 완료 후 이벤트 분석 차단
        if event_state.liquidation_complete:
            return

        # 데이트레이딩 또는 프리마켓 단타: 매수 마감 시간 이후 신규 매수 이벤트 무시
        session = market_calendar.get_market_session(market=market_code)
        if settings.should_enforce_buy_cutoff(market_code, session=session):
            from datetime import time as _dt_time
            from util.time_util import now_kst
            from zoneinfo import ZoneInfo
            mkt_cfg = settings.get_market_config(market_code, session=session)
            cutoff = _dt_time(mkt_cfg["buy_cutoff_hour"], mkt_cfg["buy_cutoff_minute"])
            market_now = now_kst().astimezone(ZoneInfo(market_timezone(market_code)))
            if market_now.time() >= cutoff:
                return
        if not symbol:
            return

        # 쿨다운 체크 (동일 종목 연속 분석 방지)
        import time as _time
        now_ts = _time.time()
        cooldown_key = f"{market_code}:{symbol}"
        last_ts = self._cooldowns.get(cooldown_key, 0)
        if now_ts - last_ts < self.EVENT_COOLDOWN_SEC:
            return
        if cooldown_key in self._analyzing:
            return

        self._cooldowns[cooldown_key] = now_ts
        self._analyzing.add(cooldown_key)

        price = event.data.get("price", 0)
        change_rate = event.data.get("change_rate", 0)
        event_type = event.type.value

        with activity_logger.context(market_scope=scope, trading_date=trading_date):
            await activity_logger.log(
                ActivityType.EVENT, ActivityPhase.PROGRESS,
                f"\u26a1 실시간 감지: {event_type} - {symbol} "
                f"({price:,.0f}원, {change_rate:+.2f}%)",
                symbol=symbol,
                detail=event.data,
            )

            # 즉시 분석 + 매매 (비동기)
            try:
                # 실시간 이벤트에서도 트레이딩 컨텍스트 갱신
                event_state.trading_context = await self._build_trading_context(market_code)

                product_metadata = self._get_product_metadata(symbol, market_code)
                stock_info = {
                    "symbol": symbol,
                    "name": product_metadata.get("name") or event.data.get("name", symbol),
                    "market": market_code,
                    "strategy_type": "AGGRESSIVE_SHORT" if abs(change_rate) >= 5 else "STABLE_SHORT",
                    "analysis_source": "event",
                    "event_type": event_type,
                    "trigger": event_type,
                    "event_price": price,
                    "event_change_rate": change_rate,
                    **product_metadata,
                }
                cycle_id = activity_logger.start_cycle()

                # 포트폴리오 스냅샷 조회 (리스크 체크용, MCP 1회)
                snapshot = {
                    "cash": 0,
                    "total_asset": 0,
                    "holding_count": 0,
                    "holding_symbols": [],
                    "holding_positions": {},
                    "today_trade_count": 0,
                }
                balance = None
                try:
                    from trading.account_manager import account_manager

                    balance, holdings = await account_manager.get_account_snapshot(market_code)
                    if not balance.is_valid:
                        logger.error("실시간 이벤트: 계좌 조회 실패 → 분석 중단")
                        return
                    async with event_state.cash_lock:
                        event_state.available_cash = balance.effective_cash
                        snapshot["cash"] = event_state.available_cash
                    snapshot["cash_foreign"] = balance.cash_foreign
                    snapshot["effective_cash_foreign"] = balance.effective_cash_foreign
                    snapshot["total_asset"] = balance.total_asset
                    snapshot["total_asset_foreign"] = balance.total_asset_foreign
                    snapshot["holding_count"] = len(holdings)
                    holding_symbols, holding_positions = self._build_holding_snapshot(
                        holdings,
                        balance.total_asset,
                    )
                    snapshot["holding_symbols"] = holding_symbols
                    snapshot["holding_positions"] = holding_positions
                    snapshot["today_trade_count"] = await self._get_today_trade_count(scope)
                    for holding in holdings:
                        self._remember_product_metadata(
                            holding.symbol,
                            holding.market,
                            {"name": holding.name},
                        )
                except Exception as e:
                    logger.warning("실시간 이벤트 포트폴리오 스냅샷 조회 실패: {}", str(e))

                dynamic_limits = None
                if settings.AI_RISK_TUNING_ENABLED:
                    try:
                        from strategy.ai_risk_tuner import ai_risk_tuner

                        dynamic_limits = await ai_risk_tuner.compute_limits(
                            market=market_code,
                            risk_appetite=settings.risk_appetite_for_market(market_code),
                            cycle_id=cycle_id,
                            balance=balance,
                        )
                    except Exception:
                        pass

                result = await self._analyze_and_trade(
                    stock_info,
                    cycle_id,
                    dynamic_limits=dynamic_limits,
                    portfolio_snapshot=snapshot,
                )
                if result.get("executed"):
                    logger.info("실시간 매매 실행: {} ({})", symbol, event_type)
                    order_amount = result.get("order_amount", 0)
                    if order_amount > 0:
                        async with event_state.cash_lock:
                            event_state.available_cash -= order_amount
                            logger.debug(
                                "[{}] 실시간 주문 {:,.0f}원 차감 → 잔여 현금 {:,.0f}원",
                                symbol,
                                order_amount,
                                event_state.available_cash,
                            )
                    if not is_crypto_market(market_code):
                        try:
                            from services.watchlist_sync import reconcile_market_watchlist

                            await reconcile_market_watchlist(market_code)
                        except Exception as e:
                            logger.warning("[{}] 실시간 주문 후 감시종목 재동기화 실패: {}", market_code, str(e))
                # 신규 매수 종목 WebSocket 구독 추가
                await self._ensure_realtime_subscription(symbol, market=market_code)
            except Exception as e:
                logger.error("실시간 분석 오류 ({}): {}", symbol, str(e))
            finally:
                self._analyzing.discard(cooldown_key)

    async def _on_stop_loss(self, event: Event) -> None:
        """손절선 도달 → 즉시 매도"""
        if not self._running:
            return
        symbol = event.data.get("symbol", "")
        market_code = normalize_market(event.data.get("market", settings.primary_market_code))
        scope = market_scope(market_code)
        trading_date = market_calendar.market_date(market=scope)
        price = event.data.get("price", 0)
        stop_loss = event.data.get("stop_loss_price", 0)

        with activity_logger.context(market_scope=scope, trading_date=trading_date):
            logger.warning("손절선 도달: {} (현재가: {:,.0f}, 손절: {:,.0f})", symbol, price, stop_loss)
            await activity_logger.log(
                ActivityType.EVENT, ActivityPhase.PROGRESS,
                f"\U0001f6a8 손절선 도달: {symbol} — 즉시 매도 실행 "
                f"(현재가: {price:,.0f}원, 손절: {stop_loss:,.0f}원)",
                symbol=symbol,
                detail=event.data,
            )
            if settings.is_trading_enabled_for_market(market_code):
                try:
                    from trading.account_manager import account_manager
                    holdings = await account_manager.get_holdings(market_code)
                    holding = next((h for h in holdings if h.symbol == symbol and h.market == market_code), None)
                    if holding and holding.quantity > 0:
                        resp = await mcp_client.place_order(
                            symbol=symbol, side="SELL",
                            quantity=holding.quantity, price=None, market=market_code,
                        )
                        await activity_logger.log(
                            ActivityType.ORDER, ActivityPhase.COMPLETE,
                            f"\U0001f6a8 손절 매도: {symbol} {format_quantity_with_unit(holding.quantity, market_code)} "
                            f"({'성공' if resp.success else '실패: ' + (resp.error or '')})",
                            symbol=symbol,
                        )
                        if resp.success:
                            event_detector.remove_levels(symbol, market=market_code)
                            order_data = resp.data or {}
                            order_id = order_data.get("order_id", "")
                            await decision_maker.confirm_and_record(
                                symbol=symbol,
                                market=market_code,
                                side="SELL",
                                order_id=order_id,
                                quantity=holding.quantity,
                                expected_price=price,
                                exit_reason="STOP_LOSS",
                            )
                except Exception as e:
                    logger.error("손절 매도 실패 ({}): {}", symbol, str(e))

    async def _on_take_profit(self, event: Event) -> None:
        """익절선 도달 → 즉시 매도"""
        if not self._running:
            return
        symbol = event.data.get("symbol", "")
        market_code = normalize_market(event.data.get("market", settings.primary_market_code))
        scope = market_scope(market_code)
        trading_date = market_calendar.market_date(market=scope)
        price = event.data.get("price", 0)
        take_profit = event.data.get("take_profit_price", 0)

        with activity_logger.context(market_scope=scope, trading_date=trading_date):
            logger.info("익절선 도달: {} (현재가: {:,.0f}, 익절: {:,.0f})", symbol, price, take_profit)
            await activity_logger.log(
                ActivityType.EVENT, ActivityPhase.PROGRESS,
                f"\U0001f3af 익절선 도달: {symbol} — 매도 실행 "
                f"(현재가: {price:,.0f}원, 익절: {take_profit:,.0f}원)",
                symbol=symbol,
                detail=event.data,
            )
            if settings.is_trading_enabled_for_market(market_code):
                try:
                    from trading.account_manager import account_manager
                    holdings = await account_manager.get_holdings(market_code)
                    holding = next((h for h in holdings if h.symbol == symbol and h.market == market_code), None)
                    if holding and holding.quantity > 0:
                        resp = await mcp_client.place_order(
                            symbol=symbol, side="SELL",
                            quantity=holding.quantity, price=None, market=market_code,
                        )
                        await activity_logger.log(
                            ActivityType.ORDER, ActivityPhase.COMPLETE,
                            f"\U0001f3af 익절 매도: {symbol} {format_quantity_with_unit(holding.quantity, market_code)} "
                            f"({'성공' if resp.success else '실패: ' + (resp.error or '')})",
                            symbol=symbol,
                        )
                        if resp.success:
                            event_detector.remove_levels(symbol, market=market_code)
                            order_data = resp.data or {}
                            order_id = order_data.get("order_id", "")
                            await decision_maker.confirm_and_record(
                                symbol=symbol,
                                market=market_code,
                                side="SELL",
                                order_id=order_id,
                                quantity=holding.quantity,
                                expected_price=price,
                                exit_reason="TAKE_PROFIT",
                            )
                except Exception as e:
                    logger.error("익절 매도 실패 ({}): {}", symbol, str(e))

    async def _ensure_realtime_subscription(self, symbol: str, market: str | None = None) -> None:
        """매수 후 WebSocket 실시간 구독 확인/추가"""
        try:
            market_code = normalize_market(market or settings.primary_market_code)
            if is_crypto_market(market_code):
                from realtime.coin_stream_manager import coin_stream_manager

                await coin_stream_manager.ensure_symbol(market_scope(market_code), symbol, market_code)
            else:
                from realtime.stream_manager import stream_manager

                await stream_manager.ensure_symbol(market_scope(market_code), symbol, market_code)
            logger.debug("매수 종목 WebSocket 구독 추가: {} ({})", symbol, market_code)
        except Exception as e:
            logger.warning("WebSocket 구독 추가 실패 ({}): {}", symbol, str(e))
