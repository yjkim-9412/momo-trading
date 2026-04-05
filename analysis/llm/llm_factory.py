"""LLM Factory — Claude/Codex CLI 공통 라우터"""
from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from loguru import logger

from analysis.llm.base import LLMProviderProtocol, LLMSessionProtocol
from analysis.llm.claude_code_provider import ClaudeCodeProvider
from analysis.llm.codex_cli_provider import CodexCLIProvider
from core.config import settings
from trading.enums import ActivityPhase, ActivityType, LLMProvider, LLMTier, Tier1Profile
from trading.market_profile import normalize_market_scope


class LLMFactory:
    """CLI 기반 LLM 라우터"""

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
        self._selected_provider: LLMProvider | None = None
        self._providers: dict[LLMTier, LLMProviderProtocol] = {}
        # 코인 전용 provider 캐시 (주식과 독립)
        self._crypto_provider: LLMProvider | None = None
        self._crypto_providers: dict[LLMTier, LLMProviderProtocol] = {}
        self._usage_by_scope_tier: dict[str, dict[str, int]] = {}
        self._cycle_usage_markers: dict[tuple[str, str], dict[str, int]] = {}
        self._last_cycle_delta: dict[str, Any] | None = None

    def _instantiate_provider(
        self,
        provider: LLMProvider,
        tier: LLMTier,
        *,
        scope: str | None = None,
        profile: Tier1Profile | None = None,
        phase: str = "cycle",
    ) -> LLMProviderProtocol:
        """scope/profile 기준으로 provider 인스턴스 생성"""
        provider_class = self.PROVIDER_CLASSES[provider]
        return provider_class(
            tier,
            model=settings.get_llm_model_for_scope_provider(scope, provider, tier, profile),
            reasoning_effort=settings.get_llm_reasoning_effort_for_scope_provider(
                scope,
                provider,
                tier,
                profile,
                phase,
            ),
        )

    def _ensure_provider_cache(self) -> None:
        """settings 기준으로 provider 인스턴스 갱신"""
        selected_provider = settings.llm_provider
        if self._selected_provider == selected_provider and self._providers:
            return

        self._providers = {
            LLMTier.TIER1: self._instantiate_provider(
                selected_provider,
                LLMTier.TIER1,
                profile=Tier1Profile.ANALYSIS,
            ),
            LLMTier.TIER2: self._instantiate_provider(selected_provider, LLMTier.TIER2),
        }
        self._selected_provider = selected_provider

    def _ensure_crypto_provider_cache(self) -> None:
        """코인 전용 provider 인스턴스 갱신"""
        crypto_provider = settings.crypto_llm_provider
        if self._crypto_provider == crypto_provider and self._crypto_providers:
            return

        self._crypto_providers = {
            LLMTier.TIER1: self._instantiate_provider(
                crypto_provider,
                LLMTier.TIER1,
                scope="CRYPTO",
                profile=Tier1Profile.ANALYSIS,
            ),
            LLMTier.TIER2: self._instantiate_provider(
                crypto_provider,
                LLMTier.TIER2,
                scope="CRYPTO",
            ),
        }
        self._crypto_provider = crypto_provider

    def _get_provider(self, tier: LLMTier, scope: str | None = None) -> LLMProviderProtocol:
        """Tier별 provider 인스턴스 반환 (CRYPTO scope면 코인 전용 provider)"""
        if scope and normalize_market_scope(scope) == "CRYPTO":
            self._ensure_crypto_provider_cache()
            return self._crypto_providers[tier]
        self._ensure_provider_cache()
        return self._providers[tier]

    def _get_profiled_tier1_provider(
        self,
        profile: Tier1Profile,
        scope: str | None = None,
        phase: str = "cycle",
    ) -> LLMProviderProtocol:
        """Tier1 profile/scope 기준 provider 인스턴스 반환"""
        provider_enum = settings.llm_provider_for_scope(scope)
        provider = self._get_provider(LLMTier.TIER1, scope=scope)
        expected_model = settings.get_llm_model_for_scope(scope, LLMTier.TIER1, profile)
        expected_effort = settings.get_llm_reasoning_effort_for_scope(
            scope,
            LLMTier.TIER1,
            profile,
            phase,
        )
        if (
            provider.provider == provider_enum
            and provider.configured_model == expected_model
            and provider.configured_reasoning_effort == expected_effort
        ):
            return provider
        return self._instantiate_provider(
            provider_enum,
            LLMTier.TIER1,
            scope=scope,
            profile=profile,
            phase=phase,
        )

    def _get_session_provider_class(self, scope: str | None = None) -> type[LLMSessionProtocol]:
        """현재 선택된 provider class 반환"""
        provider = self._get_provider(LLMTier.TIER1, scope=scope)
        return cast(type[LLMSessionProtocol], type(provider))

    @classmethod
    def get_tier_metadata(cls, tier: LLMTier) -> dict[str, str]:
        """Tier별 사용자 표시 메타데이터 반환"""
        return dict(cls.TIER_METADATA[tier])

    @staticmethod
    def _usage_counter(snapshot: dict[str, Any] | None) -> dict[str, int]:
        """usage snapshot에서 비교 가능한 카운터만 추출"""
        data = snapshot or {}
        return {
            "calls": int(data.get("total_calls") or 0),
            "input_tokens": int(data.get("total_input_tokens") or 0),
            "output_tokens": int(data.get("total_output_tokens") or 0),
            "cached_input_tokens": int(data.get("total_cached_input_tokens") or 0),
        }

    @staticmethod
    def _usage_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
        """usage before/after delta 계산"""
        return {
            key: max(0, int(after.get(key, 0)) - int(before.get(key, 0)))
            for key in ("calls", "input_tokens", "output_tokens", "cached_input_tokens")
        }

    @staticmethod
    def _scope_tier_usage_key(scope: str | None, tier: LLMTier) -> str:
        """scope/tier별 usage bucket key."""
        return f"{normalize_market_scope(scope or 'GLOBAL')}:{tier.value}"

    def _get_provider_usage_counter(self, provider: LLMProviderProtocol) -> dict[str, int]:
        """provider cumulative usage snapshot 반환"""
        provider_class = cast(type[LLMSessionProtocol], type(provider))
        return self._usage_counter(provider_class.get_usage_snapshot())

    def _record_scope_tier_usage(
        self,
        *,
        scope: str | None,
        tier: LLMTier,
        delta: dict[str, int],
    ) -> None:
        """scope/tier별 usage 누적"""
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

    @staticmethod
    def _serialize_tier_status(provider: LLMProviderProtocol) -> dict[str, Any]:
        """Tier별 provider 상태 직렬화"""
        tier_status = {
            "provider": provider.provider.value,
            "model": provider.configured_model,
            "reasoning_effort": provider.configured_reasoning_effort,
        }
        tier_status.update(LLMFactory.get_tier_metadata(provider.tier))
        return tier_status

    @classmethod
    def _serialize_tier1_profiles(cls, provider: LLMProvider) -> dict[str, Any]:
        """Tier1 프로필 상태 직렬화"""
        model = settings.get_llm_model(provider, LLMTier.TIER1)
        profiles: dict[str, Any] = {}
        for profile in Tier1Profile:
            payload = {
                "provider": provider.value,
                "model": model,
                "reasoning_effort": settings.get_llm_reasoning_effort(
                    provider,
                    LLMTier.TIER1,
                    profile,
                ),
            }
            payload.update(cls.TIER1_PROFILE_METADATA[profile])
            profiles[profile.value] = payload
        return profiles

    async def _generate_with_provider(
        self,
        provider: LLMProviderProtocol,
        prompt: str,
        *,
        tier: LLMTier,
        system_prompt: str = "",
        scope: str | None = None,
        phase: str = "cycle",
        symbol: str | None = None,
        cycle_id: str | None = None,
        reasoning_effort_override: str | None = None,
        profile: Tier1Profile | None = None,
    ) -> tuple[str, str]:
        """지정 provider로 텍스트 생성"""
        if not await provider.is_available():
            raise RuntimeError(f"{provider.provider.value} CLI를 찾을 수 없습니다 (PATH 확인)")

        last_error = None
        for attempt in range(2):
            try:
                usage_before = self._get_provider_usage_counter(provider)
                start = time.time()
                result = await provider.generate(
                    prompt,
                    system_prompt,
                    scope=scope,
                    phase=phase,
                    reasoning_effort_override=reasoning_effort_override,
                )
                elapsed_ms = int((time.time() - start) * 1000)
                provider_name = provider.provider.value
                model_id = provider.model_id
                usage_after = self._get_provider_usage_counter(provider)
                self._record_scope_tier_usage(
                    scope=scope,
                    tier=tier,
                    delta=self._usage_delta(usage_before, usage_after),
                )

                logger.debug(
                    "LLM 생성 완료: {} / {} ({}ms)",
                    provider_name, model_id, elapsed_ms,
                )

                await self._log_llm_conversation(
                    tier=tier,
                    provider=provider_name,
                    model=model_id,
                    system_prompt=system_prompt,
                    prompt=prompt,
                    response=result,
                    elapsed_ms=elapsed_ms,
                    symbol=symbol,
                    cycle_id=cycle_id,
                )

                return result, provider_name
            except Exception as e:
                last_error = e
                if attempt == 0:
                    logger.warning("LLM 호출 실패, 재시도: {}", str(e)[:100])
                    await asyncio.sleep(2)

        fallback_result = await self._maybe_generate_with_fallback(
            provider=provider,
            last_error=last_error,
            prompt=prompt,
            tier=tier,
            system_prompt=system_prompt,
            scope=scope,
            phase=phase,
            symbol=symbol,
            cycle_id=cycle_id,
            reasoning_effort_override=reasoning_effort_override,
            profile=profile,
        )
        if fallback_result is not None:
            return fallback_result

        raise last_error

    @staticmethod
    def _is_codex_failure(error: Exception | None) -> bool:
        """Codex CLI 실패 여부 (인증, 세션, 사용량 소진, 프로세스 실패 포함)"""
        if error is None:
            return False
        message = str(error)
        patterns = (
            # 인증/세션 오류
            "Auth(",
            "TokenRefreshFailed",
            "Failed to parse server response",
            "Codex CLI 빈 응답",
            "Codex 세션 ID를 찾을 수 없습니다",
            "Transport channel closed",
            # 사용량/제한 오류
            "rate limit",
            "Rate limit",
            "quota",
            "insufficient",
            "billing",
            "exceeded",
            "429",
            "too many requests",
            # 프로세스 실패
            "Codex CLI 실패",
        )
        return any(pattern in message for pattern in patterns)

    async def _maybe_generate_with_fallback(
        self,
        *,
        provider: LLMProviderProtocol,
        last_error: Exception | None,
        prompt: str,
        tier: LLMTier,
        system_prompt: str,
        scope: str | None,
        phase: str,
        symbol: str | None,
        cycle_id: str | None,
        reasoning_effort_override: str | None,
        profile: Tier1Profile | None,
    ) -> tuple[str, str] | None:
        """Codex CLI 실패 시 Claude Code fallback 1회 시도 (전 스코프)"""
        if (
            settings.llm_provider_for_scope(scope) != LLMProvider.CODEX_CLI
            or provider.provider != LLMProvider.CODEX_CLI
            or not scope
            or not self._is_codex_failure(last_error)
        ):
            return None

        fallback_provider = self._instantiate_provider(
            LLMProvider.CLAUDE_CODE,
            tier,
            scope=scope,
            profile=profile,
            phase=phase,
        )
        if not await fallback_provider.is_available():
            logger.warning(
                "[{}] {} 실패 후 Claude Code fallback 불가: CLI 미설치",
                scope,
                provider.provider.value,
            )
            return None

        logger.warning(
            "[{}] {} 실패 → {} fallback 시도: {}",
            scope,
            provider.provider.value,
            fallback_provider.provider.value,
            str(last_error)[:160],
        )
        try:
            from services.activity_logger import activity_logger

            await activity_logger.log(
                ActivityType.LLM_CALL,
                ActivityPhase.PROGRESS,
                f"[{tier.value}] {provider.provider.value} 실패 → {fallback_provider.provider.value} fallback",
                llm_provider=fallback_provider.provider.value,
                llm_tier=tier.value,
                symbol=symbol,
                cycle_id=cycle_id,
                detail={
                    "failed_provider": provider.provider.value,
                    "fallback_provider": fallback_provider.provider.value,
                    "reason": str(last_error)[:300],
                },
            )
        except Exception as activity_error:
            logger.debug("LLM fallback activity 로깅 실패 (무시): {}", str(activity_error))

        try:
            usage_before = self._get_provider_usage_counter(fallback_provider)
            start = time.time()
            result = await fallback_provider.generate(
                prompt,
                system_prompt,
                scope=scope,
                phase=phase,
                reasoning_effort_override=reasoning_effort_override,
            )
            elapsed_ms = int((time.time() - start) * 1000)
            provider_name = fallback_provider.provider.value
            model_id = fallback_provider.model_id
            usage_after = self._get_provider_usage_counter(fallback_provider)
            self._record_scope_tier_usage(
                scope=scope,
                tier=tier,
                delta=self._usage_delta(usage_before, usage_after),
            )
            await self._log_llm_conversation(
                tier=tier,
                provider=provider_name,
                model=model_id,
                system_prompt=system_prompt,
                prompt=prompt,
                response=result,
                elapsed_ms=elapsed_ms,
                symbol=symbol,
                cycle_id=cycle_id,
            )
            return result, provider_name
        except Exception as fallback_error:
            logger.error(
                "[{}] {} fallback 실패: {}",
                scope,
                fallback_provider.provider.value,
                str(fallback_error)[:200],
            )
            raise RuntimeError(
                f"{provider.provider.value} 실패: {last_error}; "
                f"{fallback_provider.provider.value} fallback 실패: {fallback_error}"
            ) from fallback_error

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
        """텍스트 생성 (최대 2회 시도)

        Returns:
            (생성 텍스트, 사용된 provider 이름)
        """
        provider = self._get_provider(tier, scope=scope)
        return await self._generate_with_provider(
            provider,
            prompt,
            tier=tier,
            system_prompt=system_prompt,
            scope=scope,
            phase=phase,
            symbol=symbol,
            cycle_id=cycle_id,
            reasoning_effort_override=reasoning_effort_override,
            profile=None,
        )

    async def _log_llm_conversation(
        self,
        *,
        tier: LLMTier,
        provider: str,
        model: str,
        system_prompt: str,
        prompt: str,
        response: str,
        elapsed_ms: int,
        symbol: str | None = None,
        cycle_id: str | None = None,
    ) -> None:
        """LLM 프롬프트/응답을 activity log에 기록"""
        try:
            from services.activity_logger import activity_logger
            await activity_logger.log(
                ActivityType.LLM_CALL, ActivityPhase.COMPLETE,
                f"[{tier.value}] {provider} ({model}) — {elapsed_ms/1000:.1f}초",
                detail={
                    "llm_system_prompt": system_prompt[:2000] if system_prompt else "",
                    "llm_prompt": prompt[:5000],
                    "llm_response": response[:5000],
                    "llm_model": model,
                },
                llm_provider=provider,
                llm_tier=tier.value,
                execution_time_ms=elapsed_ms,
                symbol=symbol,
                cycle_id=cycle_id,
            )
        except Exception as e:
            logger.debug("LLM 대화 로깅 실패 (무시): {}", str(e))

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
        """Tier 1 (빠른 분석용)"""
        provider = self._get_profiled_tier1_provider(profile, scope=scope, phase=phase)
        return await self._generate_with_provider(
            provider,
            prompt,
            tier=LLMTier.TIER1,
            system_prompt=system_prompt,
            scope=scope,
            phase=phase,
            symbol=symbol,
            cycle_id=cycle_id,
            profile=profile,
        )

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
        """Tier 2 (프리미엄 분석용)"""
        return await self.generate(
            prompt,
            LLMTier.TIER2,
            system_prompt,
            scope=scope,
            phase=phase,
            symbol=symbol,
            cycle_id=cycle_id,
        )

    def start_session(self, scope: str = "KRX", phase: str = "cycle") -> str | None:
        """선택된 provider 세션 시작"""
        session_provider_class = self._get_session_provider_class(scope)
        self._cycle_usage_markers[(normalize_market_scope(scope), phase)] = self._usage_counter(
            session_provider_class.get_usage_snapshot()
        )
        return session_provider_class.start_session(scope, phase)

    def end_session(self, scope: str = "KRX", phase: str = "cycle") -> str | None:
        """선택된 provider 세션 종료"""
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
        """선택된 provider 세션 일시 중지"""
        return self._get_session_provider_class(scope).pause_session(scope, phase)

    def resume_session(self, session_id: str, scope: str = "KRX", phase: str = "cycle") -> None:
        """선택된 provider 세션 재개"""
        self._get_session_provider_class(scope).resume_session(session_id, scope, phase)

    def get_session_id(self, scope: str | None = None, phase: str = "cycle") -> str | None:
        """선택된 provider 세션 ID 반환"""
        return self._get_session_provider_class(scope).get_session_id(scope, phase)

    def get_llm_usage(self) -> dict[str, Any]:
        """선택된 provider 사용량 반환"""
        report = self._get_session_provider_class().get_usage_report()
        provider = settings.llm_provider
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
            {
                key: dict(value)
                for key, value in self._usage_by_scope_tier.items()
            },
        )
        report.setdefault(
            "last_cycle_delta",
            dict(self._last_cycle_delta) if self._last_cycle_delta else None,
        )
        return report

    async def get_llm_status(self) -> dict[str, Any]:
        """현재 LLM 설정 상태 반환 (Admin API용)"""
        selected_provider = settings.llm_provider
        selected_tier1 = self._get_provider(LLMTier.TIER1)
        selected_tier2 = self._get_provider(LLMTier.TIER2)
        available_providers = []

        for provider_enum, provider_class in self.PROVIDER_CLASSES.items():
            provider_tier1 = provider_class(LLMTier.TIER1)
            provider_tier2 = provider_class(LLMTier.TIER2)
            available_providers.append({
                "id": provider_enum.value,
                "name": provider_tier1.display_name,
                "selected": provider_enum == selected_provider,
                "available": await provider_tier1.is_available(),
                "models": {
                    "tier1": settings.get_llm_model(provider_enum, LLMTier.TIER1),
                    "tier2": settings.get_llm_model(provider_enum, LLMTier.TIER2),
                },
                "reasoning_efforts": {
                    "tier1": provider_tier1.configured_reasoning_effort,
                    "tier1_profiles": {
                        profile.value: settings.get_llm_reasoning_effort(
                            provider_enum,
                            LLMTier.TIER1,
                            profile,
                        )
                        for profile in Tier1Profile
                    },
                    "tier2": provider_tier2.configured_reasoning_effort,
                },
                "has_key": True,
            })

        return {
            "current_provider": selected_provider.value,
            "selected_provider": selected_provider.value,
            "provider": selected_provider.value,
            "provider_name": self.PROVIDER_LABELS[selected_provider],
            "session_id": self.get_session_id(),
            "tier1": self._serialize_tier_status(selected_tier1),
            "tier1_profiles": self._serialize_tier1_profiles(selected_provider),
            "tier2": self._serialize_tier_status(selected_tier2),
            "available_providers": available_providers,
        }


llm_factory = LLMFactory()
