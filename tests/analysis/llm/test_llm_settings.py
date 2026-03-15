import core.config as config_module
from core.config import Settings
from trading.enums import LLMProvider, LLMTier, Tier1Profile


class DummyLogger:
    def __init__(self):
        self.warnings: list[str] = []

    def info(self, _message: str, *args) -> None:
        return None

    def warning(self, message: str, *args) -> None:
        self.warnings.append(message.format(*args))


def test_get_llm_reasoning_effort_uses_codex_global_fallback():
    settings = Settings(
        _env_file=None,
        CODEX_REASONING_EFFORT="HIGH",
    )

    effort = settings.get_llm_reasoning_effort(
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )

    assert effort == "high"


def test_get_llm_reasoning_effort_prefers_profile_specific_value():
    settings = Settings(
        _env_file=None,
        CODEX_REASONING_EFFORT="low",
        CODEX_REASONING_EFFORT_TIER1="medium",
        CODEX_REASONING_EFFORT_TIER1_SCAN="MINIMAL",
        CODEX_REASONING_EFFORT_TIER1_ANALYSIS="HIGH",
        CODEX_REASONING_EFFORT_TIER2="XHIGH",
    )

    tier1_scan = settings.get_llm_reasoning_effort(
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )
    tier1_analysis = settings.get_llm_reasoning_effort(
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
    )
    tier2 = settings.get_llm_reasoning_effort(LLMProvider.CODEX_CLI, LLMTier.TIER2)

    assert tier1_scan == "minimal"
    assert tier1_analysis == "high"
    assert tier2 == "xhigh"


def test_get_llm_reasoning_effort_defaults_tier1_profiles():
    settings = Settings(_env_file=None)

    scan_effort = settings.get_llm_reasoning_effort(
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )
    analysis_effort = settings.get_llm_reasoning_effort(
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
    )

    assert scan_effort == "low"
    assert analysis_effort == "medium"


def test_get_llm_reasoning_effort_defaults_tier2_to_xhigh():
    settings = Settings(_env_file=None)

    effort = settings.get_llm_reasoning_effort(LLMProvider.CODEX_CLI, LLMTier.TIER2)

    assert effort == "xhigh"


def test_get_llm_reasoning_effort_ignores_invalid_value():
    settings = Settings(
        _env_file=None,
        CODEX_REASONING_EFFORT="invalid",
        CODEX_REASONING_EFFORT_TIER1=" medium ",
        CODEX_REASONING_EFFORT_TIER1_SCAN=" wrong ",
        CODEX_REASONING_EFFORT_TIER2="",
    )

    tier1_scan = settings.get_llm_reasoning_effort(
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )
    tier1_analysis = settings.get_llm_reasoning_effort(
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
    )
    tier2 = settings.get_llm_reasoning_effort(LLMProvider.CODEX_CLI, LLMTier.TIER2)

    assert tier1_scan == "medium"
    assert tier1_analysis == "medium"
    assert tier2 == "xhigh"


def test_crypto_scope_provider_specific_model_and_effort():
    settings = Settings(
        _env_file=None,
        CRYPTO_LLM_PROVIDER=LLMProvider.CODEX_CLI.value,
        CRYPTO_LLM_MODEL_TIER1_SCAN="claude-crypto-scan",
        CRYPTO_CODEX_MODEL="codex-crypto",
        CRYPTO_CLAUDE_EFFORT_TIER1_SCAN="high",
        CRYPTO_CODEX_REASONING_EFFORT_TIER1_SCAN="minimal",
    )

    claude_model = settings.get_llm_model_for_scope_provider(
        "CRYPTO",
        LLMProvider.CLAUDE_CODE,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )
    codex_model = settings.get_llm_model_for_scope_provider(
        "CRYPTO",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )
    claude_effort = settings.get_llm_reasoning_effort_for_scope_provider(
        "CRYPTO",
        LLMProvider.CLAUDE_CODE,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )
    codex_effort = settings.get_llm_reasoning_effort_for_scope_provider(
        "CRYPTO",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )

    assert claude_model == "claude-crypto-scan"
    assert codex_model == "codex-crypto"
    assert claude_effort == "high"
    assert codex_effort == "minimal"


def test_report_phase_defaults_codex_to_xhigh():
    settings = Settings(_env_file=None)

    stock_effort = settings.get_llm_reasoning_effort_for_scope_provider(
        "KRX",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
        phase="report",
    )
    crypto_effort = settings.get_llm_reasoning_effort_for_scope_provider(
        "CRYPTO",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
        phase="report",
    )

    assert stock_effort == "xhigh"
    assert crypto_effort == "xhigh"


def test_report_phase_prefers_report_overrides_and_allows_claude_max():
    settings = Settings(
        _env_file=None,
        CLAUDE_CODE_EFFORT_REPORT="high",
        CRYPTO_CLAUDE_EFFORT_REPORT="MAX",
        CODEX_REASONING_EFFORT_REPORT="HIGH",
        CRYPTO_CODEX_REASONING_EFFORT_REPORT="minimal",
    )

    stock_claude_effort = settings.get_llm_reasoning_effort_for_scope_provider(
        "KRX",
        LLMProvider.CLAUDE_CODE,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
        phase="report",
    )
    crypto_claude_effort = settings.get_llm_reasoning_effort_for_scope_provider(
        "CRYPTO",
        LLMProvider.CLAUDE_CODE,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
        phase="report",
    )
    stock_codex_effort = settings.get_llm_reasoning_effort_for_scope_provider(
        "KRX",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
        phase="report",
    )
    crypto_codex_effort = settings.get_llm_reasoning_effort_for_scope_provider(
        "CRYPTO",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
        phase="report",
    )

    assert stock_claude_effort == "high"
    assert crypto_claude_effort == "max"
    assert stock_codex_effort == "high"
    assert crypto_codex_effort == "minimal"


def test_validate_on_startup_warns_for_invalid_codex_reasoning_effort(monkeypatch):
    dummy_logger = DummyLogger()
    monkeypatch.setattr(config_module, "logger", dummy_logger)

    settings = Settings(
        _env_file=None,
        LLM_PROVIDER=LLMProvider.CODEX_CLI.value,
        CODEX_REASONING_EFFORT="invalid",
        KIS_APP_KEY="test-key",
        TRADING_ENABLED=True,
    )
    monkeypatch.setattr(
        settings.__class__,
        "get_llm_cli_path",
        lambda self, provider: f"/tmp/{provider.value.lower()}",
    )

    settings.validate_on_startup()

    assert any("CODEX_REASONING_EFFORT=invalid" in warning for warning in dummy_logger.warnings)
