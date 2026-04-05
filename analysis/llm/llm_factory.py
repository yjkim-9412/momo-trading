"""LLM Factory — Claude/Codex CLI 공통 라우터."""
from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from loguru import logger

from analysis.llm.base import LLMExecutionPlan, LLMProviderProtocol, LLMRequest, LLMSessionProtocol
from analysis.llm.claude_code_provider import ClaudeCodeProvider
from analysis.llm.codex_cli_provider import CodexCLIProvider
from core.config import settings
from trading.enums import ActivityPhase, ActivityType, LLMProvider, LLMTier, Tier1Profile
from trading.market_profile import normalize_market_scope


class ProviderRegistry:
    """Provider 인스턴스/클래스 조회."""

    def __init__(self, provider_classes: dict[LLMProvider, type[LLMProviderProtocol]]):
        self._provider_classes = provider_classes
        self._instances: dict[tuple[LLMProvider, LLMTier], LLMProviderProtocol] = {}

    def get(self, provider: LLMProvider, tier: LLMTier) -> LLMProviderProtocol:
        key = (provider, tier)
        if key not in self._instances:
            self._instances[key] = self._provider_classes[provider](tier)
        return self._instances[key]

    def get_class(self, provider: LLMProvider) -> type[LLMSessionProtocol]:
        return cast(type[LLMSessionProtocol], self._provider_classes[provider])

    def clear(self) -> None:
        self._instances = {}


class LLMRoutingPolicy:
    """Provider fallback 정책."""

    FALLBACK_MATRIX: dict[LLMProvider, tuple[LLMProvider, ...]] = {
        LLMProvider.CODEX_CLI: (LLMProvider.CLAUDE_CODE,),
    }

    @classmethod
    def fallback_providers(
        cls,
        provider: LLMProvider,
        *,
        scope: str | None,
        error: Exception | None,
    ) -> tuple[LLMProvider, ...]:
        if not scope or error is None:
            return ()
        if provider == LLMProvider.CODEX_CLI and cls._is_codex_failure(error):
            return cls.FALLBACK_MATRIX.get(provider, ())
        return ()

    @staticmethod
    def _is_codex_failure(error: Exception | None) -> bool:
        """Codex CLI fallback 대상 오류 여부."""
        if error is None:
            return False
        message = str(error)
        patterns = (
            "Auth(",
            "TokenRefreshFailed",
            "Failed to parse server response",
            "Codex CLI 빈 응답",
            "Codex 세션 ID를 찾을 수 없습니다",
            "Transport channel closed",
            "rate limit",
            "Rate limit",
            "quota",
            "insufficient",
            "billing",
            "exceeded",
            "429",
            "too many requests",
            "Codex CLI 실패",
        )
        return any(pattern in message for pattern in patterns)


class LLMFactory:
    """CLI 기반 LLM 오케스트레이터."""

    PROVIDER_CLASSES: dict[LLMProvider, type[LLMProviderProtocol]] = {
        LLMProvider.CLAUDE_CODE: ClaudeCodeProvider,
        LLMProvider.CODEX_CLI: CodexCLIProvider,
    }
    PROVIDER_LABELS = {
        LLMProvider.CLAUDE_CODE: "Claude Code (로컬)",
        LLMProvider.CODEX_CLI: "Codex CLI (로컬)",
    }
    TIER_METADATA = {
        LLMTier.TIER1: {
            "display_name": "후보 분석 에이전트",
            "short_label": "후보 분석",
            "description": "차트·시장 컨텍스트를 바탕으로 매수 후보와 목표/손절을 1차 판단",
        },
        LLMTier.TIER2: {
            "display_name": "최종 검토 에이전트",
            "short_label": "최종 검토",
            "description": "1차 분석 결과를 리스크·포트폴리오 관점에서 재검증해 주문 승인 여부를 결정",
        },
    }
    TIER1_PROFILE_METADATA = {
        Tier1Profile.SCAN: {
            "display_name": "시장 스캔 프로필",
            "short_label": "스캔",
            "description": "시장 스캔·스크리닝·뉴스 요약에 사용하는 저비용 추론 프로필",
        },
        Tier1Profile.ANALYSIS: {
            "display_name": "종목 판단 프로필",
            "short_label": "판단",
            "description": "종목 1차 분석·AI 한도 결정에 사용하는 기본 추론 프로필",
        },
    }

    def __init__(self):
        self._provider_registry = ProviderRegistry(self.PROVIDER_CLASSES)
        self._routing_policy = LLMRoutingPolicy()
        self._usage_by_scope_tier: dict[str, dict[str, int]] = {}
        self._usage_by_scope_tier_phase: dict[str, dict[str, int | str]] = {}
        self._cycle_usage_markers: dict[tuple[str, str], dict[str, int]] = {}
        self._last_cycle_delta: dict[str, Any] | None = None

    @classmethod
    def get_tier_metadata(cls, tier: LLMTier) -> dict[str, str]:
        return dict(cls.TIER_METADATA[tier])

    @staticmethod
    def _usage_counter(snapshot: dict[str, Any] | None) -> dict[str, int]:
        data = snapshot or {}
        return {
            "calls": int(data.get("total_calls") or 0),
            "input_tokens": int(data.get("total_input_tokens") or 0),
            "output_tokens": int(data.get("total_output_tokens") or 0),
            "cached_input_tokens": int(data.get("total_cached_input_tokens") or 0),
        }

    @staticmethod
    def _usage_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
        return {
            key: max(0, int(after.get(key, 0)) - int(before.get(key, 0)))
            for key in ("calls", "input_tokens", "output_tokens", "cached_input_tokens")
        }

    @staticmethod
    def _scope_tier_usage_key(scope: str | None, tier: LLMTier) -> str:
        return f"{normalize_market_scope(scope or 'GLOBAL')}:{tier.value}"

    @staticmethod
    def _scope_tier_phase_usage_key(scope: str | None, tier: LLMTier, phase: str) -> str:
        return f"{normalize_market_scope(scope or 'GLOBAL')}:{tier.value}:{phase}"

    def _record_scope_tier_usage(
        self,
        *,
        scope: str | None,
        tier: LLMTier,
        delta: dict[str, int],
    ) -> None:
        if not any(delta.values()):
            return
        key = self._scope_tier_usage_key(scope, tier)
        bucket = self._usage_by_scope_tier.setdefault(
            key,
            {
                "scope": normalize_market_scope(scope or "GLOBAL"),
                "tier": tier.value,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
            },
        )
        for field_name in ("calls", "input_tokens", "output_tokens", "cached_input_tokens"):
            bucket[field_name] += int(delta.get(field_name, 0))

    def _record_scope_tier_phase_usage(
        self,
        *,
        scope: str | None,
        tier: LLMTier,
        phase: str,
        delta: dict[str, int],
    ) -> None:
        if not any(delta.values()):
            return
        key = self._scope_tier_phase_usage_key(scope, tier, phase)
        bucket = self._usage_by_scope_tier_phase.setdefault(
            key,
            {
                "scope": normalize_market_scope(scope or "GLOBAL"),
                "tier": tier.value,
                "phase": phase,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
            },
        )
        for field_name in ("calls", "input_tokens", "output_tokens", "cached_input_tokens"):
            bucket[field_name] += int(delta.get(field_name, 0))

    def _build_request(
        self,
        *,
        prompt: str,
        tier: LLMTier,
        system_prompt: str = "",
        scope: str | None = None,
        phase: str = "cycle",
        requested_profile: Tier1Profile | None = None,
        symbol: str | None = None,
        cycle_id: str | None = None,
        reasoning_effort_override: str | None = None,
    ) -> LLMRequest:
        return LLMRequest(
            tier=tier,
            prompt=prompt,
            system_prompt=system_prompt,
            scope=scope,
            phase=phase,
            requested_profile=requested_profile,
            symbol=symbol,
            cycle_id=cycle_id,
            reasoning_effort_override=reasoning_effort_override,
        )

    def _get_provider(self, provider: LLMProvider, tier: LLMTier) -> LLMProviderProtocol:
        return self._provider_registry.get(provider, tier)

    def _build_execution_plan(
        self,
        request: LLMRequest,
        *,
        provider_override: LLMProvider | None = None,
    ) -> tuple[LLMProviderProtocol, LLMExecutionPlan]:
        provider_enum = provider_override or settings.llm_provider_for_scope(request.scope)
        provider = self._get_provider(provider_enum, request.tier)
        plan = provider.plan_request(request)
        return provider, plan

    def _get_provider_usage_counter(self, provider: LLMProvider) -> dict[str, int]:
        provider_class = self._provider_registry.get_class(provider)
        return self._usage_counter(provider_class.get_usage_snapshot())

    async def _generate_with_plan(
        self,
        provider: LLMProviderProtocol,
        request: LLMRequest,
        plan: LLMExecutionPlan,
    ) -> tuple[str, str]:
        if not await provider.is_available():
            raise RuntimeError(f"{provider.provider.value} CLI를 찾을 수 없습니다 (PATH 확인)")

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                usage_before = self._get_provider_usage_counter(plan.provider)
                start = time.time()
                result = await provider.generate_from_plan(request, plan)
                elapsed_ms = int((time.time() - start) * 1000)
                usage_after = self._get_provider_usage_counter(plan.provider)
                usage_delta = self._usage_delta(usage_before, usage_after)
                self._record_scope_tier_usage(
                    scope=request.scope,
                    tier=request.tier,
                    delta=usage_delta,
                )
                self._record_scope_tier_phase_usage(
                    scope=request.scope,
                    tier=request.tier,
                    phase=request.phase,
                    delta=usage_delta,
                )

                logger.debug(
                    "LLM 생성 완료: {} / {} ({}ms)",
                    plan.provider.value,
                    provider.model_id,
                    elapsed_ms,
                )

                await self._log_llm_conversation(
                    request=request,
                    plan=plan,
                    model=provider.model_id,
                    response=result,
                    elapsed_ms=elapsed_ms,
                    usage_delta=usage_delta,
                )
                return result, plan.provider.value
            except Exception as error:
                last_error = error
                if attempt == 0:
                    logger.warning("LLM 호출 실패, 재시도: {}", str(error)[:100])
                    await asyncio.sleep(2)

        raise last_error or RuntimeError("LLM 호출 실패")

    async def _log_fallback_attempt(
        self,
        *,
        request: LLMRequest,
        source_plan: LLMExecutionPlan,
        fallback_plan: LLMExecutionPlan,
        error: Exception,
    ) -> None:
        try:
            from services.activity_logger import activity_logger

            await activity_logger.log(
                ActivityType.LLM_CALL,
                ActivityPhase.PROGRESS,
                f"[{request.tier.value}] {source_plan.provider.value} 실패 → {fallback_plan.provider.value} fallback",
                llm_provider=fallback_plan.provider.value,
                llm_tier=request.tier.value,
                symbol=request.symbol,
                cycle_id=request.cycle_id,
                detail={
                    "failed_provider": source_plan.provider.value,
                    "fallback_provider": fallback_plan.provider.value,
                    "scope": source_plan.scope,
                    "phase": request.phase,
                    "reason": str(error)[:300],
                    "requested_profile": (
                        request.requested_profile.value
                        if request.requested_profile
                        else None
                    ),
                    "effective_profile": (
                        fallback_plan.effective_profile.value
                        if fallback_plan.effective_profile
                        else None
                    ),
                },
            )
        except Exception as activity_error:
            logger.debug("LLM fallback activity 로깅 실패 (무시): {}", str(activity_error))

    async def _execute_request(
        self,
        request: LLMRequest,
        *,
        provider_override: LLMProvider | None = None,
        allow_fallback: bool = True,
    ) -> tuple[str, str, LLMExecutionPlan]:
        provider, plan = self._build_execution_plan(request, provider_override=provider_override)
        try:
            result, provider_name = await self._generate_with_plan(provider, request, plan)
            return result, provider_name, plan
        except Exception as error:
            fallback_providers = (
                self._routing_policy.fallback_providers(
                    plan.provider,
                    scope=request.scope,
                    error=error,
                )
                if allow_fallback
                else ()
            )
            for fallback_provider in fallback_providers:
                fallback_instance, fallback_plan = self._build_execution_plan(
                    request,
                    provider_override=fallback_provider,
                )
                if not await fallback_instance.is_available():
                    logger.warning(
                        "[{}] {} 실패 후 {} fallback 불가: CLI 미설치",
                        plan.scope,
                        plan.provider.value,
                        fallback_provider.value,
                    )
                    continue
                logger.warning(
                    "[{}] {} 실패 → {} fallback 시도: {}",
                    plan.scope,
                    plan.provider.value,
                    fallback_provider.value,
                    str(error)[:160],
                )
                await self._log_fallback_attempt(
                    request=request,
                    source_plan=plan,
                    fallback_plan=fallback_plan,
                    error=error,
                )
                result, provider_name = await self._generate_with_plan(
                    fallback_instance,
                    request,
                    fallback_plan,
                )
                return result, provider_name, fallback_plan
            raise

    async def generate(
        self,
        prompt: str,
        tier: LLMTier = LLMTier.TIER1,
        system_prompt: str = "",
        *,
        scope: str | None = None,
        phase: str = "cycle",
        symbol: str | None = None,
        cycle_id: str | None = None,
        reasoning_effort_override: str | None = None,
    ) -> tuple[str, str]:
        request = self._build_request(
            prompt=prompt,
            tier=tier,
            system_prompt=system_prompt,
            scope=scope,
            phase=phase,
            symbol=symbol,
            cycle_id=cycle_id,
            reasoning_effort_override=reasoning_effort_override,
        )
        result, provider_name, _plan = await self._execute_request(request)
        return result, provider_name

    async def generate_tier1(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        profile: Tier1Profile = Tier1Profile.ANALYSIS,
        scope: str | None = None,
        phase: str = "cycle",
        symbol: str | None = None,
        cycle_id: str | None = None,
    ) -> tuple[str, str]:
        request = self._build_request(
            prompt=prompt,
            tier=LLMTier.TIER1,
            system_prompt=system_prompt,
            scope=scope,
            phase=phase,
            requested_profile=profile,
            symbol=symbol,
            cycle_id=cycle_id,
        )
        result, provider_name, _plan = await self._execute_request(request)
        return result, provider_name

    async def generate_tier2(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        scope: str | None = None,
        phase: str = "cycle",
        symbol: str | None = None,
        cycle_id: str | None = None,
    ) -> tuple[str, str]:
        request = self._build_request(
            prompt=prompt,
            tier=LLMTier.TIER2,
            system_prompt=system_prompt,
            scope=scope,
            phase=phase,
            symbol=symbol,
            cycle_id=cycle_id,
        )
        result, provider_name, _plan = await self._execute_request(request)
        return result, provider_name

    async def _log_llm_conversation(
        self,
        *,
        request: LLMRequest,
        plan: LLMExecutionPlan,
        model: str,
        response: str,
        elapsed_ms: int,
        usage_delta: dict[str, int] | None = None,
    ) -> None:
        try:
            from services.activity_logger import activity_logger

            await activity_logger.log(
                ActivityType.LLM_CALL,
                ActivityPhase.COMPLETE,
                f"[{request.tier.value}] {plan.provider.value} ({model}) — {elapsed_ms/1000:.1f}초",
                detail={
                    "llm_system_prompt": request.system_prompt[:2000] if request.system_prompt else "",
                    "llm_prompt": request.prompt[:5000],
                    "llm_response": response[:5000],
                    "llm_model": model,
                    "llm_scope": plan.scope,
                    "llm_phase": request.phase,
                    "llm_requested_profile": (
                        request.requested_profile.value
                        if request.requested_profile
                        else None
                    ),
                    "llm_effective_profile": (
                        plan.effective_profile.value
                        if plan.effective_profile
                        else None
                    ),
                    "llm_session_mode": plan.session_mode,
                    "llm_capabilities": plan.capabilities.as_dict(),
                    "llm_usage_delta": dict(usage_delta or {}),
                },
                llm_provider=plan.provider.value,
                llm_tier=request.tier.value,
                execution_time_ms=elapsed_ms,
                symbol=request.symbol,
                cycle_id=request.cycle_id,
            )
        except Exception as error:
            logger.debug("LLM 대화 로깅 실패 (무시): {}", str(error))

    def _get_session_provider_class(self, scope: str | None = None) -> type[LLMSessionProtocol]:
        provider_enum = settings.llm_provider_for_scope(scope)
        return self._provider_registry.get_class(provider_enum)

    def start_session(self, scope: str = "KRX", phase: str = "cycle") -> str | None:
        session_provider_class = self._get_session_provider_class(scope)
        self._cycle_usage_markers[(normalize_market_scope(scope), phase)] = self._usage_counter(
            session_provider_class.get_usage_snapshot()
        )
        return session_provider_class.start_session(scope, phase)

    def end_session(self, scope: str = "KRX", phase: str = "cycle") -> str | None:
        session_provider_class = self._get_session_provider_class(scope)
        scope_key = normalize_market_scope(scope)
        marker = self._cycle_usage_markers.pop((scope_key, phase), None)
        if marker is not None:
            delta = self._usage_delta(
                marker,
                self._usage_counter(session_provider_class.get_usage_snapshot()),
            )
            self._last_cycle_delta = {
                "scope": scope_key,
                "phase": phase,
                "provider": settings.llm_provider_for_scope(scope).value,
                **delta,
            }
        return session_provider_class.end_session(scope, phase)

    def pause_session(self, scope: str = "KRX", phase: str = "cycle") -> str | None:
        return self._get_session_provider_class(scope).pause_session(scope, phase)

    def resume_session(self, session_id: str, scope: str = "KRX", phase: str = "cycle") -> None:
        self._get_session_provider_class(scope).resume_session(session_id, scope, phase)

    def get_session_id(self, scope: str | None = None, phase: str = "cycle") -> str | None:
        provider_enum = settings.llm_provider_for_scope(scope)
        return self._provider_registry.get_class(provider_enum).get_session_handle(
            scope or "KRX",
            phase,
        ).external_id

    def _status_scope(self) -> str | None:
        if settings.has_stock_markets:
            return settings.primary_market_code
        if settings.has_crypto_markets:
            return "CRYPTO"
        return None

    def _build_status_plan(
        self,
        provider: LLMProvider,
        *,
        tier: LLMTier,
        requested_profile: Tier1Profile | None = None,
        scope: str | None = None,
        phase: str = "cycle",
    ) -> LLMExecutionPlan:
        request = self._build_request(
            prompt="",
            tier=tier,
            scope=scope,
            phase=phase,
            requested_profile=requested_profile,
        )
        provider_instance = self._get_provider(provider, tier)
        return provider_instance.plan_request(request)

    @staticmethod
    def _serialize_tier_status(
        plan: LLMExecutionPlan,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        payload = {
            "provider": plan.provider.value,
            "model": plan.model,
            "reasoning_effort": plan.reasoning_effort,
            "requested_profile": (
                plan.requested_profile.value
                if plan.requested_profile
                else None
            ),
            "effective_profile": (
                plan.effective_profile.value
                if plan.effective_profile
                else None
            ),
            "effective_model": plan.model,
            "effective_reasoning_effort": plan.reasoning_effort,
            "session_mode": plan.session_mode,
            "capabilities": plan.capabilities.as_dict(),
        }
        payload.update(metadata)
        return payload

    def _serialize_tier1_profiles(
        self,
        provider: LLMProvider,
        *,
        scope: str | None,
    ) -> dict[str, Any]:
        profiles: dict[str, Any] = {}
        for profile in Tier1Profile:
            plan = self._build_status_plan(
                provider,
                tier=LLMTier.TIER1,
                requested_profile=profile,
                scope=scope,
                phase="cycle",
            )
            payload = {
                "provider": provider.value,
                "model": plan.model,
                "reasoning_effort": plan.reasoning_effort,
                "requested_profile": profile.value,
                "effective_profile": (
                    plan.effective_profile.value
                    if plan.effective_profile
                    else None
                ),
                "effective_model": plan.model,
                "effective_reasoning_effort": plan.reasoning_effort,
                "session_mode": plan.session_mode,
                "capabilities": plan.capabilities.as_dict(),
            }
            payload.update(self.TIER1_PROFILE_METADATA[profile])
            profiles[profile.value] = payload
        return profiles

    def get_llm_usage(self) -> dict[str, Any]:
        scope = self._status_scope()
        provider = settings.llm_provider_for_scope(scope)
        report = self._provider_registry.get_class(provider).get_usage_report()
        report.setdefault("provider", provider.value)
        report.setdefault("selected_provider", provider.value)
        report.setdefault("provider_name", self.PROVIDER_LABELS[provider])
        report.setdefault("summary", {})
        summary = report["summary"]
        report.setdefault("total_sessions", summary.get("total_sessions"))
        report.setdefault("total_messages", summary.get("total_messages"))
        report.setdefault("model_usage", {})
        report.setdefault("daily_activity", [])
        report.setdefault("daily_model_tokens", [])
        report.setdefault(
            "by_scope_tier",
            {key: dict(value) for key, value in self._usage_by_scope_tier.items()},
        )
        report.setdefault(
            "by_scope_tier_phase",
            {key: dict(value) for key, value in self._usage_by_scope_tier_phase.items()},
        )
        report.setdefault(
            "last_cycle_delta",
            dict(self._last_cycle_delta) if self._last_cycle_delta else None,
        )
        if scope:
            report.setdefault(
                "default_cycle_plan",
                self._build_status_plan(
                    provider,
                    tier=LLMTier.TIER1,
                    requested_profile=Tier1Profile.ANALYSIS,
                    scope=scope,
                    phase="cycle",
                ).as_dict(),
            )
        return report

    async def get_llm_status(self) -> dict[str, Any]:
        status_scope = self._status_scope()
        selected_provider = settings.llm_provider_for_scope(status_scope)
        tier1_plan = self._build_status_plan(
            selected_provider,
            tier=LLMTier.TIER1,
            requested_profile=Tier1Profile.ANALYSIS,
            scope=status_scope,
            phase="cycle",
        )
        tier2_plan = self._build_status_plan(
            selected_provider,
            tier=LLMTier.TIER2,
            scope=status_scope,
            phase="cycle",
        )
        available_providers = []

        for provider_enum in self.PROVIDER_CLASSES:
            provider_tier1 = self._get_provider(provider_enum, LLMTier.TIER1)
            provider_tier2 = self._get_provider(provider_enum, LLMTier.TIER2)
            default_cycle_plan = self._build_status_plan(
                provider_enum,
                tier=LLMTier.TIER1,
                requested_profile=Tier1Profile.ANALYSIS,
                scope=status_scope,
                phase="cycle",
            )
            available_providers.append(
                {
                    "id": provider_enum.value,
                    "name": provider_tier1.display_name,
                    "selected": provider_enum == selected_provider,
                    "available": await provider_tier1.is_available(),
                    "models": {
                        "tier1": settings.get_llm_model(provider_enum, LLMTier.TIER1),
                        "tier2": settings.get_llm_model(provider_enum, LLMTier.TIER2),
                    },
                    "reasoning_efforts": {
                        "tier1": settings.get_llm_reasoning_effort(provider_enum, LLMTier.TIER1),
                        "tier1_profiles": {
                            profile.value: settings.get_llm_reasoning_effort(
                                provider_enum,
                                LLMTier.TIER1,
                                profile,
                            )
                            for profile in Tier1Profile
                        },
                        "tier2": settings.get_llm_reasoning_effort(provider_enum, LLMTier.TIER2),
                    },
                    "capabilities": provider_tier1.capabilities.as_dict(),
                    "default_cycle_plan": default_cycle_plan.as_dict(),
                    "has_key": True,
                }
            )

        return {
            "current_provider": selected_provider.value,
            "selected_provider": selected_provider.value,
            "provider": selected_provider.value,
            "provider_name": self.PROVIDER_LABELS[selected_provider],
            "session_id": tier1_plan.session_handle.external_id,
            "status_scope": tier1_plan.scope,
            "tier1": self._serialize_tier_status(tier1_plan, self.get_tier_metadata(LLMTier.TIER1)),
            "tier1_profiles": self._serialize_tier1_profiles(selected_provider, scope=status_scope),
            "tier2": self._serialize_tier_status(tier2_plan, self.get_tier_metadata(LLMTier.TIER2)),
            "available_providers": available_providers,
        }


llm_factory = LLMFactory()
