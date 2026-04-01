"""시장 스캔 + 종목 선별 통합 — MCP 데이터 병렬 수집 → AI 한 번에 분석+선별"""
import asyncio

from loguru import logger

from core.config import settings
from analysis.feedback.performance_tracker import PerformanceTracker
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.market_scan import (
    get_market_label,
    get_market_scan_prompt,
    get_market_scan_system,
)
from core.database import AsyncSessionLocal
from scheduler.market_calendar import market_calendar
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType, Tier1Profile
from trading.market_profile import is_us_market, market_scope, normalize_market
from trading.mcp_client import mcp_client
from trading.product_policy import (
    classify_product,
    coerce_strategy_for_product,
    is_product_trade_allowed,
)

# 모의투자 매매불가 종목 필터 키워드
_EXCLUDE_NAME_KEYWORDS = ("ETN", "스팩", "SPAC")
_US_BASE_SCAN_LIMIT = 30
_US_EXPANDED_SCAN_LIMIT = 60
_US_DETAIL_ENRICH_MIN_LIMIT = 24
_US_DETAIL_ENRICH_MULTIPLIER = 4


class MarketScanner:
    """
    MCP를 통해 시장 데이터 수집 → AI가 시장 국면 판단 + 최종 종목 선별을 한 번에 수행.
    (기존 scan → screening 2단계를 1단계로 통합하여 LLM 호출 1건 절약)
    """

    def __init__(self):
        self._untradeable_symbols: dict[str, set[str]] = {}

    def add_untradeable(self, symbol: str, market: str = "KRX") -> None:
        """매매불가 종목을 런타임 블록리스트에 등록 (당일 스캔에서 제외)"""
        m = normalize_market(market)
        self._untradeable_symbols.setdefault(m, set()).add(symbol)
        logger.info(
            "매매불가 블록리스트 등록: {}:{} (총 {}건)",
            m, symbol, sum(len(v) for v in self._untradeable_symbols.values()),
        )

    def _filter_untradeable(self, stocks: list[dict], market: str = "KRX") -> list[dict]:
        """매매불가 종목 필터링 (런타임 블록리스트 + 이름 키워드)

        Each stock's own ``market`` field is checked first; ``market`` param is the fallback.
        """
        filtered = []
        for s in stocks:
            name = s.get("name", "")
            symbol = s.get("symbol", "")
            item_market = normalize_market(s.get("market", market))
            blocked = self._untradeable_symbols.get(item_market, set())
            if symbol in blocked:
                continue
            if any(kw in name for kw in _EXCLUDE_NAME_KEYWORDS):
                continue
            filtered.append(s)
        if len(filtered) < len(stocks):
            logger.info("매매불가 종목 필터: {}건 → {}건", len(stocks), len(filtered))
        return filtered

    def _apply_product_policy(self, selected: list[dict], *, log_summary: bool = True) -> list[dict]:
        """레버리지/인버스 상품 정책 적용"""
        filtered: list[dict] = []
        dropped = 0
        adjusted = 0

        for item in selected:
            symbol = item.get("symbol", "")
            market_code = normalize_market(item.get("market", settings.primary_market_code))
            classification = classify_product(
                symbol=symbol,
                market=market_code,
                name=item.get("name", ""),
                category=item.get("category", ""),
            )
            strategy_type = str(item.get("strategy_type") or "STABLE_SHORT").upper()
            effective_strategy = coerce_strategy_for_product(strategy_type, classification)
            enriched = {
                **item,
                "market": market_code,
                "strategy_type": effective_strategy or strategy_type,
                **classification.to_metadata(),
            }

            allowed, reason = is_product_trade_allowed(
                classification,
                strategy_type=effective_strategy or strategy_type,
                session=market_calendar.get_market_session(market=market_code),
            )
            if not allowed:
                dropped += 1
                logger.info("상품 정책 제외: {} {} - {}", market_code, symbol, reason)
                continue

            if effective_strategy and effective_strategy != strategy_type:
                adjusted += 1
                logger.info(
                    "상품 정책 전략 조정: {} {} {} → {}",
                    market_code, symbol, strategy_type, effective_strategy,
                )
                enriched["strategy_type"] = effective_strategy

            filtered.append(enriched)

        if log_summary and (dropped or adjusted):
            logger.info(
                "상품 정책 적용: {}건 → {}건 (제외 {}건, 전략 조정 {}건)",
                len(selected), len(filtered), dropped, adjusted,
            )
        return filtered

    @staticmethod
    def _to_float(value: object, default: float = 0.0) -> float:
        """숫자 변환 실패 시 기본값 반환"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    async def _build_affordability_fx_rates(self, stocks: list[dict]) -> dict[str, float]:
        """해외 종목 KRW 환산에 필요한 환율 캐시 조회"""
        markets = {
            normalize_market(item.get("market", settings.primary_market_code))
            for item in stocks
            if is_us_market(item.get("market", settings.primary_market_code))
        }
        rates: dict[str, float] = {}
        for market_code in sorted(markets):
            rates[market_code] = float(await mcp_client._get_exchange_rate_to_krw(market_code) or 1.0)
        return rates

    def _resolve_price_krw(self, item: dict, fx_rates: dict[str, float]) -> float:
        """종목 현재가를 KRW 기준으로 정규화"""
        price = self._to_float(item.get("price", item.get("current_price", 0.0)))
        if price <= 0:
            return 0.0

        market_code = normalize_market(item.get("market", settings.primary_market_code))
        currency = str(item.get("currency") or ("USD" if is_us_market(market_code) else "KRW")).upper()
        if currency == "KRW":
            return price

        exchange_rate = self._to_float(item.get("exchange_rate_to_krw"), 0.0)
        if exchange_rate <= 0:
            exchange_rate = self._to_float(fx_rates.get(market_code), 0.0)
        if exchange_rate <= 0:
            return 0.0
        return price * exchange_rate

    def _resolve_available_cash_foreign(
        self,
        market: str,
        balance,
    ) -> float | None:
        """미국장 스캔용 실주문 기준 현금을 USD 기준으로 정리한다."""
        market_code = normalize_market(market)
        if not is_us_market(market_code):
            return None

        effective_cash_foreign = self._to_float(getattr(balance, "effective_cash_foreign", 0.0), 0.0)
        if effective_cash_foreign > 0:
            return effective_cash_foreign

        cash_foreign = self._to_float(getattr(balance, "cash_foreign", 0.0), 0.0)
        if cash_foreign > 0:
            return cash_foreign

        return None

    def _is_affordable_stock(
        self,
        item: dict,
        *,
        available_cash_krw: float,
        available_cash_foreign: float | None,
        fx_rates: dict[str, float],
    ) -> tuple[bool, float]:
        """종목이 현재 현금으로 1주 매수 가능한지 판단한다."""
        price = self._to_float(item.get("price", item.get("current_price", 0.0)))
        market_code = normalize_market(item.get("market", settings.primary_market_code))
        currency = str(item.get("currency") or ("USD" if is_us_market(market_code) else "KRW")).upper()

        if price <= 0:
            return False, 0.0

        price_krw = self._resolve_price_krw(item, fx_rates)
        if price_krw <= 0:
            return False, 0.0

        if is_us_market(market_code) and currency != "KRW" and available_cash_foreign is not None:
            return price <= available_cash_foreign, price_krw

        return price_krw <= available_cash_krw, price_krw

    def _filter_affordable_stocks(
        self,
        stocks: list[dict],
        available_cash_krw: float,
        available_cash_foreign: float | None,
        fx_rates: dict[str, float],
    ) -> tuple[list[dict], int]:
        """현재 가용 현금으로 1주 매수 가능한 후보만 남긴다."""
        filtered: list[dict] = []
        dropped = 0

        for item in stocks:
            is_affordable, price_krw = self._is_affordable_stock(
                item,
                available_cash_krw=available_cash_krw,
                available_cash_foreign=available_cash_foreign,
                fx_rates=fx_rates,
            )
            if not is_affordable:
                dropped += 1
                continue
            filtered.append({**item, "price_krw": price_krw})

        return filtered, dropped

    def _build_stock_lookup(self, *groups: list[dict]) -> dict[tuple[str, str], dict]:
        """시장/심볼 기준 후보 종목 역참조 인덱스 생성"""
        lookup: dict[tuple[str, str], dict] = {}
        for stocks in groups:
            for item in stocks:
                symbol = str(item.get("symbol", "")).strip()
                if not symbol:
                    continue
                market_code = normalize_market(item.get("market", settings.primary_market_code))
                lookup[(market_code, symbol.upper())] = item
        return lookup

    def _filter_selected_by_available_cash(
        self,
        selected: list[dict],
        stock_lookup: dict[tuple[str, str], dict],
        available_cash_krw: float,
        available_cash_foreign: float | None,
        fx_rates: dict[str, float],
        default_market: str,
    ) -> tuple[list[dict], int]:
        """LLM 선택 결과를 원천 시세와 대조해 1주 매수 가능 후보만 유지"""
        filtered: list[dict] = []
        dropped = 0

        for item in selected:
            symbol = str(item.get("symbol", "")).strip()
            market_code = normalize_market(item.get("market", default_market), default=default_market)
            source = stock_lookup.get((market_code, symbol.upper()))
            if source is None and symbol:
                matches = [
                    candidate
                    for (_, candidate_symbol), candidate in stock_lookup.items()
                    if candidate_symbol == symbol.upper()
                ]
                if len(matches) == 1:
                    source = matches[0]
                    market_code = normalize_market(source.get("market", market_code), default=market_code)
            candidate = {**(source or {}), **item, "market": market_code}
            is_affordable, price_krw = self._is_affordable_stock(
                candidate,
                available_cash_krw=available_cash_krw,
                available_cash_foreign=available_cash_foreign,
                fx_rates=fx_rates,
            )
            if not symbol or not is_affordable:
                dropped += 1
                continue
            filtered.append(candidate)

        return filtered, dropped

    @staticmethod
    def _merge_stock_lists(*groups: list[dict]) -> list[dict]:
        """시장/심볼 기준으로 후보군을 병합한다."""
        merged: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for group in groups:
            for item in group:
                symbol = str(item.get("symbol", "")).strip().upper()
                if not symbol:
                    continue
                market_code = normalize_market(item.get("market", settings.primary_market_code))
                key = (market_code, symbol)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged

    def _resolve_trade_value_usd(self, item: dict) -> float:
        """USD 기준 거래대금을 정리한다."""
        direct_value = self._to_float(item.get("trade_value_usd"), 0.0)
        if direct_value > 0:
            return direct_value

        currency = str(item.get("currency") or "USD").upper()
        trade_value = self._to_float(item.get("trade_value"), 0.0)
        if trade_value > 0 and currency == "USD":
            return trade_value

        price = self._to_float(item.get("price", item.get("current_price", 0.0)), 0.0)
        volume = self._to_float(item.get("volume", 0.0), 0.0)
        if price > 0 and volume > 0:
            return price * volume
        return 0.0

    def _resolve_junk_filter_reason(
        self,
        item: dict,
        thresholds: dict[str, float],
    ) -> str | None:
        """미국 프리마켓 잡주 필터 제외 사유."""
        price = self._to_float(item.get("price", item.get("current_price", 0.0)), 0.0)
        volume = self._to_float(item.get("volume", 0.0), 0.0)
        trade_value_usd = self._resolve_trade_value_usd(item)
        abs_change_pct = abs(self._to_float(item.get("change_rate", 0.0), 0.0))

        if price < thresholds["min_price_usd"]:
            return "price_below_min"
        if volume < thresholds["min_volume"]:
            return "volume_below_min"
        if trade_value_usd < thresholds["min_trade_value_usd"]:
            return "trade_value_below_min"
        if abs_change_pct > thresholds["max_abs_change_pct"]:
            return "abs_change_above_max"
        if (
            price < thresholds["hot_price_ceiling_usd"]
            and abs_change_pct >= thresholds["hot_abs_change_pct"]
            and trade_value_usd < thresholds["hot_min_trade_value_usd"]
        ):
            return "hot_low_price_mover"
        return None

    def _apply_us_premarket_junk_filter(
        self,
        stocks: list[dict],
        thresholds: dict[str, float],
    ) -> tuple[list[dict], dict[str, object]]:
        """미국 프리마켓 잡주 필터 적용."""
        filtered: list[dict] = []
        reasons: dict[str, int] = {}

        for item in stocks:
            reason = self._resolve_junk_filter_reason(item, thresholds)
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            filtered.append({
                **item,
                "trade_value_usd": self._resolve_trade_value_usd(item),
            })

        return filtered, {
            "before": len(stocks),
            "after": len(filtered),
            "dropped": len(stocks) - len(filtered),
            "reasons": reasons,
        }

    def _apply_us_premarket_junk_filter_groups(
        self,
        volume_rank: list[dict],
        surge_data: list[dict],
        drop_data: list[dict],
    ) -> tuple[list[dict], list[dict], list[dict], dict[str, dict[str, object]]]:
        """카테고리별 프리마켓 잡주 필터 적용."""
        thresholds = settings.us_premarket_junk_filter_thresholds
        volume_rank, volume_stats = self._apply_us_premarket_junk_filter(volume_rank, thresholds)
        surge_data, surge_stats = self._apply_us_premarket_junk_filter(surge_data, thresholds)
        drop_data, drop_stats = self._apply_us_premarket_junk_filter(drop_data, thresholds)
        return volume_rank, surge_data, drop_data, {
            "volume_rank": volume_stats,
            "surge_data": surge_stats,
            "drop_data": drop_stats,
        }

    def _resolve_us_regular_opening_guard_reason(
        self,
        item: dict,
        thresholds: dict[str, float],
    ) -> str | None:
        """미국 정규장 오프닝 급등 저가주 제외 사유."""
        price = self._to_float(item.get("price", item.get("current_price", 0.0)), 0.0)
        abs_change_pct = abs(self._to_float(item.get("change_rate", 0.0), 0.0))

        if (
            price < thresholds["min_price_usd"]
            and abs_change_pct >= thresholds["low_price_max_abs_change_pct"]
        ):
            return "opening_low_price_hot_mover"
        if (
            price < thresholds["mid_price_ceiling_usd"]
            and abs_change_pct >= thresholds["mid_price_max_abs_change_pct"]
        ):
            return "opening_mid_price_extreme_mover"
        return None

    def _apply_us_regular_opening_guard(
        self,
        stocks: list[dict],
        thresholds: dict[str, float],
    ) -> tuple[list[dict], dict[str, object]]:
        """미국 정규장 오프닝 급등 저가주 필터 적용."""
        filtered: list[dict] = []
        reasons: dict[str, int] = {}

        for item in stocks:
            reason = self._resolve_us_regular_opening_guard_reason(item, thresholds)
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            filtered.append(item)

        return filtered, {
            "before": len(stocks),
            "after": len(filtered),
            "dropped": len(stocks) - len(filtered),
            "reasons": reasons,
        }

    def _apply_us_regular_opening_guard_groups(
        self,
        volume_rank: list[dict],
        surge_data: list[dict],
        drop_data: list[dict],
    ) -> tuple[list[dict], list[dict], list[dict], dict[str, dict[str, object]]]:
        """카테고리별 미국 정규장 오프닝 가드 적용."""
        thresholds = settings.us_regular_opening_guard_thresholds
        volume_rank, volume_stats = self._apply_us_regular_opening_guard(volume_rank, thresholds)
        surge_data, surge_stats = self._apply_us_regular_opening_guard(surge_data, thresholds)
        drop_data, drop_stats = self._apply_us_regular_opening_guard(drop_data, thresholds)
        return volume_rank, surge_data, drop_data, {
            "volume_rank": volume_stats,
            "surge_data": surge_stats,
            "drop_data": drop_stats,
        }

    @staticmethod
    def _bucket_monitor_floor_met(
        volume_rank: list[dict],
        surge_data: list[dict],
        drop_data: list[dict],
    ) -> bool:
        """버킷별 최소 개수 충족 여부."""
        return min(len(volume_rank), len(surge_data), len(drop_data)) >= 2

    def _has_sufficient_us_monitor_candidates(
        self,
        volume_rank: list[dict],
        surge_data: list[dict],
        drop_data: list[dict],
    ) -> bool:
        """미국 프리마켓 후보 풀이 충분한지 확인."""
        unique_candidates = self._merge_stock_lists(volume_rank, surge_data, drop_data)
        return (
            len(unique_candidates) >= settings.us_premarket_min_monitor_candidates
            and self._bucket_monitor_floor_met(volume_rank, surge_data, drop_data)
        )

    @staticmethod
    def _detail_enrich_limit() -> int:
        """상세 시세 보강 최대 건수."""
        return max(
            _US_DETAIL_ENRICH_MIN_LIMIT,
            settings.us_premarket_min_monitor_candidates * _US_DETAIL_ENRICH_MULTIPLIER,
        )

    @staticmethod
    def _merge_stock_with_detail(stock: dict, detail: dict | None) -> dict:
        """기존 후보에 상세 시세를 합친다."""
        if not detail:
            return stock
        merged = {**stock, **detail}
        merged["name"] = stock.get("name") or detail.get("name") or stock.get("symbol", "")
        merged["scan_source"] = stock.get("scan_source", detail.get("scan_source", "DISCOVERY"))
        merged["market"] = normalize_market(merged.get("market", stock.get("market", settings.primary_market_code)))
        return merged

    async def _enrich_us_candidates_with_price_detail(
        self,
        volume_rank: list[dict],
        surge_data: list[dict],
        drop_data: list[dict],
    ) -> tuple[list[dict], list[dict], list[dict], dict[str, int]]:
        """미국장 후보 일부에 price-detail 상세 시세를 보강한다."""
        detail_limit = self._detail_enrich_limit()
        unique_candidates = self._merge_stock_lists(volume_rank, surge_data, drop_data)[:detail_limit]
        if not unique_candidates:
            return volume_rank, surge_data, drop_data, {"requested": 0, "enriched": 0}

        responses = await asyncio.gather(
            *[
                mcp_client.get_current_price_detail(
                    str(item.get("symbol", "")),
                    market=str(item.get("market", settings.primary_market_code)),
                )
                for item in unique_candidates
            ],
            return_exceptions=True,
        )
        detail_lookup: dict[tuple[str, str], dict] = {}
        enriched = 0
        for item, response in zip(unique_candidates, responses, strict=False):
            if isinstance(response, Exception) or not getattr(response, "success", False):
                continue
            data = response.data or {}
            symbol = str(data.get("symbol", item.get("symbol", ""))).strip().upper()
            market_code = normalize_market(data.get("market", item.get("market", settings.primary_market_code)))
            if not symbol:
                continue
            detail_lookup[(market_code, symbol)] = data
            enriched += 1

        def apply(group: list[dict]) -> list[dict]:
            return [
                self._merge_stock_with_detail(
                    stock,
                    detail_lookup.get((
                        normalize_market(stock.get("market", settings.primary_market_code)),
                        str(stock.get("symbol", "")).strip().upper(),
                    )),
                )
                for stock in group
            ]

        return apply(volume_rank), apply(surge_data), apply(drop_data), {
            "requested": len(unique_candidates),
            "enriched": enriched,
        }

    async def _build_us_expansion_candidates(
        self,
        default_market: str,
        holdings,
    ) -> list[dict]:
        """워치리스트/보유 종목 기반 추가 미국장 후보 수집."""
        request_items: list[tuple[str, str, str, str]] = []
        seen: set[tuple[str, str]] = set()

        for holding in holdings or []:
            market_code = normalize_market(getattr(holding, "market", default_market), default=default_market)
            if not is_us_market(market_code):
                continue
            symbol = str(getattr(holding, "symbol", "")).strip().upper()
            if not symbol:
                continue
            key = (market_code, symbol)
            if key in seen:
                continue
            seen.add(key)
            request_items.append((symbol, market_code, str(getattr(holding, "name", symbol)), "HOLDING_EXPANSION"))

        for symbol in settings.us_watchlist_symbols:
            watch_market = normalize_market(default_market, default=default_market)
            key = (watch_market, symbol)
            if key in seen:
                continue
            seen.add(key)
            request_items.append((symbol, watch_market, symbol, "WATCHLIST_EXPANSION"))

        if not request_items:
            return []

        responses = await asyncio.gather(
            *[
                mcp_client.get_current_price_detail(symbol, market=market)
                for symbol, market, _, _ in request_items
            ],
            return_exceptions=True,
        )
        candidates: list[dict] = []
        for (symbol, market_code, fallback_name, scan_source), response in zip(request_items, responses, strict=False):
            if isinstance(response, Exception) or not getattr(response, "success", False):
                continue
            data = response.data or {}
            price = self._to_float(data.get("price", data.get("current_price", 0.0)), 0.0)
            if price <= 0:
                continue
            candidates.append({
                **data,
                "symbol": str(data.get("symbol", symbol)).strip().upper(),
                "name": str(data.get("name") or fallback_name or symbol),
                "market": normalize_market(data.get("market", market_code), default=market_code),
                "scan_source": scan_source,
                "trade_value_usd": self._resolve_trade_value_usd(data),
            })
        return self._filter_untradeable(candidates, market=default_market)

    def _merge_rank_group(
        self,
        base: list[dict],
        extra: list[dict],
        *,
        sort_key: str,
        reverse: bool,
        limit: int,
    ) -> list[dict]:
        """버킷별 후보를 병합 후 정렬한다."""
        merged = self._merge_stock_lists(base, extra)
        merged.sort(key=lambda item: self._to_float(item.get(sort_key, 0.0), 0.0), reverse=reverse)
        return merged[:limit]

    def _supplement_candidate_groups(
        self,
        volume_rank: list[dict],
        surge_data: list[dict],
        drop_data: list[dict],
        supplemental: list[dict],
        *,
        limit: int,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """추가 후보를 카테고리별 랭킹에 배치한다."""
        if not supplemental:
            return volume_rank, surge_data, drop_data

        volume_rank = self._merge_rank_group(
            volume_rank,
            supplemental,
            sort_key="volume",
            reverse=True,
            limit=limit,
        )
        surge_candidates = [item for item in supplemental if self._to_float(item.get("change_rate", 0.0), 0.0) >= 0.0]
        drop_candidates = [item for item in supplemental if self._to_float(item.get("change_rate", 0.0), 0.0) < 0.0]
        surge_data = self._merge_rank_group(
            surge_data,
            surge_candidates,
            sort_key="change_rate",
            reverse=True,
            limit=limit,
        )
        drop_data = self._merge_rank_group(
            drop_data,
            drop_candidates,
            sort_key="change_rate",
            reverse=False,
            limit=limit,
        )
        return volume_rank, surge_data, drop_data

    async def _fetch_us_rank_stage(
        self,
        scan_markets: list[str],
        stage: int,
        holdings,
        default_market: str,
    ) -> tuple[list[dict], list[dict], list[dict], dict[str, int]]:
        """미국 프리마켓 확장 stage별 후보 재수집."""
        limit = _US_EXPANDED_SCAN_LIMIT if stage >= 1 else _US_BASE_SCAN_LIMIT
        include_trade_growth = stage >= 2
        volume_rank, surge_data, drop_data = await asyncio.gather(
            self._get_volume_rank(
                scan_markets,
                limit=limit,
                include_trade_growth=include_trade_growth,
            ),
            self._get_fluctuation_rank(scan_markets, "top", limit=limit),
            self._get_fluctuation_rank(scan_markets, "bottom", limit=limit),
        )
        supplemental_count = 0
        if stage >= 3:
            supplemental = await self._build_us_expansion_candidates(default_market, holdings)
            supplemental_count = len(supplemental)
            volume_rank, surge_data, drop_data = self._supplement_candidate_groups(
                volume_rank,
                surge_data,
                drop_data,
                supplemental,
                limit=limit,
            )

        return volume_rank, surge_data, drop_data, {
            "stage": stage,
            "limit": limit,
            "include_trade_growth": int(include_trade_growth),
            "supplemental_count": supplemental_count,
        }

    async def _prepare_us_premarket_candidates(
        self,
        target: str,
        scan_markets: list[str],
        holdings,
        available_cash: float,
        available_cash_foreign: float | None,
        initial_groups: tuple[list[dict], list[dict], list[dict]],
    ) -> tuple[list[dict], list[dict], list[dict], dict[str, float], dict, dict]:
        """미국 프리마켓 잡주 필터 + 확장 파이프라인."""
        max_stage = settings.us_premarket_expansion_max_stage if settings.US_PREMARKET_EXPANSION_ENABLED else 0
        final_groups = initial_groups
        final_fx_rates: dict[str, float] = {}
        final_affordability_stats: dict[str, dict[str, int]] = {}
        final_junk_stats: dict[str, dict[str, object]] = {}
        expansion_meta: dict[str, object] = {
            "triggered": False,
            "final_stage": 0,
            "stage_counts": [],
        }

        for stage in range(max_stage + 1):
            if stage == 0:
                volume_rank, surge_data, drop_data = initial_groups
                stage_meta = {
                    "stage": 0,
                    "limit": _US_BASE_SCAN_LIMIT,
                    "include_trade_growth": 0,
                    "supplemental_count": 0,
                }
            else:
                expansion_meta["triggered"] = True
                volume_rank, surge_data, drop_data, stage_meta = await self._fetch_us_rank_stage(
                    scan_markets,
                    stage,
                    holdings,
                    scan_markets[0] if scan_markets else target,
                )

            volume_rank, surge_data, drop_data, detail_meta = await self._enrich_us_candidates_with_price_detail(
                volume_rank,
                surge_data,
                drop_data,
            )
            volume_rank, surge_data, drop_data, junk_stats = self._apply_us_premarket_junk_filter_groups(
                volume_rank,
                surge_data,
                drop_data,
            )
            unique_after_junk = len(self._merge_stock_lists(volume_rank, surge_data, drop_data))
            fx_rates = await self._build_affordability_fx_rates(
                self._merge_stock_lists(volume_rank, surge_data, drop_data),
            )
            affordability_stats = {
                "volume_rank": {"before": len(volume_rank), "after": 0, "dropped": 0},
                "surge_data": {"before": len(surge_data), "after": 0, "dropped": 0},
                "drop_data": {"before": len(drop_data), "after": 0, "dropped": 0},
            }
            volume_rank, volume_dropped = self._filter_affordable_stocks(
                volume_rank,
                available_cash,
                available_cash_foreign,
                fx_rates,
            )
            surge_data, surge_dropped = self._filter_affordable_stocks(
                surge_data,
                available_cash,
                available_cash_foreign,
                fx_rates,
            )
            drop_data, drop_dropped = self._filter_affordable_stocks(
                drop_data,
                available_cash,
                available_cash_foreign,
                fx_rates,
            )
            affordability_stats["volume_rank"]["after"] = len(volume_rank)
            affordability_stats["volume_rank"]["dropped"] = volume_dropped
            affordability_stats["surge_data"]["after"] = len(surge_data)
            affordability_stats["surge_data"]["dropped"] = surge_dropped
            affordability_stats["drop_data"]["after"] = len(drop_data)
            affordability_stats["drop_data"]["dropped"] = drop_dropped

            unique_after_affordability = len(self._merge_stock_lists(volume_rank, surge_data, drop_data))
            expansion_meta["stage_counts"].append({
                **stage_meta,
                "detail_requested": detail_meta["requested"],
                "detail_enriched": detail_meta["enriched"],
                "unique_after_junk": unique_after_junk,
                "unique_after_affordability": unique_after_affordability,
            })
            final_groups = (volume_rank, surge_data, drop_data)
            final_fx_rates = fx_rates
            final_affordability_stats = affordability_stats
            final_junk_stats = junk_stats
            expansion_meta["final_stage"] = stage

            if self._has_sufficient_us_monitor_candidates(volume_rank, surge_data, drop_data):
                break

        return (*final_groups, final_fx_rates, final_affordability_stats, {
            **expansion_meta,
            "triggered": bool(expansion_meta["triggered"]),
        }, final_junk_stats)

    @staticmethod
    def _regular_selected_floor_target(market: str, session: str) -> int:
        """미국 정규장 최소 선정 종목 수."""
        market_code = normalize_market(market)
        session_code = str(session or "").upper()
        if not is_us_market(market_code) or session_code != "US_REGULAR":
            return 0
        return settings.us_regular_min_selected_candidates

    def _build_regular_floor_candidate_pool(self, *groups: list[dict]) -> list[dict]:
        """정규장 최소 선정 보장용 candidate pool 생성."""
        merged = self._merge_stock_lists(*groups)
        if not merged:
            return []

        prepared = [
            {
                **item,
                "market": normalize_market(item.get("market", settings.primary_market_code)),
                "strategy_type": str(item.get("strategy_type") or "STABLE_SHORT").upper(),
            }
            for item in merged
        ]
        return self._apply_product_policy(prepared, log_summary=False)

    async def _expand_us_regular_candidate_groups(
        self,
        scan_markets: list[str],
        holdings,
        default_market: str,
    ) -> tuple[list[dict], list[dict], list[dict], dict[str, int]]:
        """미국 정규장 후보 풀이 부족할 때 discovery + 시드로 확장."""
        limit = _US_EXPANDED_SCAN_LIMIT
        volume_rank, surge_data, drop_data = await asyncio.gather(
            self._get_volume_rank(
                scan_markets,
                limit=limit,
                include_trade_growth=True,
            ),
            self._get_fluctuation_rank(scan_markets, "top", limit=limit),
            self._get_fluctuation_rank(scan_markets, "bottom", limit=limit),
        )
        supplemental = await self._build_us_expansion_candidates(default_market, holdings)
        volume_rank, surge_data, drop_data = self._supplement_candidate_groups(
            volume_rank,
            surge_data,
            drop_data,
            supplemental,
            limit=limit,
        )
        return volume_rank, surge_data, drop_data, {
            "limit": limit,
            "supplemental_count": len(supplemental),
        }

    @staticmethod
    def _backfill_selected_candidates(
        selected: list[dict],
        candidate_pool: list[dict],
        minimum_count: int,
    ) -> tuple[list[dict], int]:
        """LLM 선정이 부족하면 deterministic fallback으로 보강."""
        if minimum_count <= 0 or len(selected) >= minimum_count:
            return selected, 0

        merged = list(selected)
        seen = {
            (
                normalize_market(item.get("market", settings.primary_market_code)),
                str(item.get("symbol", "")).strip().upper(),
            )
            for item in merged
            if item.get("symbol")
        }
        backfilled = 0

        for item in candidate_pool:
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            market_code = normalize_market(item.get("market", settings.primary_market_code))
            key = (market_code, symbol)
            if key in seen:
                continue

            merged.append({
                **item,
                "symbol": symbol,
                "market": market_code,
                "strategy_type": str(item.get("strategy_type") or "STABLE_SHORT").upper(),
                "reason": str(item.get("reason") or "가용 현금 기준 후보 보강"),
                "scan_source": item.get("scan_source", "FLOOR_BACKFILL"),
            })
            seen.add(key)
            backfilled += 1
            if len(merged) >= minimum_count:
                break

        return merged, backfilled

    @staticmethod
    def _empty_candidate_market_summary(
        junk_filter_stats: dict[str, dict[str, object]] | None,
        opening_guard_stats: dict[str, dict[str, object]] | None = None,
    ) -> str:
        """후보가 모두 제거됐을 때 요약 메시지."""
        if not junk_filter_stats:
            if not opening_guard_stats:
                return "가용 현금 기준 1주 매수 가능 후보 없음"
        if opening_guard_stats:
            total_after_guard = sum(int(stats.get("after", 0)) for stats in opening_guard_stats.values())
            total_guard_dropped = sum(int(stats.get("dropped", 0)) for stats in opening_guard_stats.values())
            if total_after_guard == 0 and total_guard_dropped > 0:
                return "정규장 오프닝 가드 기준 후보 없음"
        if not junk_filter_stats:
            return "가용 현금 기준 1주 매수 가능 후보 없음"

        total_after_junk = sum(int(stats.get("after", 0)) for stats in junk_filter_stats.values())
        total_junk_dropped = sum(int(stats.get("dropped", 0)) for stats in junk_filter_stats.values())
        if total_after_junk == 0 and total_junk_dropped > 0:
            return "프리마켓 잡주 필터 기준 후보 없음"
        return "가용 현금 기준 1주 매수 가능 후보 없음"

    @staticmethod
    def _selection_target_range(
        market: str,
        session: str,
        minutes_until_cutoff: int,
    ) -> str:
        market_code = normalize_market(market)
        session_code = str(session or "").upper()
        if is_us_market(market_code) and session_code == "US_PRE":
            return "3~6"
        if (
            is_us_market(market_code)
            and session_code == "US_REGULAR"
            and minutes_until_cutoff <= 30
            and settings.us_regular_min_selected_candidates > 0
        ):
            lower = settings.us_regular_min_selected_candidates
            upper = max(lower, 4)
            return f"{lower}~{upper}"
        if minutes_until_cutoff <= 30:
            return "0~3"
        if minutes_until_cutoff <= 90:
            return "3~5"
        return "5~8"

    async def scan(
        self,
        market: str | None = None,
        cycle_id: str | None = None,
        dynamic_limits: dict | None = None,
        account_snapshot: tuple | None = None,
    ) -> dict:
        """시장 스캔 + 종목 선별 통합 실행"""
        target = normalize_market(market or settings.primary_market_code)
        primary_market = target
        if is_us_market(primary_market) and not settings.US_TRADING_ENABLED:
            logger.info("미국장 비활성화 → 시장 스캔 스킵")
            return {"selected": [], "market_summary": "미국장 비활성화", "available_cash": 0}

        logger.info("시장 스캔 시작: {}", primary_market)
        timer = activity_logger.timer()

        await activity_logger.log(
            ActivityType.SCAN, ActivityPhase.START,
            f"\U0001f4e1 시장 스캔 중... {get_market_label(primary_market)} 거래량/등락 상위 종목 조회",
            cycle_id=cycle_id,
        )

        from util.time_util import now_kst
        from zoneinfo import ZoneInfo
        from trading.market_profile import market_timezone

        scan_markets = settings.scan_markets_for(target)
        now = now_kst().astimezone(ZoneInfo(market_timezone(target)))
        session = market_calendar.get_market_session(dt=now, market=target)
        regular_open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_from_regular_open = None
        if is_us_market(target) and session == "US_REGULAR":
            minutes_from_regular_open = max(0, int((now - regular_open_time).total_seconds() / 60))
        mkt_cfg = settings.get_market_config(target, session=session)
        cutoff_time = now.replace(
            hour=mkt_cfg["buy_cutoff_hour"],
            minute=mkt_cfg["buy_cutoff_minute"],
            second=0,
            microsecond=0,
        )
        minutes_until_cutoff = max(0, int((cutoff_time - now).total_seconds() / 60))
        selection_target_range = self._selection_target_range(
            target,
            session,
            minutes_until_cutoff,
        )
        regular_floor_target = self._regular_selected_floor_target(target, session)
        regular_selection_floor = (
            settings.us_regular_min_selected_candidates
            if is_us_market(primary_market)
            else 0
        )
        if account_snapshot is None:
            (
                account_snapshot,
                volume_rank,
                surge_data,
                drop_data,
                performance_summary,
            ) = await asyncio.gather(
                account_manager.get_account_snapshot(target),
                self._get_volume_rank(scan_markets),
                self._get_fluctuation_rank(scan_markets, "top"),
                self._get_fluctuation_rank(scan_markets, "bottom"),
                self._get_performance_summary(target),
            )
        else:
            (
                volume_rank,
                surge_data,
                drop_data,
                performance_summary,
            ) = await asyncio.gather(
                self._get_volume_rank(scan_markets),
                self._get_fluctuation_rank(scan_markets, "top"),
                self._get_fluctuation_rank(scan_markets, "bottom"),
                self._get_performance_summary(target),
            )
        balance, holdings = account_snapshot
        available_cash = balance.effective_cash
        max_pos_pct = 0.2
        if dynamic_limits:
            max_pos_pct = dynamic_limits.get("max_position_pct", 20.0) / 100
        max_per_stock = available_cash * max_pos_pct
        available_cash_foreign = self._resolve_available_cash_foreign(
            target,
            balance,
        )
        max_per_stock_foreign = (
            available_cash_foreign * max_pos_pct
            if available_cash_foreign is not None
            else None
        )
        junk_filter_enabled = settings.is_us_premarket_junk_filter_session(target, session=session)
        opening_guard_enabled = settings.is_us_regular_opening_guard_session(
            target,
            session=session,
            minutes_from_regular_open=minutes_from_regular_open,
        )
        junk_filter_stats: dict[str, dict[str, object]] = {}
        opening_guard_stats: dict[str, dict[str, object]] = {}
        expansion_stats: dict[str, object] = {
            "triggered": False,
            "final_stage": 0,
            "stage_counts": [],
        }
        regular_floor_stats: dict[str, object] = {
            "target": regular_floor_target,
            "triggered": False,
            "base_affordable_count": 0,
            "expanded_affordable_count": 0,
            "candidate_pool_count": 0,
            "supplemental_count": 0,
            "backfilled_count": 0,
            "unmet_floor": False,
        }
        regular_floor_candidate_pool: list[dict] = []
        if junk_filter_enabled:
            (
                volume_rank,
                surge_data,
                drop_data,
                fx_rates,
                affordability_stats,
                expansion_stats,
                junk_filter_stats,
            ) = await self._prepare_us_premarket_candidates(
                target,
                scan_markets,
                holdings,
                available_cash,
                available_cash_foreign,
                (volume_rank, surge_data, drop_data),
            )
        else:
            all_candidates = [*volume_rank, *surge_data, *drop_data]
            fx_rates = await self._build_affordability_fx_rates(all_candidates)
            affordability_stats = {
                "volume_rank": {"before": len(volume_rank), "after": 0, "dropped": 0},
                "surge_data": {"before": len(surge_data), "after": 0, "dropped": 0},
                "drop_data": {"before": len(drop_data), "after": 0, "dropped": 0},
            }
            volume_rank, volume_dropped = self._filter_affordable_stocks(
                volume_rank,
                available_cash,
                available_cash_foreign,
                fx_rates,
            )
            surge_data, surge_dropped = self._filter_affordable_stocks(
                surge_data,
                available_cash,
                available_cash_foreign,
                fx_rates,
            )
            drop_data, drop_dropped = self._filter_affordable_stocks(
                drop_data,
                available_cash,
                available_cash_foreign,
                fx_rates,
            )
            affordability_stats["volume_rank"]["after"] = len(volume_rank)
            affordability_stats["volume_rank"]["dropped"] = volume_dropped
            affordability_stats["surge_data"]["after"] = len(surge_data)
            affordability_stats["surge_data"]["dropped"] = surge_dropped
            affordability_stats["drop_data"]["after"] = len(drop_data)
            affordability_stats["drop_data"]["dropped"] = drop_dropped
            if opening_guard_enabled:
                (
                    volume_rank,
                    surge_data,
                    drop_data,
                    opening_guard_stats,
                ) = self._apply_us_regular_opening_guard_groups(
                    volume_rank,
                    surge_data,
                    drop_data,
                )

        if regular_floor_target > 0:
            regular_floor_candidate_pool = self._build_regular_floor_candidate_pool(
                volume_rank,
                surge_data,
                drop_data,
            )
            regular_floor_stats["base_affordable_count"] = len(regular_floor_candidate_pool)
            regular_floor_stats["expanded_affordable_count"] = len(regular_floor_candidate_pool)
            regular_floor_stats["candidate_pool_count"] = len(regular_floor_candidate_pool)

            if len(regular_floor_candidate_pool) < regular_floor_target:
                regular_floor_stats["triggered"] = True
                (
                    volume_rank,
                    surge_data,
                    drop_data,
                    regular_expansion_meta,
                ) = await self._expand_us_regular_candidate_groups(
                    scan_markets,
                    holdings,
                    scan_markets[0] if scan_markets else target,
                )
                regular_floor_stats["supplemental_count"] = regular_expansion_meta["supplemental_count"]
                expanded_candidates = [*volume_rank, *surge_data, *drop_data]
                fx_rates = await self._build_affordability_fx_rates(expanded_candidates)
                affordability_stats = {
                    "volume_rank": {"before": len(volume_rank), "after": 0, "dropped": 0},
                    "surge_data": {"before": len(surge_data), "after": 0, "dropped": 0},
                    "drop_data": {"before": len(drop_data), "after": 0, "dropped": 0},
                }
                volume_rank, volume_dropped = self._filter_affordable_stocks(
                    volume_rank,
                    available_cash,
                    available_cash_foreign,
                    fx_rates,
                )
                surge_data, surge_dropped = self._filter_affordable_stocks(
                    surge_data,
                    available_cash,
                    available_cash_foreign,
                    fx_rates,
                )
                drop_data, drop_dropped = self._filter_affordable_stocks(
                    drop_data,
                    available_cash,
                    available_cash_foreign,
                    fx_rates,
                )
                affordability_stats["volume_rank"]["after"] = len(volume_rank)
                affordability_stats["volume_rank"]["dropped"] = volume_dropped
                affordability_stats["surge_data"]["after"] = len(surge_data)
                affordability_stats["surge_data"]["dropped"] = surge_dropped
                affordability_stats["drop_data"]["after"] = len(drop_data)
                affordability_stats["drop_data"]["dropped"] = drop_dropped
                if opening_guard_enabled:
                    (
                        volume_rank,
                        surge_data,
                        drop_data,
                        opening_guard_stats,
                    ) = self._apply_us_regular_opening_guard_groups(
                        volume_rank,
                        surge_data,
                        drop_data,
                    )
                regular_floor_candidate_pool = self._build_regular_floor_candidate_pool(
                    volume_rank,
                    surge_data,
                    drop_data,
                )
                regular_floor_stats["expanded_affordable_count"] = len(regular_floor_candidate_pool)
                regular_floor_stats["candidate_pool_count"] = len(regular_floor_candidate_pool)

        data_elapsed = activity_logger.elapsed_ms(timer)
        logger.info("MCP 데이터 수집 완료: {}ms", data_elapsed)
        if junk_filter_enabled:
            junk_before = sum(int(stats.get("before", 0)) for stats in junk_filter_stats.values())
            junk_after = sum(int(stats.get("after", 0)) for stats in junk_filter_stats.values())
            junk_reasons: dict[str, int] = {}
            for stats in junk_filter_stats.values():
                for reason, count in dict(stats.get("reasons", {})).items():
                    junk_reasons[reason] = junk_reasons.get(reason, 0) + int(count)
            if junk_before != junk_after:
                logger.info(
                    "미국 프리마켓 잡주 필터 적용: {}건 → {}건 | {}",
                    junk_before,
                    junk_after,
                    junk_reasons,
                )
            if expansion_stats.get("triggered"):
                logger.info(
                    "미국 프리마켓 후보 확장 완료: final_stage={} | {}",
                    expansion_stats.get("final_stage", 0),
                    expansion_stats.get("stage_counts", []),
                )
        if opening_guard_enabled:
            opening_before = sum(int(stats.get("before", 0)) for stats in opening_guard_stats.values())
            opening_after = sum(int(stats.get("after", 0)) for stats in opening_guard_stats.values())
            opening_reasons: dict[str, int] = {}
            for stats in opening_guard_stats.values():
                for reason, count in dict(stats.get("reasons", {})).items():
                    opening_reasons[reason] = opening_reasons.get(reason, 0) + int(count)
            if opening_before != opening_after:
                logger.info(
                    "미국 정규장 오프닝 가드 적용: {}건 → {}건 | {}",
                    opening_before,
                    opening_after,
                    opening_reasons,
                )
        if regular_floor_stats.get("triggered"):
            logger.info(
                "미국 정규장 후보 floor 보강: base={} expanded={} supplemental={}",
                regular_floor_stats.get("base_affordable_count", 0),
                regular_floor_stats.get("expanded_affordable_count", 0),
                regular_floor_stats.get("supplemental_count", 0),
            )

        if any(int(stats["dropped"]) > 0 for stats in affordability_stats.values()):
            if available_cash_foreign is not None:
                logger.info(
                    "가용 현금 기준 후보 필터 적용: 거래량 {}→{}, 급등 {}→{}, 급락 {}→{} (실주문 기준 현금 {:,.2f}USD)",
                    affordability_stats["volume_rank"]["before"],
                    affordability_stats["volume_rank"]["after"],
                    affordability_stats["surge_data"]["before"],
                    affordability_stats["surge_data"]["after"],
                    affordability_stats["drop_data"]["before"],
                    affordability_stats["drop_data"]["after"],
                    available_cash_foreign,
                )
            else:
                logger.info(
                    "가용 현금 기준 후보 필터 적용: 거래량 {}→{}, 급등 {}→{}, 급락 {}→{} (현금 {:,.0f}원)",
                    affordability_stats["volume_rank"]["before"],
                    affordability_stats["volume_rank"]["after"],
                    affordability_stats["surge_data"]["before"],
                    affordability_stats["surge_data"]["after"],
                    affordability_stats["drop_data"]["before"],
                    affordability_stats["drop_data"]["after"],
                    available_cash,
                )
        if not volume_rank and not surge_data and not drop_data:
            elapsed = activity_logger.elapsed_ms(timer)
            market_summary = self._empty_candidate_market_summary(
                junk_filter_stats if junk_filter_enabled else None,
                opening_guard_stats if opening_guard_enabled else None,
            )
            if regular_floor_target > 0:
                regular_floor_stats["unmet_floor"] = True
            await activity_logger.log(
                ActivityType.SCAN, ActivityPhase.COMPLETE,
                f"\U0001f4e1 시장 스캔 완료: {market_summary}",
                cycle_id=cycle_id,
                detail={
                    "selected_count": 0,
                    "selected": [],
                    "market_analysis": market_summary,
                    "available_cash": available_cash,
                    "available_cash_foreign": available_cash_foreign,
                    "markets": scan_markets,
                    "affordability_filter": affordability_stats,
                    "junk_filter": junk_filter_stats,
                    "opening_guard": opening_guard_stats,
                    "expansion": expansion_stats,
                    "regular_floor": regular_floor_stats,
                },
                execution_time_ms=elapsed,
            )
            return {
                "selected": [],
                "market_summary": market_summary,
                "market_regime": "",
                "market_analysis": market_summary,
                "leading_sectors": [],
                "available_cash": available_cash,
                "available_cash_foreign": available_cash_foreign,
                "max_per_stock": max_per_stock,
                "max_per_stock_foreign": max_per_stock_foreign,
                "markets": scan_markets,
                "opening_guard": opening_guard_stats,
                "regular_floor": regular_floor_stats,
            }

        prompt = get_market_scan_prompt(primary_market).format(
            market_label=get_market_label(primary_market),
            current_time=now.strftime("%H:%M"),
            timezone_label=now.tzname() or "LOCAL",
            market_session=session,
            minutes_until_cutoff=minutes_until_cutoff,
            selection_target_range=selection_target_range,
            regular_selection_floor=regular_selection_floor,
            available_cash=available_cash,
            available_cash_foreign=available_cash_foreign or 0.0,
            max_per_stock=max_per_stock,
            max_per_stock_foreign=max_per_stock_foreign or 0.0,
            volume_rank_data=self._format_data(volume_rank),
            surge_data=self._format_data(surge_data),
            drop_data=self._format_data(drop_data),
            holdings_data=self._format_holdings(holdings),
            holding_count=len(holdings),
            performance_summary=performance_summary,
        )

        try:
            result_text, provider = await llm_factory.generate_tier1(
                prompt,
                system_prompt=get_market_scan_system(primary_market),
                profile=Tier1Profile.SCAN,
                scope=market_scope(target),
                phase="cycle",
            )
            parsed = self._parse_json_response(result_text)
            selected = parsed.get("selected", [])
            stock_lookup = self._build_stock_lookup(volume_rank, surge_data, drop_data)
            for item in selected:
                item["market"] = normalize_market(item.get("market", primary_market), default=primary_market)
            selected, selected_affordability_dropped = self._filter_selected_by_available_cash(
                selected,
                stock_lookup,
                available_cash,
                available_cash_foreign,
                fx_rates,
                primary_market,
            )
            selected = self._apply_product_policy(selected)
            if regular_floor_target > 0:
                desired_selected_count = min(
                    regular_floor_target,
                    len(regular_floor_candidate_pool),
                )
                selected, backfilled_count = self._backfill_selected_candidates(
                    selected,
                    regular_floor_candidate_pool,
                    desired_selected_count,
                )
                regular_floor_stats["backfilled_count"] = backfilled_count
                regular_floor_stats["candidate_pool_count"] = len(regular_floor_candidate_pool)
                regular_floor_stats["unmet_floor"] = len(selected) < regular_floor_target
            elapsed = activity_logger.elapsed_ms(timer)

            logger.info(
                "시장 스캔+선별 완료 ({} / {}): {}개 선정 (데이터 {}ms + AI {}ms)",
                primary_market, provider, len(selected), data_elapsed, elapsed - data_elapsed,
            )

            # 활동 로그 요약
            selected_lines = []
            for s in selected[:8]:
                name = s.get("name", s.get("symbol", "?"))
                strategy = s.get("strategy_type", "")
                reason = s.get("reason", "")
                line = f"  {name} [{strategy}]"
                if reason:
                    line += f" — {reason}"
                selected_lines.append(line)

            summary_text = f"\U0001f4e1 시장 스캔 완료: {len(selected)}개 선정"
            if selected_lines:
                summary_text += "\n" + "\n".join(selected_lines)
            market_analysis = parsed.get("market_analysis", "")
            if market_analysis:
                summary_text += f"\n   시장: {market_analysis}"

            await activity_logger.log(
                ActivityType.SCAN, ActivityPhase.COMPLETE,
                summary_text,
                cycle_id=cycle_id,
                detail={
                    "selected_count": len(selected),
                    "selected": selected,
                    "market_regime": parsed.get("market_regime", ""),
                    "market_analysis": market_analysis,
                    "available_cash": available_cash,
                    "available_cash_foreign": available_cash_foreign,
                    "markets": scan_markets,
                    "affordability_filter": {
                        **affordability_stats,
                        "selected_dropped": selected_affordability_dropped,
                    },
                    "junk_filter": junk_filter_stats,
                    "opening_guard": opening_guard_stats,
                    "expansion": expansion_stats,
                    "regular_floor": regular_floor_stats,
                },
                llm_provider=provider,
                llm_tier="TIER1",
                execution_time_ms=elapsed,
            )

            return {
                "selected": selected,
                "market_summary": parsed.get("market_analysis", ""),
                "market_regime": parsed.get("market_regime", ""),
                "market_analysis": parsed.get("market_analysis", ""),
                "leading_sectors": parsed.get("leading_sectors", []),
                "available_cash": available_cash,
                "available_cash_foreign": available_cash_foreign,
                "max_per_stock": max_per_stock,
                "max_per_stock_foreign": max_per_stock_foreign,
                "provider": provider,
                "markets": scan_markets,
                "regular_floor": regular_floor_stats,
            }
        except Exception as e:
            elapsed = activity_logger.elapsed_ms(timer)
            err_msg = str(e) or repr(e)
            logger.error("시장 스캔 AI 분석 실패 ({}): {}", type(e).__name__, err_msg)
            await activity_logger.log(
                ActivityType.SCAN, ActivityPhase.ERROR,
                f"\u274c 시장 스캔 실패: [{type(e).__name__}] {err_msg[:100]}",
                cycle_id=cycle_id,
                error_message=err_msg,
                execution_time_ms=elapsed,
            )
            return {"selected": [], "market_summary": "스캔 실패", "available_cash": available_cash}

    async def _get_performance_summary(self, market: str | None = None) -> str:
        """과거 매매 성과 요약 텍스트 생성"""
        try:
            scope = market_scope(market or settings.primary_market_code)
            async with AsyncSessionLocal() as session:
                tracker = PerformanceTracker(session)
                stats = await tracker.get_overall_stats(market_scope=scope)

            overall = stats.get("overall")
            if not overall or overall.total_trades == 0:
                return "매매 이력 없음"

            lines = [
                f"총 {overall.total_trades}거래, "
                f"승률 {overall.win_rate * 100:.1f}%, "
                f"총손익 {overall.total_pnl:+,.0f}원, "
                f"평균수익률 {overall.avg_return:+.2f}%"
            ]

            by_strategy = stats.get("by_strategy", {})
            for strategy_type, stat in by_strategy.items():
                lines.append(
                    f"  - {strategy_type}: {stat.total_trades}거래, "
                    f"승률 {stat.win_rate * 100:.1f}%, "
                    f"평균수익률 {stat.avg_return:+.2f}%"
                )

            return "\n".join(lines)
        except Exception as e:
            logger.warning("성과 요약 조회 실패: {}", str(e))
            return "매매 이력 없음"

    async def _get_volume_rank(
        self,
        markets: list[str],
        *,
        limit: int = _US_BASE_SCAN_LIMIT,
        include_trade_growth: bool = False,
    ) -> list[dict]:
        responses = await asyncio.gather(
            *[
                mcp_client.get_volume_rank(
                    market=market,
                    limit=limit,
                    include_trade_growth=include_trade_growth,
                )
                for market in markets
            ],
            return_exceptions=True,
        )
        stocks = self._merge_scan_stocks(markets, responses)
        stocks.sort(key=lambda item: float(item.get("volume", 0)), reverse=True)
        return stocks[:limit]

    async def _get_fluctuation_rank(
        self,
        markets: list[str],
        sort: str,
        *,
        limit: int = _US_BASE_SCAN_LIMIT,
    ) -> list[dict]:
        responses = await asyncio.gather(
            *[
                mcp_client.get_fluctuation_rank(
                    market=market,
                    sort=sort,
                    limit=limit,
                )
                for market in markets
            ],
            return_exceptions=True,
        )
        stocks = self._merge_scan_stocks(markets, responses)
        stocks.sort(key=lambda item: float(item.get("change_rate", 0)), reverse=(sort != "bottom"))
        return stocks[:limit]

    def _merge_scan_stocks(self, markets: list[str], responses: list) -> list[dict]:
        """시장별 스캔 결과 병합"""
        merged: list[dict] = []
        seen: set[tuple[str, str]] = set()

        for market, response in zip(markets, responses, strict=False):
            if isinstance(response, Exception) or not getattr(response, "success", False):
                continue
            data = response.data or {}
            stocks = data.get("stocks", data.get("items", []))
            for item in stocks:
                symbol = item.get("symbol", "")
                item_market = normalize_market(item.get("market", market), default=market)
                key = (item_market, symbol)
                if not symbol or key in seen:
                    continue
                seen.add(key)
                merged.append({**item, "market": item_market})
        return self._filter_untradeable(merged)

    def _format_data(self, data: list[dict]) -> str:
        if not data:
            return "데이터 없음"
        lines = []
        for i, item in enumerate(data[:15], 1):
            symbol = item.get("symbol", item.get("code", ""))
            name = item.get("name", "")
            price = item.get("price", item.get("current_price", ""))
            change_rate = item.get("change_rate", "")
            volume = item.get("volume", "")
            market = item.get("market", "")
            currency = item.get("currency", "KRW")
            unit = "원" if currency == "KRW" else currency
            market_text = f"[{market}] " if market else ""
            lines.append(f"{i}. {market_text}{name}({symbol}) {price}{unit} {change_rate}% 거래량:{volume}")
        return "\n".join(lines)

    def _format_holdings(self, holdings) -> str:
        if not holdings:
            return "보유 종목 없음"
        lines = []
        for h in holdings:
            market_text = f"[{h.market}] " if getattr(h, "market", "") else ""
            currency = getattr(h, "currency", "KRW")
            unit = "원" if currency == "KRW" else currency
            lines.append(
                f"- {market_text}{h.name}({h.symbol}) {h.quantity}주 "
                f"평균단가:{h.avg_buy_price:,.2f}{unit} 수익률:{h.pnl_rate:+.2f}%"
            )
        return "\n".join(lines)

    def _parse_json_response(self, text: str) -> dict:
        from core.json_utils import parse_llm_json
        return parse_llm_json(text)


market_scanner = MarketScanner()
