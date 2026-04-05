import pytest
from unittest.mock import AsyncMock, patch

from analysis.llm.llm_factory import llm_factory
from core.config import settings
from trading.enums import LLMProvider, LLMTier, Tier1Profile


class DummyCodexProvider:
    _sessions: dict[tuple[str, str], str | None] = {("KRX", "cycle"): "dummy-session"}
    _usage: dict[str, object] = {}

    def __init__(
        self,
        tier: LLMTier,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ):
        self._tier = tier
        self._model = model or "gpt-5.4"
        self._reasoning_effort = reasoning_effort or (
            "medium" if self._tier == LLMTier.TIER1 else "high"
        )

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.CODEX_CLI

    @property
    def display_name(self) -> str:
        return "Codex CLI (테스트)"

    @property
    def tier(self) -> LLMTier:
        return self._tier

    @property
    def configured_model(self) -> str:
        return self._model

    @property
    def configured_reasoning_effort(self) -> str | None:
        return self._reasoning_effort

    @property
    def model_id(self) -> str:
        return f"dummy:{self._tier.value}"

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        scope: str | None = None,
        phase: str = "cycle",
        reasoning_effort_override: str | None = None,
    ) -> str:
        session = self.get_session_id(scope, phase) or "no-session"
        effort = reasoning_effort_override or self.configured_reasoning_effort or "none"
        usage = self._usage.setdefault(
            "codex:gpt-5.4",
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
            },
        )
        usage["calls"] += 1
        usage["input_tokens"] += 40
        usage["output_tokens"] += 10
        usage["cached_input_tokens"] += 5
        return (
            f"{self.provider.value}:{self.configured_model}:{scope or 'NONE'}:"
            f"{phase}:{session}:{effort}|{system_prompt}|{prompt}"
        )

    async def is_available(self) -> bool:
        return True

    @classmethod
    def start_session(cls, scope: str = "KRX", phase: str = "cycle") -> str:
        session_id = f"{scope.lower()}-{phase}-session"
        cls._sessions[(scope, phase)] = session_id
        return session_id

    @classmethod
    def pause_session(cls, scope: str = "KRX", phase: str = "cycle") -> str | None:
        return cls._sessions.get((scope, phase))

    @classmethod
    def resume_session(cls, session_id: str, scope: str = "KRX", phase: str = "cycle") -> None:
        cls._sessions[(scope, phase)] = session_id

    @classmethod
    def end_session(cls, scope: str = "KRX", phase: str = "cycle") -> str | None:
        return cls._sessions.get((scope, phase))

    @classmethod
    def get_session_id(cls, scope: str | None = None, phase: str = "cycle") -> str | None:
        if scope is None:
            return next(iter(cls._sessions.values()), None)
        return cls._sessions.get((scope, phase))

    @classmethod
    def get_usage_snapshot(cls) -> dict:
        total_calls = sum(int(stats["calls"]) for stats in cls._usage.values())
        total_input_tokens = sum(int(stats["input_tokens"]) for stats in cls._usage.values())
        total_output_tokens = sum(int(stats["output_tokens"]) for stats in cls._usage.values())
        total_cached_input_tokens = sum(
            int(stats["cached_input_tokens"])
            for stats in cls._usage.values()
        )
        return {
            "provider": LLMProvider.CODEX_CLI.value,
            "session_id": cls.get_session_id(),
            "total_calls": total_calls,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cached_input_tokens": total_cached_input_tokens,
            "total_cache_read": 0,
            "total_cache_creation": 0,
            "total_cost_usd": 0.0,
            "by_model": {model: dict(stats) for model, stats in cls._usage.items()},
        }

    @classmethod
    def get_usage_report(cls) -> dict:
        return {
            "provider": LLMProvider.CODEX_CLI.value,
            "selected_provider": LLMProvider.CODEX_CLI.value,
            "provider_name": "Codex CLI (테스트)",
            "summary": {
                "total_sessions": 7,
                "total_messages": None,
            },
            "model_usage": {},
            "daily_activity": [],
            "daily_model_tokens": [],
            "app_usage": cls.get_usage_snapshot(),
        }


@pytest.fixture(autouse=True)
def reset_llm_factory(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CLAUDE_CODE.value)
    monkeypatch.setattr(settings, "CRYPTO_LLM_PROVIDER", "")
    monkeypatch.setattr(settings, "CODEX_MODEL", "gpt-5.4")
    monkeypatch.setattr(settings, "CODEX_MODEL_TIER1", "")
    monkeypatch.setattr(settings, "CODEX_MODEL_TIER2", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_REPORT", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER1", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER1_SCAN", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER1_ANALYSIS", "low")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER2", "high")
    monkeypatch.setattr(settings, "CRYPTO_CODEX_MODEL", "")
    monkeypatch.setattr(settings, "CRYPTO_CODEX_MODEL_TIER1_SCAN", "")
    monkeypatch.setattr(settings, "CRYPTO_CODEX_MODEL_TIER1_ANALYSIS", "")
    monkeypatch.setattr(settings, "CRYPTO_CODEX_MODEL_TIER2", "")
    monkeypatch.setattr(llm_factory, "_selected_provider", None)
    monkeypatch.setattr(llm_factory, "_providers", {})
    monkeypatch.setattr(llm_factory, "_crypto_provider", None)
    monkeypatch.setattr(llm_factory, "_crypto_providers", {})
    DummyCodexProvider._sessions = {("KRX", "cycle"): "dummy-session"}
    DummyCodexProvider._usage = {
        "codex:gpt-5.4": {
            "calls": 3,
            "input_tokens": 120,
            "output_tokens": 30,
            "cached_input_tokens": 40,
        },
    }
    monkeypatch.setattr(llm_factory, "_usage_by_scope_tier", {})
    monkeypatch.setattr(llm_factory, "_cycle_usage_markers", {})
    monkeypatch.setattr(llm_factory, "_last_cycle_delta", None)


@pytest.mark.asyncio
async def test_generate_routes_to_selected_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, DummyCodexProvider)

    result, provider = await llm_factory.generate_tier1(
        "PROMPT",
        system_prompt="SYSTEM",
        profile=Tier1Profile.SCAN,
        scope="KRX",
        phase="cycle",
    )

    assert result == "CODEX_CLI:gpt-5.4:KRX:cycle:dummy-session:low|SYSTEM|PROMPT"
    assert provider == LLMProvider.CODEX_CLI.value
    usage = llm_factory.get_llm_usage()
    assert usage["by_scope_tier"]["KRX:TIER1"]["calls"] >= 1


@pytest.mark.asyncio
async def test_generate_report_uses_report_specific_effort(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setattr(settings, "CRYPTO_LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, DummyCodexProvider)

    result, provider = await llm_factory.generate_tier1(
        "PROMPT",
        system_prompt="SYSTEM",
        profile=Tier1Profile.ANALYSIS,
        scope="CRYPTO",
        phase="report",
    )

    assert result == "CODEX_CLI:gpt-5.4:CRYPTO:report:no-session:xhigh|SYSTEM|PROMPT"
    assert provider == LLMProvider.CODEX_CLI.value


@pytest.mark.asyncio
async def test_get_llm_status_uses_selected_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, DummyCodexProvider)
    monkeypatch.setattr(
        settings.__class__,
        "get_llm_cli_path",
        lambda self, provider: f"/tmp/{provider.value.lower()}",
    )

    status = await llm_factory.get_llm_status()

    assert status["selected_provider"] == LLMProvider.CODEX_CLI.value
    assert status["provider_name"] == "Codex CLI (로컬)"
    assert status["tier1"] == {
        "provider": LLMProvider.CODEX_CLI.value,
        "model": "gpt-5.4",
        "reasoning_effort": "low",
        "display_name": "후보 분석 에이전트",
        "short_label": "후보 분석",
        "description": "차트·시장 컨텍스트를 바탕으로 매수 후보와 목표/손절을 1차 판단",
    }
    assert status["tier1_profiles"]["scan"]["reasoning_effort"] == "low"
    assert status["tier1_profiles"]["analysis"]["reasoning_effort"] == "low"
    assert status["tier2"]["reasoning_effort"] == "high"
    assert status["tier2"]["display_name"] == "최종 검토 에이전트"
    assert status["session_id"] == "dummy-session"
    assert any(
        item["id"] == LLMProvider.CODEX_CLI.value
        and item["selected"]
        and item["reasoning_efforts"]["tier1_profiles"]["scan"] == "low"
        for item in status["available_providers"]
    )


def test_get_llm_usage_for_codex_returns_generic_shape(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, DummyCodexProvider)

    usage = llm_factory.get_llm_usage()

    assert usage["provider"] == LLMProvider.CODEX_CLI.value
    assert usage["summary"]["total_sessions"] == 7
    assert usage["app_usage"]["total_calls"] == 3
    assert usage["last_cycle_delta"] is None
    assert usage["by_scope_tier"] == {}


def test_session_methods_are_scope_aware(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, DummyCodexProvider)

    krx_sid = llm_factory.start_session("KRX", "cycle")
    us_sid = llm_factory.start_session("US", "cycle")

    assert krx_sid == "krx-cycle-session"
    assert us_sid == "us-cycle-session"
    assert llm_factory.get_session_id("KRX", "cycle") == "krx-cycle-session"
    assert llm_factory.get_session_id("US", "cycle") == "us-cycle-session"


class FailingCodexProvider(DummyCodexProvider):
    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        scope: str | None = None,
        phase: str = "cycle",
        reasoning_effort_override: str | None = None,
    ) -> str:
        raise RuntimeError(
            'Codex CLI 실패 (exit 1): Auth(TokenRefreshFailed("Failed to parse server response"))'
        )


class NonFallbackCodexProvider(DummyCodexProvider):
    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        scope: str | None = None,
        phase: str = "cycle",
        reasoning_effort_override: str | None = None,
    ) -> str:
        raise RuntimeError("prompt parse failed")


class DummyClaudeProvider(DummyCodexProvider):
    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.CLAUDE_CODE

    @property
    def display_name(self) -> str:
        return "Claude Code (테스트)"

    @property
    def model_id(self) -> str:
        return f"dummy-claude:{self._tier.value}"


@pytest.mark.asyncio
async def test_crypto_codex_auth_failure_falls_back_to_claude(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setattr(settings, "CRYPTO_LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setattr(settings, "CRYPTO_LLM_MODEL_TIER1_SCAN", "claude-crypto-scan")
    monkeypatch.setattr(settings, "CRYPTO_CODEX_MODEL", "codex-crypto")
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, FailingCodexProvider)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CLAUDE_CODE, DummyClaudeProvider)

    with patch("services.activity_logger.activity_logger.log", AsyncMock()) as log_activity:
        result, provider = await llm_factory.generate_tier1(
            "PROMPT",
            system_prompt="SYSTEM",
            profile=Tier1Profile.SCAN,
            scope="CRYPTO",
            phase="cycle",
            cycle_id="cycle-1",
        )

    assert provider == LLMProvider.CLAUDE_CODE.value
    assert result.startswith("CLAUDE_CODE:claude-crypto-scan:CRYPTO:cycle:")
    assert any("fallback" in call.args[2] for call in log_activity.await_args_list)


@pytest.mark.asyncio
async def test_crypto_report_codex_auth_failure_falls_back_to_claude_with_report_effort(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setattr(settings, "CRYPTO_LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setattr(settings, "CRYPTO_CODEX_MODEL", "codex-crypto")
    monkeypatch.setattr(settings, "CRYPTO_CLAUDE_EFFORT_REPORT", "max")
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, FailingCodexProvider)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CLAUDE_CODE, DummyClaudeProvider)

    with patch("services.activity_logger.activity_logger.log", AsyncMock()):
        result, provider = await llm_factory.generate_tier1(
            "PROMPT",
            system_prompt="SYSTEM",
            profile=Tier1Profile.ANALYSIS,
            scope="CRYPTO",
            phase="report",
            cycle_id="cycle-report-1",
        )

    assert provider == LLMProvider.CLAUDE_CODE.value
    assert result.startswith("CLAUDE_CODE:")
    assert ":CRYPTO:report:" in result
    assert ":max|SYSTEM|PROMPT" in result


@pytest.mark.asyncio
async def test_crypto_non_auth_failure_does_not_fallback(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setattr(settings, "CRYPTO_LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, NonFallbackCodexProvider)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CLAUDE_CODE, DummyClaudeProvider)

    with pytest.raises(RuntimeError, match="prompt parse failed"):
        await llm_factory.generate_tier1(
            "PROMPT",
            system_prompt="SYSTEM",
            profile=Tier1Profile.SCAN,
            scope="CRYPTO",
            phase="cycle",
        )
