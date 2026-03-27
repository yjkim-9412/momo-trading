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

    def _apply_product_policy(self, selected: list[dict]) -> list[dict]:
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

        if dropped or adjusted:
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
    def _selection_target_range(
        market: str,
        session: str,
        minutes_until_cutoff: int,
    ) -> str:
        market_code = normalize_market(market)
        session_code = str(session or "").upper()
        if is_us_market(market_code) and session_code == "US_PRE":
            return "3~6"
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

        scan_markets = settings.scan_markets_for(target)
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
        all_candidates = [*volume_rank, *surge_data, *drop_data]
        fx_rates = await self._build_affordability_fx_rates(all_candidates)
        available_cash_foreign = self._resolve_available_cash_foreign(
            target,
            balance,
        )
        max_per_stock_foreign = (
            available_cash_foreign * max_pos_pct
            if available_cash_foreign is not None
            else None
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

        data_elapsed = activity_logger.elapsed_ms(timer)
        logger.info("MCP 데이터 수집 완료: {}ms", data_elapsed)
        if volume_dropped or surge_dropped or drop_dropped:
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
            market_summary = "가용 현금 기준 1주 매수 가능 후보 없음"
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
            }

        # 2. AI 시장 분석 + 종목 선별 (통합 1회 호출)
        from util.time_util import now_kst
        from core.config import settings as _settings
        from zoneinfo import ZoneInfo
        from trading.market_profile import market_timezone

        now = now_kst().astimezone(ZoneInfo(market_timezone(target)))
        mkt_cfg = _settings.get_market_config(target)
        cutoff_time = now.replace(
            hour=mkt_cfg["buy_cutoff_hour"],
            minute=mkt_cfg["buy_cutoff_minute"],
            second=0, microsecond=0,
        )
        minutes_until_cutoff = max(0, int((cutoff_time - now).total_seconds() / 60))
        session = market_calendar.get_market_session(dt=now, market=target)
        selection_target_range = self._selection_target_range(
            target,
            session,
            minutes_until_cutoff,
        )

        prompt = get_market_scan_prompt(primary_market).format(
            market_label=get_market_label(primary_market),
            current_time=now.strftime("%H:%M"),
            timezone_label=now.tzname() or "LOCAL",
            market_session=session,
            minutes_until_cutoff=minutes_until_cutoff,
            selection_target_range=selection_target_range,
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

    async def _get_volume_rank(self, markets: list[str]) -> list[dict]:
        responses = await asyncio.gather(
            *[mcp_client.get_volume_rank(market=market) for market in markets],
            return_exceptions=True,
        )
        stocks = self._merge_scan_stocks(markets, responses)
        stocks.sort(key=lambda item: float(item.get("volume", 0)), reverse=True)
        return stocks[:30]

    async def _get_fluctuation_rank(self, markets: list[str], sort: str) -> list[dict]:
        responses = await asyncio.gather(
            *[mcp_client.get_fluctuation_rank(market=market, sort=sort) for market in markets],
            return_exceptions=True,
        )
        stocks = self._merge_scan_stocks(markets, responses)
        stocks.sort(key=lambda item: float(item.get("change_rate", 0)), reverse=(sort != "bottom"))
        return stocks[:30]

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
