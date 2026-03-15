"""트레이딩 규칙 엔진 — 리뷰 피드백 → 코드 레벨 하드 강제 자동화"""
from __future__ import annotations

from datetime import timedelta

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionLocal
from models.coin_trading_rule import CoinTradingRule
from models.trading_rule import TradingRule
from trading.market_profile import MARKET_SCOPE_CRYPTO, normalize_market_scope
from trading.risk_policy import normalize_crypto_regime
from util.time_util import now_kst

# ──────────────────────────────────────────────
# 안전 범위: 파라미터별 (min, max)
# ──────────────────────────────────────────────
SAFETY_BOUNDS: dict[str, tuple[float, float]] = {
    "min_confidence": (0.50, 0.90),
    "stop_loss_pct": (-8.0, -1.0),
    "take_profit_pct": (2.0, 15.0),
    "rr_floor": (0.8, 3.0),
    # 토글 (0=off, 1=on)
    "revalidate_rr_ratio": (0.0, 1.0),
    "require_stop_loss_logging": (0.0, 1.0),
}

MAX_ACTIVE_RULES = 20
DEFAULT_EXPIRY_DAYS = 5
STRATEGY_SCOPES = {"ALL", "STABLE_SHORT", "AGGRESSIVE_SHORT"}
STOCK_REGIME_SCOPES = {"ALL", "BULL", "BEAR", "SIDEWAYS", "THEME"}
CRYPTO_REGIME_SCOPES = {"ALL", "BULL_RUN", "BEAR_MARKET", "CONSOLIDATION", "ALTSEASON", "THEME"}

# 부트스트랩: 항상 활성화해야 할 기본 검증 규칙
BOOTSTRAP_RULES = [
    {
        "rule_type": "VALIDATION_TOGGLE",
        "param_name": "revalidate_rr_ratio",
        "param_value": 1.0,
        "reason": "RR 비율 LLM 보고값과 코드 계산값 불일치 방지 (상시 활성)",
        "expires_days": 365,
    },
    {
        "rule_type": "VALIDATION_TOGGLE",
        "param_name": "require_stop_loss_logging",
        "param_value": 1.0,
        "reason": "매수 진입 시 손절가 필수 기록 (상시 활성)",
        "expires_days": 365,
    },
]


class TradingRuleEngine:
    """일일 리뷰 피드백 → 코드 강제 규칙 생성 + 적용"""

    @staticmethod
    def _normalize_scope_value(raw_value: str | None, fallback: str = "ALL") -> str:
        value = str(raw_value or fallback).strip().upper()
        return value or fallback

    @staticmethod
    def _rule_model(market_scope: str):
        scope = normalize_market_scope(market_scope)
        if scope == MARKET_SCOPE_CRYPTO:
            return CoinTradingRule
        return TradingRule

    @staticmethod
    def _scope_filters(rule_model, market_scope: str) -> list:
        if rule_model is TradingRule:
            return [TradingRule.market_scope == normalize_market_scope(market_scope)]
        return []

    @staticmethod
    def _regime_scopes_for_market(market_scope: str) -> set[str]:
        scope = normalize_market_scope(market_scope)
        if scope == MARKET_SCOPE_CRYPTO:
            return CRYPTO_REGIME_SCOPES
        return STOCK_REGIME_SCOPES

    @staticmethod
    def _normalize_regime_scope(raw_scope: str, market_scope: str) -> str:
        scope = normalize_market_scope(market_scope)
        if scope == MARKET_SCOPE_CRYPTO:
            return normalize_crypto_regime(raw_scope) or "ALL"
        return raw_scope

    @staticmethod
    def _build_rule(rule_model, market_scope: str, **kwargs):
        if rule_model is TradingRule:
            return TradingRule(market_scope=normalize_market_scope(market_scope), **kwargs)
        return CoinTradingRule(**kwargs)

    # ──────────────────────────────────────────
    # 규칙 생성
    # ──────────────────────────────────────────
    async def generate_rules_from_review(
        self,
        parsed_review: dict,
        report_date,
        market_scope: str = "KRX",
    ) -> list[TradingRule]:
        """장 마감 리뷰 LLM 출력에서 action_items 파싱 → TradingRule 생성"""
        action_items = parsed_review.get("action_items") or []
        if not action_items:
            logger.info("[TradingRule] action_items 없음 — 규칙 생성 스킵")
            return []

        scope = normalize_market_scope(market_scope)
        rule_model = self._rule_model(scope)
        now = now_kst()
        rules: list[TradingRule | CoinTradingRule] = []

        for item in action_items:
            param_name = item.get("param_name", "")
            if param_name not in SAFETY_BOUNDS:
                logger.warning("[TradingRule] 미지원 파라미터: {}", param_name)
                continue

            raw_scope = (
                item.get("apply_scope")
                or item.get("market_regime")
                or item.get("strategy_type")
                or "ALL"
            )
            normalized_scope = self._normalize_scope_value(raw_scope)
            if param_name == "rr_floor":
                normalized_scope = self._normalize_regime_scope(normalized_scope, scope)
                regime_scopes = self._regime_scopes_for_market(scope)
                if normalized_scope not in regime_scopes:
                    logger.warning(
                        "[TradingRule] rr_floor 적용 범위 오류: {} (허용: {})",
                        normalized_scope,
                        ", ".join(sorted(regime_scopes)),
                    )
                    continue
                target_scope = normalized_scope
            else:
                if normalized_scope not in STRATEGY_SCOPES:
                    logger.warning(
                        "[TradingRule] {} 적용 범위 오류: {} (허용: {})",
                        param_name,
                        normalized_scope,
                        ", ".join(sorted(STRATEGY_SCOPES)),
                    )
                    continue
                target_scope = normalized_scope

            raw_value = float(item.get("param_value", 0))
            lo, hi = SAFETY_BOUNDS[param_name]
            clamped = max(lo, min(hi, raw_value))
            if clamped != raw_value:
                logger.info(
                    "[TradingRule] {} 값 클램핑: {} → {} (범위 {}~{})",
                    param_name, raw_value, clamped, lo, hi,
                )

            rule = self._build_rule(
                rule_model,
                scope,
                rule_type=item.get("rule_type", "PARAM_OVERRIDE"),
                strategy_type=target_scope,
                param_name=param_name,
                param_value=clamped,
                source="DAILY_REVIEW",
                reason=item.get("reason", ""),
                source_report_date=report_date,
                priority=item.get("priority", "MEDIUM"),
                is_active=True,
                expires_at=now + timedelta(days=DEFAULT_EXPIRY_DAYS),
            )
            rules.append(rule)

        if not rules:
            return []

        async with AsyncSessionLocal() as session:
            # 중복 방지: 같은 (param_name, strategy_type) 기존 규칙 비활성화
            for r in rules:
                await session.execute(
                    update(rule_model)
                    .where(
                        *self._scope_filters(rule_model, scope),
                        rule_model.param_name == r.param_name,
                        rule_model.strategy_type == r.strategy_type,
                        rule_model.is_active.is_(True),
                    )
                    .values(is_active=False)
                )
                session.add(r)

            # 활성 규칙 상한 체크
            active_count = len(
                (await session.execute(
                    select(rule_model).where(
                        *self._scope_filters(rule_model, scope),
                        rule_model.is_active.is_(True),
                    )
                )).scalars().all()
            )
            if active_count > MAX_ACTIVE_RULES:
                logger.warning(
                    "[TradingRule] 활성 규칙 {}건 > 상한 {} — LOW 우선순위부터 비활성화",
                    active_count, MAX_ACTIVE_RULES,
                )
                excess = (
                    await session.execute(
                        select(rule_model)
                        .where(
                            *self._scope_filters(rule_model, scope),
                            rule_model.is_active.is_(True),
                        )
                        .order_by(
                            # LOW 먼저 제거, 그 다음 오래된 순
                            rule_model.priority.desc(),
                            rule_model.created_at.asc(),
                        )
                        .limit(active_count - MAX_ACTIVE_RULES)
                    )
                ).scalars().all()
                for old_rule in excess:
                    old_rule.is_active = False

            await session.commit()

        logger.info("[TradingRule] {}건 규칙 생성 완료", len(rules))
        return rules

    # ──────────────────────────────────────────
    # 규칙 로드
    # ──────────────────────────────────────────
    async def load_active_rules(self, market_scope: str = "KRX") -> dict:
        """활성 규칙 로드 → 적용 가능한 구조로 변환

        Returns:
            {
                "param_overrides": {"STABLE_SHORT": {...}, "ALL": {...}},
                "validation_flags": {"revalidate_rr_ratio": True, ...},
                "rr_floor_overrides": {"ALL": 1.2, "THEME": 1.3, ...},
                "rules": [TradingRule, ...],
            }
        """
        now = now_kst()
        scope = normalize_market_scope(market_scope)
        rule_model = self._rule_model(scope)

        async with AsyncSessionLocal() as session:
            # 부트스트랩 규칙 확인 + 생성
            await self._ensure_bootstrap_rules(session, scope, rule_model)

            result = await session.execute(
                select(rule_model).where(
                    *self._scope_filters(rule_model, scope),
                    rule_model.is_active.is_(True),
                    rule_model.expires_at > now,
                )
            )
            rules = list(result.scalars().all())
            await session.commit()

        param_overrides: dict[str, dict] = {}
        validation_flags: dict[str, bool] = {}
        rr_floor_overrides: dict[str, float] = {}

        for r in rules:
            if r.rule_type == "VALIDATION_TOGGLE":
                validation_flags[r.param_name] = r.param_value >= 1.0
            elif r.param_name == "rr_floor":
                # strategy_type 컬럼을 rr_floor 적용 국면(BULL/THEME/...) 저장용으로 재사용
                rr_floor_overrides[r.strategy_type] = r.param_value
            else:
                scope = r.strategy_type or "ALL"
                param_overrides.setdefault(scope, {})[r.param_name] = r.param_value

        return {
            "param_overrides": param_overrides,
            "validation_flags": validation_flags,
            "rr_floor_overrides": rr_floor_overrides,
            "rules": rules,
        }

    # ──────────────────────────────────────────
    # 전략/리스크 매니저에 적용
    # ──────────────────────────────────────────
    def apply_to_strategies(self, strategies: dict, active_rules: dict) -> None:
        """전략 인스턴스에 파라미터 오버라이드 적용"""
        param_overrides = active_rules.get("param_overrides", {})
        all_overrides = param_overrides.get("ALL", {})

        for strategy_key, strategy_instance in strategies.items():
            specific = param_overrides.get(strategy_key, {})
            merged = {**all_overrides, **specific}  # 전략별이 우선

            for param_name, param_value in merged.items():
                if hasattr(strategy_instance, param_name):
                    old_val = getattr(strategy_instance, param_name)
                    if old_val != param_value:
                        setattr(strategy_instance, param_name, param_value)
                        logger.info(
                            "[TradingRule] {} {}: {} → {}",
                            strategy_key, param_name, old_val, param_value,
                        )

    def apply_to_risk_manager(self, risk_mgr, active_rules: dict) -> None:
        """RiskManager.RR_FLOOR 오버라이드"""
        rr_overrides = active_rules.get("rr_floor_overrides", {})
        for regime, floor in rr_overrides.items():
            old = risk_mgr.RR_FLOOR.get(regime)
            risk_mgr.RR_FLOOR[regime] = floor
            logger.info(
                "[TradingRule] RR_FLOOR[{}]: {} → {}",
                regime, old, floor,
            )

    # ──────────────────────────────────────────
    # 적용 기록 + 만료 정리
    # ──────────────────────────────────────────
    async def record_application(self, rule_ids: list[str], market_scope: str | None = None) -> None:
        """적용 횟수 증가 (감사 추적)"""
        if not rule_ids:
            return
        scope = normalize_market_scope(market_scope) if market_scope else None
        rule_model = self._rule_model(scope or "KRX")
        async with AsyncSessionLocal() as session:
            for rid in rule_ids:
                stmt = update(rule_model).where(rule_model.id == rid)
                if scope:
                    stmt = stmt.where(*self._scope_filters(rule_model, scope))
                await session.execute(
                    stmt.values(applied_count=rule_model.applied_count + 1)
                )
            await session.commit()

    async def expire_old_rules(self, market_scope: str | None = None) -> int:
        """만료된 규칙 비활성화"""
        now = now_kst()
        scope = normalize_market_scope(market_scope) if market_scope else None
        rule_model = self._rule_model(scope or "KRX")
        async with AsyncSessionLocal() as session:
            stmt = update(rule_model).where(
                rule_model.is_active.is_(True),
                rule_model.expires_at <= now,
            )
            if scope:
                stmt = stmt.where(*self._scope_filters(rule_model, scope))
            result = await session.execute(stmt.values(is_active=False))
            await session.commit()
            return result.rowcount  # type: ignore[return-value]

    # ──────────────────────────────────────────
    # 부트스트랩 규칙
    # ──────────────────────────────────────────
    async def _ensure_bootstrap_rules(self, session: AsyncSession, market_scope: str, rule_model) -> None:
        """상시 활성화 부트스트랩 규칙 확인 — 없으면 생성"""
        now = now_kst()
        for tmpl in BOOTSTRAP_RULES:
            existing = await session.execute(
                select(rule_model).where(
                    *self._scope_filters(rule_model, market_scope),
                    rule_model.param_name == tmpl["param_name"],
                    rule_model.rule_type == tmpl["rule_type"],
                    rule_model.is_active.is_(True),
                )
            )
            if existing.scalars().first():
                continue

            rule = self._build_rule(
                rule_model,
                market_scope,
                rule_type=tmpl["rule_type"],
                param_name=tmpl["param_name"],
                param_value=tmpl["param_value"],
                source="BOOTSTRAP",
                reason=tmpl["reason"],
                priority="HIGH",
                is_active=True,
                expires_at=now + timedelta(days=tmpl["expires_days"]),
            )
            session.add(rule)
            logger.info("[TradingRule] 부트스트랩 규칙 생성: {}", tmpl["param_name"])

        await session.flush()


# 싱글톤 인스턴스
trading_rule_engine = TradingRuleEngine()
