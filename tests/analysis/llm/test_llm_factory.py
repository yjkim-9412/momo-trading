import pytest

from analysis.llm.llm_factory import llm_factory
from core.config import settings
from trading.enums import LLMProvider, LLMTier


class DummyCodexProvider:
    _session_id = "dummy-session"

    def __init__(self, tier: LLMTier):
        self._tier = tier

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
        return "gpt-5.4"

    @property
    def model_id(self) -> str:
        return f"dummy:{self._tier.value}"

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        return f"{system_prompt}|{prompt}"

    async def is_available(self) -> bool:
        return True

    @classmethod
    def start_session(cls) -> str:
        return cls._session_id

    @classmethod
    def pause_session(cls) -> str:
        return cls._session_id

    @classmethod
    def resume_session(cls, session_id: str) -> None:
        cls._session_id = session_id

    @classmethod
    def end_session(cls) -> str:
        return cls._session_id

    @classmethod
    def get_session_id(cls) -> str:
        return cls._session_id

    @classmethod
    def get_usage_snapshot(cls) -> dict:
        return {
            "provider": LLMProvider.CODEX_CLI.value,
            "session_id": cls._session_id,
            "total_calls": 3,
            "total_input_tokens": 120,
            "total_output_tokens": 30,
            "total_cached_input_tokens": 40,
            "total_cache_read": 0,
            "total_cache_creation": 0,
            "total_cost_usd": 0.0,
            "by_model": {
                "codex:gpt-5.4": {
                    "calls": 3,
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "cached_input_tokens": 40,
                },
            },
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
    monkeypatch.setattr(llm_factory, "_selected_provider", None)
    monkeypatch.setattr(llm_factory, "_providers", {})


@pytest.mark.asyncio
async def test_generate_routes_to_selected_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, DummyCodexProvider)

    result, provider = await llm_factory.generate_tier1("PROMPT", system_prompt="SYSTEM")

    assert result == "SYSTEM|PROMPT"
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
    }
    assert status["session_id"] == "dummy-session"
    assert any(
        item["id"] == LLMProvider.CODEX_CLI.value and item["selected"]
        for item in status["available_providers"]
    )


def test_get_llm_usage_for_codex_returns_generic_shape(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", LLMProvider.CODEX_CLI.value)
    monkeypatch.setitem(llm_factory.PROVIDER_CLASSES, LLMProvider.CODEX_CLI, DummyCodexProvider)

    usage = llm_factory.get_llm_usage()

    assert usage["provider"] == LLMProvider.CODEX_CLI.value
    assert usage["summary"]["total_sessions"] == 7
    assert usage["app_usage"]["total_calls"] == 3
