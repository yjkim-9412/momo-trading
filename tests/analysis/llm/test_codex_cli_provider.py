import os

from analysis.llm.codex_cli_provider import CodexCLIProvider
from core.config import settings
from trading.enums import LLMTier


def test_parse_event_stream_extracts_session_usage_and_text():
    raw_stdout = """
{"type":"thread.started","thread_id":"thread-123"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"RESULT"}}
{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":4,"output_tokens":3}}
    """.strip()

    parsed = CodexCLIProvider.parse_event_stream(raw_stdout)

    assert parsed["session_id"] == "thread-123"
    assert parsed["fallback_text"] == "RESULT"
    assert parsed["usage"] == {
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 3,
    }


def test_build_command_adds_override_reasoning_effort_on_first_session_call(monkeypatch):
    monkeypatch.setattr(settings, "CODEX_MODEL", "gpt-5.4")
    monkeypatch.setattr(settings, "CODEX_MODEL_TIER1", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER1", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER1_ANALYSIS", "")

    provider = CodexCLIProvider(LLMTier.TIER1)
    state = {
        "session_enabled": True,
        "session_initialized": False,
        "active_session_id": None,
    }
    cmd, output_path = provider._build_command("/tmp/codex", state, "low")

    try:
        assert cmd[:2] == ["/tmp/codex", "exec"]
        assert "model_reasoning_effort=low" in cmd
        assert cmd[-1] == "-"
    finally:
        os.remove(output_path)


def test_build_command_adds_reasoning_effort_on_resume(monkeypatch):
    monkeypatch.setattr(settings, "CODEX_MODEL", "gpt-5.4")
    monkeypatch.setattr(settings, "CODEX_MODEL_TIER2", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER2", "xhigh")

    provider = CodexCLIProvider(LLMTier.TIER2)
    state = {
        "session_enabled": True,
        "session_initialized": True,
        "active_session_id": "thread-123",
    }
    cmd, output_path = provider._build_command("/tmp/codex", state)

    try:
        assert cmd[:3] == ["/tmp/codex", "exec", "resume"]
        assert "model_reasoning_effort=xhigh" in cmd
        assert cmd[-2:] == ["thread-123", "-"]
    finally:
        os.remove(output_path)


def test_build_command_uses_provider_default_when_override_missing(monkeypatch):
    monkeypatch.setattr(settings, "CODEX_MODEL", "gpt-5.4")
    monkeypatch.setattr(settings, "CODEX_MODEL_TIER1", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER1", "")
    monkeypatch.setattr(settings, "CODEX_REASONING_EFFORT_TIER1_ANALYSIS", "")

    provider = CodexCLIProvider(LLMTier.TIER1)
    state = {
        "session_enabled": False,
        "session_initialized": False,
        "active_session_id": None,
    }
    cmd, output_path = provider._build_command("/tmp/codex", state, provider.configured_reasoning_effort)

    try:
        assert "model_reasoning_effort=medium" in cmd
        assert "--ephemeral" in cmd
    finally:
        os.remove(output_path)
