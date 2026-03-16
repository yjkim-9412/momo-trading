"""시장별 실시간 감시 종목 동기화."""
from loguru import logger

from trading.market_profile import is_crypto_market, markets_for_scope, normalize_market


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
            "change_rate",
            "volume",
            "trade_value",
            "strategy_type",
            "reason",
        ):
            value = item.get(meta_key)
            if value not in (None, ""):
                enriched[meta_key] = value
        normalized.append(enriched)
    return normalized


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

    for item in runtime.last_selected_watchlist:
        symbol = str(item.get("symbol", "")).upper()
        item_market = normalize_market(item.get("market") or market_code)
        key = (item_market, symbol)
        if not symbol or key in seen:
            continue
        seen.add(key)
        desired_symbols.append((symbol, item_market))

    holdings = await account_manager.get_holdings(market_code)
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

    if is_crypto_market(market_code):
        from realtime.coin_stream_manager import coin_stream_manager

        synced_symbols = desired_symbols
        await coin_stream_manager.replace_market_subscriptions(market_code, synced_symbols)
    else:
        from realtime.stream_manager import stream_manager

        synced_symbols = desired_symbols[:41]
        await stream_manager.replace_market_subscriptions(market_code, synced_symbols)

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
    from realtime.event_detector import event_detector
    from realtime.stream_manager import stream_manager

    await stream_manager.replace_market_subscriptions(market_code, synced_symbols)

    runtime = trading_agent.get_runtime(market_code)
    cleared_selected = len(runtime.last_selected_watchlist)
    runtime.last_selected_watchlist = []

    retained_keys = {(item_market, symbol) for symbol, item_market in synced_symbols}
    scope_markets = set(markets_for_scope(market_code))
    removed_thresholds = 0
    for instrument_key in list(event_detector.monitored_symbols):
        instrument_market, _, symbol = instrument_key.partition(":")
        if not symbol or instrument_market not in scope_markets:
            continue
        if (instrument_market, symbol) in retained_keys:
            continue
        event_detector.remove_levels(symbol, market=instrument_market)
        removed_thresholds += 1

    logger.info(
        "[{}] 장후 감시 정리: 보유 유지 {}종목, selected 초기화 {}건, threshold 제거 {}건",
        market_code,
        len(synced_symbols),
        cleared_selected,
        removed_thresholds,
    )
    return synced_symbols
