"""시장별 실시간 감시 종목 동기화."""
from loguru import logger

from trading.market_profile import is_crypto_market, markets_for_scope, normalize_market


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_selected_watchlist(
    market: str,
    selected_watchlist: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in selected_watchlist or []:
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        market_code = normalize_market(item.get("market") or market)
        key = (market_code, symbol)
        if key in seen:
            continue
        seen.add(key)
        enriched: dict[str, object] = {
            "symbol": symbol,
            "market": market_code,
            "name": str(item.get("name", "") or ""),
        }
        for meta_key in (
            "scan_source",
            "price",
            "price_krw",
            "currency",
            "exchange_rate_to_krw",
            "change_rate",
            "volume",
            "trade_value",
            "strategy_type",
            "reason",
            "roadmap_stage",
            "roadmap_anchor_price",
            "roadmap_invalid_price",
            "roadmap_take_profit_price",
        ):
            value = item.get(meta_key)
            if value not in (None, ""):
                enriched[meta_key] = value
        normalized.append(enriched)
    return normalized


def _prune_unaffordable_selected_watchlist(
    market: str,
    selected_watchlist: list[dict[str, object]],
    effective_cash: float,
    holding_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, object]], list[tuple[str, str]], int]:
    """미보유 selected 종목 중 1주 매수 불가 종목을 제거한다."""
    kept: list[dict[str, object]] = []
    removed: list[tuple[str, str]] = []
    missing_price = 0

    for item in selected_watchlist:
        symbol = str(item.get("symbol", "")).upper().strip()
        item_market = normalize_market(item.get("market") or market)
        key = (item_market, symbol)
        if not symbol:
            continue
        if key in holding_keys:
            kept.append(item)
            continue

        price_krw = _to_float(item.get("price_krw"), 0.0)
        if price_krw <= 0:
            missing_price += 1
            kept.append(item)
            continue
        if price_krw > effective_cash:
            removed.append(key)
            continue
        kept.append(item)

    return kept, removed, missing_price


def _normalize_symbol_pairs(
    market: str,
    symbols: list[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for symbol, item_market in symbols or []:
        normalized_symbol = str(symbol or "").upper().strip()
        if not normalized_symbol:
            continue
        market_code = normalize_market(item_market or market)
        key = (market_code, normalized_symbol)
        if key in seen:
            continue
        seen.add(key)
        normalized.append((normalized_symbol, market_code))
    return normalized


def _gc_stale_thresholds(
    market: str,
    retained_keys: set[tuple[str, str]],
) -> int:
    from realtime.event_detector import event_detector

    removed = 0
    scope_markets = set(markets_for_scope(market))
    for instrument_key in list(event_detector.monitored_symbols):
        instrument_market, _, symbol = instrument_key.partition(":")
        if not symbol or instrument_market not in scope_markets:
            continue
        if (instrument_market, symbol) in retained_keys:
            continue
        event_detector.remove_levels(symbol, market=instrument_market)
        removed += 1
    return removed


async def reconcile_market_watchlist(
    market: str,
    selected_watchlist: list[dict[str, object]] | None = None,
) -> list[tuple[str, str]]:
    """최근 선정 종목과 보유 종목을 합쳐 scope별 desired 구독을 재계산."""
    from agent.trading_agent import trading_agent
    from trading.account_manager import account_manager

    market_code = normalize_market(market)
    runtime = trading_agent.get_runtime(market_code)
    if selected_watchlist is not None:
        runtime.last_selected_watchlist = _normalize_selected_watchlist(
            market_code,
            selected_watchlist,
        )

    desired_symbols: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    holdings = []

    if is_crypto_market(market_code):
        holdings = await account_manager.get_holdings(market_code)
        from realtime.coin_stream_manager import coin_stream_manager

        for item in runtime.last_selected_watchlist:
            symbol = str(item.get("symbol", "")).upper()
            item_market = normalize_market(item.get("market") or market_code)
            key = (item_market, symbol)
            if not symbol or key in seen:
                continue
            seen.add(key)
            desired_symbols.append((symbol, item_market))

        for holding in holdings:
            symbol = str(holding.symbol or "").upper()
            if not symbol:
                continue
            holding_market = normalize_market(holding.market or market_code)
            key = (holding_market, symbol)
            if key in seen:
                continue
            seen.add(key)
            desired_symbols.append((symbol, holding_market))

        synced_symbols = desired_symbols
        await coin_stream_manager.replace_market_subscriptions(market_code, synced_symbols)
    else:
        removed_keys: list[tuple[str, str]] = []
        missing_price_count = 0
        effective_cash: float | None = None

        try:
            balance, holdings = await account_manager.get_account_snapshot(market_code)
            if balance.is_valid:
                effective_cash = max(_to_float(balance.effective_cash), 0.0)
                runtime_cash = _to_float(getattr(runtime, "available_cash", 0.0), 0.0)
                if runtime_cash > 0:
                    effective_cash = min(effective_cash, runtime_cash) if effective_cash > 0 else runtime_cash
            else:
                logger.warning("[{}] 실시간 감시 현금 기준 필터 스킵 — 무효한 잔고 스냅샷", market_code)
        except Exception as e:
            logger.warning("[{}] 실시간 감시 현금 기준 필터 스킵 — 계좌 스냅샷 조회 실패: {}", market_code, str(e))
            try:
                holdings = await account_manager.get_holdings(market_code)
            except Exception as holding_error:
                logger.warning("[{}] 실시간 감시 동기화 보유종목 조회 실패: {}", market_code, str(holding_error))
                holdings = []

        holding_keys = {
            (normalize_market(getattr(holding, "market", None) or market_code), str(holding.symbol or "").upper())
            for holding in holdings
            if getattr(holding, "symbol", None)
        }
        if effective_cash is not None:
            runtime.last_selected_watchlist, removed_keys, missing_price_count = _prune_unaffordable_selected_watchlist(
                market_code,
                runtime.last_selected_watchlist,
                effective_cash,
                holding_keys,
            )
            if removed_keys:
                from realtime.event_detector import event_detector

                for item_market, symbol in removed_keys:
                    event_detector.remove_levels(symbol, market=item_market)
                logger.info(
                    "[{}] 가용 현금 기준 감시 제외: {}종목 제거 (현금 {:,.0f}원)",
                    market_code,
                    len(removed_keys),
                    effective_cash,
                )
            if missing_price_count:
                logger.debug(
                    "[{}] 가용 현금 기준 필터 fail-open 유지: price_krw 미기록 {}종목",
                    market_code,
                    missing_price_count,
                )

        for item in runtime.last_selected_watchlist:
            symbol = str(item.get("symbol", "")).upper()
            item_market = normalize_market(item.get("market") or market_code)
            key = (item_market, symbol)
            if not symbol or key in seen:
                continue
            seen.add(key)
            desired_symbols.append((symbol, item_market))

        for holding in holdings:
            symbol = str(holding.symbol or "").upper()
            if not symbol:
                continue
            holding_market = normalize_market(holding.market or market_code)
            key = (holding_market, symbol)
            if key in seen:
                continue
            seen.add(key)
            desired_symbols.append((symbol, holding_market))

        from realtime.stream_manager import stream_manager

        synced_symbols = desired_symbols[:41]
        await stream_manager.replace_market_subscriptions(market_code, synced_symbols)
        retained_keys = {
            (item_market, symbol)
            for symbol, item_market in synced_symbols
        } | holding_keys
        removed_thresholds = _gc_stale_thresholds(
            market_code,
            retained_keys,
        )
        if removed_thresholds:
            logger.info(
                "[{}] 실시간 감시 stale threshold 정리: {}건",
                market_code,
                removed_thresholds,
            )

    logger.info("[{}] 실시간 감시 종목 동기화: {}종목", market_code, len(synced_symbols))
    return synced_symbols


async def cleanup_post_market_stock_watchlist(
    market: str,
    retained_symbols: list[tuple[str, str]] | None = None,
) -> list[tuple[str, str]] | None:
    """장후에는 실제 보유 종목만 남기고 최근 선정 감시 종목은 정리한다."""
    market_code = normalize_market(market)
    if is_crypto_market(market_code):
        return []

    if retained_symbols is None:
        from trading.account_manager import account_manager

        try:
            holdings = await account_manager.get_holdings(market_code)
        except Exception as e:
            logger.warning("[{}] 장후 감시 정리 스킵 — 보유종목 조회 실패: {}", market_code, str(e))
            return None

        retained_symbols = [
            (str(holding.symbol or "").upper(), normalize_market(holding.market or market_code))
            for holding in holdings
            if getattr(holding, "quantity", 0) > 0 and getattr(holding, "symbol", None)
        ]

    synced_symbols = _normalize_symbol_pairs(market_code, retained_symbols)

    from agent.trading_agent import trading_agent
    from realtime.stream_manager import stream_manager

    await stream_manager.replace_market_subscriptions(market_code, synced_symbols)

    runtime = trading_agent.get_runtime(market_code)
    cleared_selected = len(runtime.last_selected_watchlist)
    runtime.last_selected_watchlist = []

    retained_keys = {(item_market, symbol) for symbol, item_market in synced_symbols}
    removed_thresholds = _gc_stale_thresholds(market_code, retained_keys)

    logger.info(
        "[{}] 장후 감시 정리: 보유 유지 {}종목, selected 초기화 {}건, threshold 제거 {}건",
        market_code,
        len(synced_symbols),
        cleared_selected,
        removed_thresholds,
    )
    return synced_symbols
