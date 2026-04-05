"""LLM provider 공통 인터페이스와 보조 헬퍼."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from trading.enums import LLMProvider, LLMTier, Tier1Profile

LLMSessionMode = Literal["ephemeral", "persistent"]


@dataclass(frozen=True)
class LLMProviderCapabilities:
    """Provider가 지원하는 실행 capability."""

    profile_specific_models: bool
    reasoning_effort_control: bool
    persistent_session: bool
    usage_reporting: str = "provider"

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_specific_models": self.profile_specific_models,
            "reasoning_effort_control": self.reasoning_effort_control,
            "persistent_session": self.persistent_session,
            "usage_reporting": self.usage_reporting,
        }


@dataclass(frozen=True)
class LLMSessionHandle:
    """Provider-agnostic 세션 상태."""

    provider: LLMProvider
    scope: str
    phase: str
    external_id: str | None
    session_enabled: bool
    initialized: bool

    @property
    def mode(self) -> LLMSessionMode:
        return "persistent" if self.session_enabled else "ephemeral"


@dataclass(frozen=True)
class LLMRequest:
    """호출부가 provider에 전달하는 공통 요청."""

    tier: LLMTier
    prompt: str
    system_prompt: str = ""
    scope: str | None = None
    phase: str = "cycle"
    requested_profile: Tier1Profile | None = None
    symbol: str | None = None
    cycle_id: str | None = None
    reasoning_effort_override: str | None = None


@dataclass(frozen=True)
class LLMExecutionPlan:
    """Provider가 계산한 실제 실행 계획."""

    provider: LLMProvider
    tier: LLMTier
    requested_profile: Tier1Profile | None
    effective_profile: Tier1Profile | None
    scope: str
    phase: str
    model: str
    reasoning_effort: str | None
    session_mode: LLMSessionMode
    session_handle: LLMSessionHandle
    capabilities: LLMProviderCapabilities

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "tier": self.tier.value,
            "requested_profile": self.requested_profile.value if self.requested_profile else None,
            "effective_profile": self.effective_profile.value if self.effective_profile else None,
            "scope": self.scope,
            "phase": self.phase,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "session_mode": self.session_mode,
            "session_id": self.session_handle.external_id,
            "capabilities": self.capabilities.as_dict(),
        }


@runtime_checkable
class LLMProviderProtocol(Protocol):
    """LLM 제공자 공통 인터페이스"""

    @property
    def provider(self) -> LLMProvider: ...

    @property
    def display_name(self) -> str: ...

    @property
    def tier(self) -> LLMTier: ...

    @property
    def configured_model(self) -> str: ...

    @property
    def configured_reasoning_effort(self) -> str | None: ...

    @property
    def model_id(self) -> str: ...

    @property
    def capabilities(self) -> LLMProviderCapabilities: ...

    def plan_request(self, request: LLMRequest) -> LLMExecutionPlan:
        """요청을 실제 실행 계획으로 변환"""
        ...

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        scope: str | None = None,
        phase: str = "cycle",
        reasoning_effort_override: str | None = None,
    ) -> str:
        """텍스트 생성"""
        ...

    async def generate_from_plan(
        self,
        request: LLMRequest,
        plan: LLMExecutionPlan,
    ) -> str:
        """사전 계산된 실행 계획으로 텍스트 생성"""
        ...

    async def is_available(self) -> bool:
        """사용 가능 여부 확인"""
        ...


@runtime_checkable
class LLMSessionProtocol(Protocol):
    """세션 유지형 LLM provider 공통 인터페이스"""

    @classmethod
    def start_session(cls, scope: str = "KRX", phase: str = "cycle") -> str | None:
        """새 세션 시작"""
        ...

    @classmethod
    def end_session(cls, scope: str = "KRX", phase: str = "cycle") -> str | None:
        """세션 종료"""
        ...

    @classmethod
    def pause_session(cls, scope: str = "KRX", phase: str = "cycle") -> str | None:
        """세션 일시 중지"""
        ...

    @classmethod
    def resume_session(cls, session_id: str, scope: str = "KRX", phase: str = "cycle") -> None:
        """세션 재개"""
        ...

    @classmethod
    def get_session_id(cls, scope: str | None = None, phase: str = "cycle") -> str | None:
        """현재 세션 ID 반환"""
        ...

    @classmethod
    def get_session_handle(cls, scope: str = "KRX", phase: str = "cycle") -> LLMSessionHandle:
        """현재 세션 상태 반환"""
        ...

    @classmethod
    def get_usage_snapshot(cls) -> dict[str, Any]:
        """누적 사용량 스냅샷 반환"""
        ...

    @classmethod
    def get_usage_report(cls) -> dict[str, Any]:
        """provider별 사용량 리포트 반환"""
        ...


def empty_usage_snapshot(provider: LLMProvider) -> dict[str, Any]:
    """provider 공통 사용량 스냅샷 기본값"""
    return {
        "provider": provider.value,
        "session_id": None,
        "total_calls": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read": 0,
        "total_cache_creation": 0,
        "total_cached_input_tokens": 0,
        "by_model": {},
        "provider_data": {},
    }
