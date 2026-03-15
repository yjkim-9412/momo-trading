"""LLM provider 공통 인터페이스와 보조 헬퍼."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from trading.enums import LLMProvider, LLMTier


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
