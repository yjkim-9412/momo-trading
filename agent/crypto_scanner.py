"""빗썸 코인 시장 스캐너 — MarketScannerProtocol 구현체

bithumb_client를 통해 전체 코인 시세를 수집하고,
거래대금/급등·급락 데이터를 LLM에 전달하여 매매 후보를 선별한다.
코인 시장은 24/7이므로 세션·매수 마감 개념 없이 항상 5~8개 후보를 선정한다.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from analysis.feedback.performance_tracker import PerformanceTracker
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.market_scan import (
    get_market_label,
    get_market_scan_prompt,
    get_market_scan_system,
)
from core.config import settings
from core.database import AsyncSessionLocal
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType, Tier1Profile
from trading.market_profile import market_scope, normalize_market
from trading.models import MCPResponse

# 크립토 전용 스캔 시스템 프롬프트
CRYPTO_SCAN_SYSTEM = """당신은 암호화폐(코인) 시장 전문 스크리너입니다.
빗썸 거래소의 시장 데이터를 분석하여 단기 매매(수시간~수일) 후보 코인을 선별하고 전략을 배정합니다.

## 분석 프레임워크
반드시 아래 순서로 분석하세요:

**Step 1. 시장 국면 판단** — 현재 코인 시장 전체 흐름
  - BULL_RUN: BTC 및 대다수 알트코인 동반 상승, 거래대금 급증
  - BEAR: BTC 하락 주도, 전반적 하락세, 공포 심리
  - SIDEWAYS: 방향성 불명확, 거래량 감소, 좁은 범위 등락
  - ALT_SEASON: BTC 횡보/약보합 중 알트코인에 자금 집중
  - THEME: 특정 섹터(AI, L2, DeFi, 밈코인 등)에 거래량 집중

**Step 2. 코인 선정 + 전략 배정** — 시장 국면에 맞는 코인 선별
  - 안정형(STABLE_SHORT): BTC, ETH 등 대형 코인, 지지선 부근, 변동성 비교적 낮음
  - 공격형(AGGRESSIVE_SHORT): 거래대금 급증, 강한 상승 모멘텀, 알트코인 급등
  - BULL_RUN → 대형+알트 혼합 / BEAR → 대형 코인 위주 또는 0개 허용
  - ALT_SEASON → 거래대금 상위 알트코인 AGGRESSIVE_SHORT

**Step 3. 코인 고유 리스크**
  - 24/7 시장이므로 오버나이트 갭 리스크는 없지만, 급변동 리스크가 큼
  - BTC 도미넌스와 알트코인 상관관계 고려
  - 거래대금이 매우 작은 코인은 유동성 리스크로 제외

## 핵심 원칙
- 제공된 데이터만 사용 (추측 금지)
- 투자 가용 금액 고려
- 과거 손실 패턴 회피
- **절대 규칙**: 반드시 제공된 데이터에 있는 코인만 선정
- 반드시 한국어로 답변
- **간결하게**: JSON만 출력, 부연 설명 불필요"""

CRYPTO_SCAN_PROMPT = """## 코인 시장 데이터

현재 시각(KST): {current_time} | 시장: 24시간 운영 (매수 제한 없음)
투자 가용 현금: {available_cash:,.0f}원 | 코인당 최대: {max_per_stock:,.0f}원
보유 코인 수: {holding_count}개
이번 스캔 선정 목표: {selection_target_range}개 (적합한 후보가 없으면 0개 허용)

### 거래대금 상위
{volume_rank_data}

### 급등 코인
{surge_data}

### 급락 코인
{drop_data}

### 보유 코인
{holdings_data}

### 매매 성과
{performance_summary}

---

위 데이터를 분석하여 코인 시장 국면을 판단하고, **심층 분석할 코인을 {selection_target_range}개 범위에서** 직접 선정하세요.
각 코인에 적합한 전략(STABLE_SHORT/AGGRESSIVE_SHORT)을 배정하세요.

JSON:
```json
{{
  "market_regime": "BULL_RUN/BEAR/SIDEWAYS/ALT_SEASON/THEME",
  "market_analysis": "코인 시장 상황 1~2줄 요약",
  "leading_sectors": ["주도 섹터 (예: L1, DeFi, 밈코인)"],
  "selected": [
    {{
      "symbol": "코인심볼 (예: BTC)",
      "name": "코인명 (예: 비트코인)",
      "market": "BITHUMB",
      "strategy_type": "STABLE_SHORT 또는 AGGRESSIVE_SHORT",
      "reason": "선정 근거 1줄",
      "category": "대형코인/알트코인/밈코인 등",
      "monitoring": {{"surge_pct": 8.0, "drop_pct": -5.0, "volume_spike_ratio": 3.0}}
    }}
  ]
}}
```"""

# 선정 목표 범위 (24/7 시장이므로 고정)
_CRYPTO_SELECTION_TARGET_RANGE = "5~8"


class CryptoScanner:
    """빗썸 코인 시장 스캐너 -- MarketScannerProtocol 구현체"""

    def __init__(self) -> None:
        self._untradeable_symbols: set[str] = set()

    def add_untradeable(self, symbol: str, market: str = "BITHUMB") -> None:
        """매매불가 코인을 런타임 블록리스트에 등록 (당일 스캔에서 제외)"""
        self._untradeable_symbols.add(symbol.upper())
        logger.info(
            "크립토 매매불가 블록리스트 등록: {} (총 {}건)",
            symbol, len(self._untradeable_symbols),
        )

    def _filter_untradeable(self, coins: list[dict]) -> list[dict]:
        """매매불가 코인 필터링"""
        if not self._untradeable_symbols:
            return coins
        filtered = [c for c in coins if c.get("symbol", "").upper() not in self._untradeable_symbols]
        if len(filtered) < len(coins):
            logger.info("크립토 매매불가 필터: {}건 -> {}건", len(coins), len(filtered))
        return filtered

    async def scan(
        self,
        *,
        market: str = "BITHUMB",
        cycle_id: str | None = None,
        dynamic_limits: dict[str, Any] | None = None,
        account_snapshot: tuple | None = None,
    ) -> dict[str, Any]:
        """코인 시장 스캔 + AI 종목 선별

        1. 빗썸 전체 overview 조회
        2. discovery 후보(거래대금/상승률/하락률) 계산
        3. CRYPTO_WATCHLIST_SYMBOLS 시드 병합
        4. _untradeable 필터링
        5. 성과 데이터 로드 (PerformanceTracker)
        6. LLM 스캔 프롬프트로 후보 선별
        7. JSON 파싱 + source 메타데이터 재결합
        """
        target = normalize_market(market, default="BITHUMB")

        if not settings.CRYPTO_ENABLED:
            logger.info("크립토 비활성화 -> 시장 스캔 스킵")
            return {
                "selected": [],
                "market_summary": "크립토 비활성화",
                "market_regime": "",
                "available_cash": 0,
                "scan_source": "crypto_overview",
            }

        logger.info("크립토 시장 스캔 시작: {}", target)
        timer = activity_logger.timer()

        await activity_logger.log(
            ActivityType.SCAN, ActivityPhase.START,
            f"\U0001f4e1 크립토 시장 스캔 중... {get_market_label(target)} 전체 코인 시세 조회",
            cycle_id=cycle_id,
        )

        # --- 1. 데이터 수집 (빗썸 API + 계좌 + 성과) ---
        bithumb_client = _get_bithumb_client()
        if bithumb_client is None:
            logger.error("bithumb_client를 불러올 수 없습니다")
            await activity_logger.log(
                ActivityType.SCAN, ActivityPhase.ERROR,
                "\u274c 크립토 스캔 실패: bithumb_client 미설치",
                cycle_id=cycle_id,
            )
            return {
                "selected": [],
                "market_summary": "bithumb_client 미설치",
                "market_regime": "",
                "available_cash": 0,
                "scan_source": "crypto_overview",
            }

        if account_snapshot is None:
            (
                account_snapshot,
                overview_data,
                performance_summary,
            ) = await asyncio.gather(
                account_manager.get_account_snapshot(target),
                _safe_call(bithumb_client.get_market_overview),
                self._get_performance_summary(target),
            )
        else:
            (
                overview_data,
                performance_summary,
            ) = await asyncio.gather(
                _safe_call(bithumb_client.get_market_overview),
                self._get_performance_summary(target),
            )
        if overview_data is None:
            overview_data = MCPResponse(success=False, error="market overview unavailable")

        volume_rank_data, surge_data = await asyncio.gather(
            _safe_call(
                bithumb_client.get_volume_rank,
                market=target,
                overview_data=overview_data,
                limit=settings.CRYPTO_SCAN_LIMIT,
            ),
            _safe_call(
                bithumb_client.get_surge_data,
                market=target,
                overview_data=overview_data,
                limit=settings.CRYPTO_SCAN_LIMIT,
            ),
        )

        balance, holdings = account_snapshot
        available_cash = balance.effective_cash
        max_pos_pct = settings.CRYPTO_MAX_POSITION_PCT / 100
        if dynamic_limits:
            max_pos_pct = dynamic_limits.get("max_position_pct", settings.CRYPTO_MAX_POSITION_PCT) / 100
        max_per_coin = available_cash * max_pos_pct

        data_elapsed = activity_logger.elapsed_ms(timer)
        logger.info("크립토 데이터 수집 완료: {}ms", data_elapsed)

        # --- 2. 데이터 정리: discovery + watchlist 하이브리드 ---
        discovery_enabled = bool(settings.CRYPTO_DYNAMIC_DISCOVERY_ENABLED)
        watchlist = settings.crypto_watchlist_symbols
        overview_all_coins = self._extract_coins(overview_data, limit=10_000)
        if not overview_all_coins:
            overview_all_coins = await self._build_watchlist_fallback_coins(
                bithumb_client,
                watchlist=settings.crypto_watchlist_symbols,
                holdings=holdings,
            )
            if overview_all_coins:
                logger.warning(
                    "크립토 overview 조회 실패 → watchlist/보유 코인 기반 degraded 스캔 사용: {}개",
                    len(overview_all_coins),
                )

        fallback_only = bool(overview_all_coins and str(overview_all_coins[0].get("scan_source", "")).endswith("_FALLBACK"))
        normalized_overview = (
            list(overview_all_coins)
            if fallback_only
            else [self._normalize_scan_candidate(item, scan_source="DISCOVERY") for item in overview_all_coins]
        )

        volume_discovery = []
        surge_discovery = []
        drop_discovery = []
        if discovery_enabled and normalized_overview and not fallback_only:
            volume_discovery = [
                self._normalize_scan_candidate(item, scan_source="DISCOVERY")
                for item in self._extract_coins(volume_rank_data, limit=settings.CRYPTO_SCAN_LIMIT)
            ]
            if not volume_discovery:
                volume_discovery = sorted(
                    normalized_overview,
                    key=lambda c: c.get("trade_value", 0.0),
                    reverse=True,
                )[: settings.CRYPTO_SCAN_LIMIT]

            surge_discovery = [
                self._normalize_scan_candidate(item, scan_source="DISCOVERY")
                for item in self._extract_coins(surge_data, limit=settings.CRYPTO_SCAN_LIMIT)
            ]
            if not surge_discovery:
                surge_discovery = sorted(
                    normalized_overview,
                    key=lambda c: c.get("change_rate", 0.0),
                    reverse=True,
                )[: settings.CRYPTO_SCAN_LIMIT]

            drop_discovery = sorted(
                normalized_overview,
                key=lambda c: c.get("change_rate", 0.0),
            )[: settings.CRYPTO_SCAN_LIMIT]

        watchlist_candidates = (
            []
            if fallback_only
            else self._build_watchlist_candidates(normalized_overview, watchlist)
        )
        if fallback_only:
            watchlist_candidates = list(normalized_overview)

        watchlist_by_volume = sorted(
            watchlist_candidates,
            key=lambda c: c.get("trade_value", 0.0),
            reverse=True,
        )
        watchlist_by_surge = sorted(
            watchlist_candidates,
            key=lambda c: c.get("change_rate", 0.0),
            reverse=True,
        )
        watchlist_by_drop = sorted(
            watchlist_candidates,
            key=lambda c: c.get("change_rate", 0.0),
        )

        volume_coins = self._merge_discovery_and_watchlist(
            volume_discovery,
            watchlist_by_volume,
            limit=settings.CRYPTO_SCAN_LIMIT,
        )
        surge_coins = self._merge_discovery_and_watchlist(
            surge_discovery,
            watchlist_by_surge,
            limit=settings.CRYPTO_SCAN_LIMIT,
        )
        drop_coins = self._merge_discovery_and_watchlist(
            drop_discovery,
            watchlist_by_drop,
            limit=settings.CRYPTO_SCAN_LIMIT,
        )

        if not volume_coins and not surge_coins and not drop_coins:
            overview_error = ""
            if hasattr(overview_data, "error"):
                overview_error = str(overview_data.error or "")
            logger.error("크립토 스캔 시장 데이터 부족: {}", overview_error or "overview/보조 입력 모두 비어있음")
            await activity_logger.log(
                ActivityType.SCAN,
                ActivityPhase.ERROR,
                "❌ 크립토 스캔 실패: 시장 데이터 unavailable",
                cycle_id=cycle_id,
                detail={
                    "reason": "market_data_unavailable",
                    "overview_error": overview_error,
                    "watchlist_size": len(settings.crypto_watchlist_symbols),
                    "holding_count": len(holdings),
                    "discovery_enabled": discovery_enabled,
                },
                execution_time_ms=data_elapsed,
            )
            return {
                "selected": [],
                "market_summary": "시장 데이터 unavailable",
                "market_regime": "",
                "available_cash": available_cash,
                "scan_source": "crypto_overview",
            }

        # --- 3. 매매불가 필터링 ---
        volume_coins = self._filter_untradeable(volume_coins)
        surge_coins = self._filter_untradeable(surge_coins)
        drop_coins = self._filter_untradeable(drop_coins)

        candidate_pool = self._merge_unique_candidates(
            volume_coins,
            surge_coins,
            drop_coins,
            limit=settings.CRYPTO_SCAN_LIMIT * 3,
        )

        # --- 4. LLM 스캔 프롬프트 구성 ---
        from util.time_util import now_kst

        now = now_kst()

        prompt = CRYPTO_SCAN_PROMPT.format(
            current_time=now.strftime("%H:%M"),
            available_cash=available_cash,
            max_per_stock=max_per_coin,
            holding_count=len(holdings),
            selection_target_range=_CRYPTO_SELECTION_TARGET_RANGE,
            volume_rank_data=self._format_coin_data(volume_coins),
            surge_data=self._format_coin_data(surge_coins),
            drop_data=self._format_coin_data(drop_coins),
            holdings_data=self._format_holdings(holdings),
            performance_summary=performance_summary,
        )

        # --- 5. LLM 호출 + 결과 파싱 ---
        try:
            result_text, provider = await llm_factory.generate_tier1(
                prompt,
                system_prompt=CRYPTO_SCAN_SYSTEM,
                profile=Tier1Profile.SCAN,
                scope=market_scope(target),
                phase="cycle",
            )
            parsed = self._parse_json_response(result_text)
            selected = self._enrich_selected_candidates(
                parsed.get("selected", []),
                candidate_pool=candidate_pool,
                market=target,
                default_scan_source="DISCOVERY" if discovery_enabled and not fallback_only else "WATCHLIST",
            )
            selected_count_by_source = self._count_by_source(selected)

            elapsed = activity_logger.elapsed_ms(timer)
            logger.info(
                "크립토 스캔+선별 완료 ({} / {}): {}개 선정 (데이터 {}ms + AI {}ms)",
                target, provider, len(selected), data_elapsed, elapsed - data_elapsed,
            )

            # 활동 로그 요약
            selected_lines = []
            for s in selected[:8]:
                name = s.get("name", s.get("symbol", "?"))
                strategy = s.get("strategy_type", "")
                reason = s.get("reason", "")
                line = f"  {name} [{strategy}]"
                if reason:
                    line += f" \u2014 {reason}"
                selected_lines.append(line)

            summary_text = f"\U0001f4e1 크립토 스캔 완료: {len(selected)}개 선정"
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
                    "selected_count_by_source": selected_count_by_source,
                    "discovery_enabled": discovery_enabled,
                    "discovery_count": len([c for c in candidate_pool if c.get("scan_source") == "DISCOVERY"]),
                    "watchlist_count": len([c for c in candidate_pool if c.get("scan_source") != "DISCOVERY"]),
                    "market_regime": parsed.get("market_regime", ""),
                    "market_analysis": market_analysis,
                    "available_cash": available_cash,
                    "scan_source": "crypto_overview",
                },
                llm_provider=provider,
                llm_tier="TIER1",
                execution_time_ms=elapsed,
            )

            return {
                "selected": selected,
                "market_summary": market_analysis,
                "market_regime": parsed.get("market_regime", ""),
                "market_analysis": market_analysis,
                "leading_sectors": parsed.get("leading_sectors", []),
                "available_cash": available_cash,
                "max_per_stock": max_per_coin,
                "provider": provider,
                "scan_source": "crypto_overview",
                "selected_count_by_source": selected_count_by_source,
            }
        except Exception as e:
            elapsed = activity_logger.elapsed_ms(timer)
            err_msg = str(e) or repr(e)
            logger.error("크립토 스캔 AI 분석 실패 ({}): {}", type(e).__name__, err_msg)
            await activity_logger.log(
                ActivityType.SCAN, ActivityPhase.ERROR,
                f"\u274c 크립토 스캔 실패: [{type(e).__name__}] {err_msg[:100]}",
                cycle_id=cycle_id,
                error_message=err_msg,
                execution_time_ms=elapsed,
            )
            return {
                "selected": [],
                "market_summary": "크립토 스캔 실패",
                "market_regime": "",
                "available_cash": available_cash,
                "scan_source": "crypto_overview",
            }

    # ------------------------------------------------------------------
    # 내부 유틸리티
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_coins(data: Any, *, limit: int = 15) -> list[dict]:
        """빗썸 API 응답에서 코인 리스트를 추출한다.

        data가 MCP 응답 객체(success/data 속성)이면 내부 data를 꺼내고,
        dict이면 stocks/items/data 키를 탐색하며,
        list이면 그대로 사용한다.
        """
        if data is None:
            return []

        # MCP 스타일 응답 객체
        if hasattr(data, "success"):
            if not data.success:
                return []
            raw = data.data if hasattr(data, "data") else data
        else:
            raw = data

        if isinstance(raw, list):
            return raw[:limit]

        if isinstance(raw, dict):
            for key in ("stocks", "items", "data", "coins"):
                items = raw.get(key)
                if isinstance(items, list):
                    return items[:limit]
            # dict 자체가 코인별 데이터인 경우 (예: {"BTC": {...}, "ETH": {...}})
            if all(isinstance(v, dict) for v in raw.values()):
                coins: list[dict] = []
                for symbol, info in raw.items():
                    entry = {**info, "symbol": symbol}
                    coins.append(entry)
                return sorted(
                    coins,
                    key=lambda c: float(c.get("volume", c.get("acc_trade_value_24h", 0))),
                    reverse=True,
                )[:limit]

        return []

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _normalize_scan_candidate(cls, item: dict[str, Any], *, scan_source: str) -> dict[str, Any]:
        symbol = str(item.get("symbol", item.get("code", "")) or "").upper()
        price = cls._to_float(item.get("price", item.get("closing_price", item.get("current_price", 0))))
        change_rate = cls._to_float(item.get("change_rate", item.get("fluctate_rate_24H", 0)))
        volume = cls._to_float(item.get("volume", item.get("acc_trade_value_24h", item.get("acc_trade_value", 0))))
        trade_value = cls._to_float(item.get("trade_value", item.get("acc_trade_value_24h", item.get("volume", 0))))
        normalized = dict(item)
        normalized.update({
            "symbol": symbol,
            "name": str(item.get("name") or item.get("korean_name") or symbol),
            "market": "BITHUMB",
            "price": price,
            "change_rate": change_rate,
            "volume": volume,
            "trade_value": trade_value,
            "scan_source": str(item.get("scan_source") or scan_source).upper(),
        })
        return normalized

    @classmethod
    def _build_watchlist_candidates(
        cls,
        overview_coins: list[dict[str, Any]],
        watchlist: list[str],
    ) -> list[dict[str, Any]]:
        overview_map = {
            str(coin.get("symbol", "")).upper(): cls._normalize_scan_candidate(coin, scan_source="DISCOVERY")
            for coin in overview_coins
            if coin.get("symbol")
        }
        watchlist_candidates: list[dict[str, Any]] = []
        for symbol in watchlist:
            sym = str(symbol or "").upper()
            base = overview_map.get(sym)
            if not base:
                continue
            watchlist_candidates.append({
                **base,
                "scan_source": "WATCHLIST",
            })
        return watchlist_candidates

    @staticmethod
    def _merge_discovery_and_watchlist(
        discovery_coins: list[dict[str, Any]],
        watchlist_coins: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        watchlist_unique: list[dict[str, Any]] = []
        watchlist_symbols: set[str] = set()
        for coin in watchlist_coins:
            symbol = str(coin.get("symbol", "")).upper()
            if not symbol or symbol in watchlist_symbols:
                continue
            watchlist_symbols.add(symbol)
            watchlist_unique.append(coin)

        watchlist_discovery: list[dict[str, Any]] = []
        discovery_rest: list[dict[str, Any]] = []
        seen_discovery: set[str] = set()
        for coin in discovery_coins:
            symbol = str(coin.get("symbol", "")).upper()
            if not symbol or symbol in seen_discovery:
                continue
            seen_discovery.add(symbol)
            if symbol in watchlist_symbols:
                watchlist_discovery.append(coin)
            else:
                discovery_rest.append(coin)

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for coin in watchlist_discovery + watchlist_unique:
            symbol = str(coin.get("symbol", "")).upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            merged.append(coin)
            if len(merged) >= limit:
                break

        if len(merged) < limit:
            for coin in discovery_rest:
                symbol = str(coin.get("symbol", "")).upper()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                merged.append(coin)
                if len(merged) >= limit:
                    break
        return merged

    @staticmethod
    def _merge_unique_candidates(
        *candidate_groups: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in candidate_groups:
            for coin in group:
                symbol = str(coin.get("symbol", "")).upper()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                merged.append(coin)
                if len(merged) >= limit:
                    return merged
        return merged

    @staticmethod
    def _count_by_source(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            source = str(item.get("scan_source", "UNKNOWN") or "UNKNOWN").upper()
            counts[source] = counts.get(source, 0) + 1
        return counts

    @staticmethod
    def _enrich_selected_candidates(
        selected: list[dict[str, Any]],
        *,
        candidate_pool: list[dict[str, Any]],
        market: str,
        default_scan_source: str,
    ) -> list[dict[str, Any]]:
        candidate_map = {
            str(item.get("symbol", "")).upper(): item
            for item in candidate_pool
            if item.get("symbol")
        }
        enriched: list[dict[str, Any]] = []
        for item in selected:
            symbol = str(item.get("symbol", "")).upper()
            if not symbol:
                continue
            base = candidate_map.get(symbol, {})
            merged = {
                **base,
                **item,
                "symbol": symbol,
                "name": str(item.get("name") or base.get("name") or symbol),
                "market": normalize_market(item.get("market", market), default=market),
                "scan_source": str(
                    item.get("scan_source")
                    or base.get("scan_source")
                    or default_scan_source
                ).upper(),
            }
            enriched.append(merged)
        return enriched

    @staticmethod
    def _format_coin_data(coins: list[dict]) -> str:
        """코인 리스트를 LLM 프롬프트용 텍스트로 포맷"""
        if not coins:
            return "데이터 없음"
        lines = []
        for i, item in enumerate(coins[:15], 1):
            symbol = item.get("symbol", item.get("code", ""))
            name = item.get("name", symbol)
            price = item.get("price", item.get("closing_price", item.get("current_price", "")))
            change_rate = item.get("change_rate", item.get("fluctate_rate_24H", ""))
            volume = item.get("volume", item.get("acc_trade_value_24h", item.get("acc_trade_value", "")))
            lines.append(
                f"{i}. {name}({symbol}) {price}원 {change_rate}% 24h거래대금:{volume}"
            )
        return "\n".join(lines)

    async def _build_watchlist_fallback_coins(
        self,
        bithumb_client: Any,
        *,
        watchlist: list[str],
        holdings: list[Any],
    ) -> list[dict]:
        """overview 실패 시 watchlist/보유 코인 현재가로 최소 스캔 입력 구성"""
        fallback_items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for holding in holdings:
            symbol = ""
            if isinstance(holding, dict):
                symbol = str(holding.get("symbol", "") or "").upper()
            else:
                symbol = str(getattr(holding, "symbol", "") or "").upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                fallback_items.append((symbol, "HOLDING_FALLBACK"))
        for symbol in watchlist:
            sym = str(symbol or "").upper()
            if sym and sym not in seen:
                seen.add(sym)
                fallback_items.append((sym, "WATCHLIST_FALLBACK"))
        if not fallback_items:
            return []

        responses = await asyncio.gather(*[
            _safe_call(bithumb_client.get_current_price, symbol, market="BITHUMB")
            for symbol, _ in fallback_items
        ])
        coins: list[dict] = []
        for (symbol, scan_source), resp in zip(fallback_items, responses, strict=False):
            if not hasattr(resp, "success") or not resp.success or not resp.data:
                continue
            data = resp.data
            coins.append({
                "symbol": symbol,
                "name": data.get("name") or symbol,
                "market": "BITHUMB",
                "price": float(data.get("price", 0) or 0),
                "change_rate": float(data.get("change_rate", 0) or 0),
                "volume": float(data.get("trade_value", data.get("volume", 0)) or 0),
                "trade_value": float(data.get("trade_value", data.get("volume", 0)) or 0),
                "scan_source": scan_source,
            })
        return coins

    @staticmethod
    def _format_holdings(holdings: Any) -> str:
        """보유 코인 목록을 LLM 프롬프트용 텍스트로 포맷"""
        if not holdings:
            return "보유 코인 없음"
        lines = []
        for h in holdings:
            name = getattr(h, "name", "") or getattr(h, "symbol", "?")
            symbol = getattr(h, "symbol", "?")
            quantity = getattr(h, "quantity", 0)
            avg_buy_price = getattr(h, "avg_buy_price", 0)
            pnl_rate = getattr(h, "pnl_rate", 0)
            lines.append(
                f"- {name}({symbol}) {quantity}개 "
                f"평균단가:{avg_buy_price:,.0f}원 수익률:{pnl_rate:+.2f}%"
            )
        return "\n".join(lines)

    async def _get_performance_summary(self, market: str) -> str:
        """과거 매매 성과 요약 텍스트 생성"""
        try:
            scope = market_scope(market)
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
            logger.warning("크립토 성과 요약 조회 실패: {}", str(e))
            return "매매 이력 없음"

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        """LLM 응답에서 JSON을 파싱"""
        from core.json_utils import parse_llm_json
        return parse_llm_json(text)


# ------------------------------------------------------------------
# bithumb_client lazy import (아직 모듈이 없을 수 있음)
# ------------------------------------------------------------------

def _get_bithumb_client() -> Any | None:
    """bithumb_client 싱글톤을 안전하게 반환한다."""
    try:
        from trading.bithumb_client import bithumb_client
        return bithumb_client
    except (ImportError, ModuleNotFoundError):
        logger.warning("trading.bithumb_client를 불러올 수 없습니다 (미설치 또는 미구현)")
        return None


async def _safe_call(coro_func: Any, *args: Any, **kwargs: Any) -> Any:
    """빗썸 API 비동기 호출을 안전하게 실행한다 (예외 시 None 반환)."""
    try:
        return await coro_func(*args, **kwargs)
    except Exception as e:
        logger.warning("빗썸 API 호출 실패 ({}): {}", type(e).__name__, str(e)[:200])
        return None


# 모듈 레벨 싱글톤
crypto_scanner = CryptoScanner()
