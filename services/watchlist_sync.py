"""시장별 실시간 감시 종목 동기화."""
from loguru import logger

from trading.market_profile import normalize_market


def _normalize_selected_watchlist(
    market: str,
    selected_watchlist: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
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
        normalized.append({
            "symbol": symbol,
            "market": market_code,
            "name": str(item.get("name", "") or ""),
        })
    return normalized


async def reconcile_market_watchlist(
    market: str,
    selected_watchlist: list[dict[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """최근 선정 종목과 보유 종목을 합쳐 scope별 desired 구독을 재계산."""
    from agent.trading_agent import trading_agent
    from realtime.stream_manager import stream_manager
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

    desired_symbols = desired_symbols[:41]
    await stream_manager.replace_market_subscriptions(market_code, desired_symbols)
    logger.info("[{}] 실시간 감시 종목 동기화: {}종목", market_code, len(desired_symbols))
    return desired_symbols
