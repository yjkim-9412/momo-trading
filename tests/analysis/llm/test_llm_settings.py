import pytest

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
    assert analysis_effort == "low"


def test_get_llm_reasoning_effort_defaults_tier2_to_high():
    settings = Settings(_env_file=None)

    effort = settings.get_llm_reasoning_effort(LLMProvider.CODEX_CLI, LLMTier.TIER2)

    assert effort == "high"


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
    assert tier1_analysis == "low"
    assert tier2 == "high"


def test_crypto_scope_provider_specific_model_and_effort():
    settings = Settings(
        _env_file=None,
        CRYPTO_LLM_PROVIDER=LLMProvider.CODEX_CLI.value,
        CRYPTO_LLM_MODEL_TIER1_SCAN="claude-crypto-scan",
        CRYPTO_CODEX_MODEL_TIER1_SCAN="codex-crypto-scan",
        CRYPTO_CODEX_MODEL_TIER1_ANALYSIS="codex-crypto-analysis",
        CRYPTO_CODEX_MODEL_TIER2="codex-crypto-tier2",
        CRYPTO_CLAUDE_EFFORT_TIER1_SCAN="high",
        CRYPTO_CODEX_REASONING_EFFORT_TIER1_SCAN="minimal",
    )

    claude_model = settings.get_llm_model_for_scope_provider(
        "CRYPTO",
        LLMProvider.CLAUDE_CODE,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )
    codex_scan = settings.get_llm_model_for_scope_provider(
        "CRYPTO",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.SCAN,
    )
    codex_analysis = settings.get_llm_model_for_scope_provider(
        "CRYPTO",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER1,
        Tier1Profile.ANALYSIS,
    )
    codex_tier2 = settings.get_llm_model_for_scope_provider(
        "CRYPTO",
        LLMProvider.CODEX_CLI,
        LLMTier.TIER2,
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
    assert codex_scan == "codex-crypto-scan"
    assert codex_analysis == "codex-crypto-analysis"
    assert codex_tier2 == "codex-crypto-tier2"
    assert claude_effort == "high"
    assert codex_effort == "minimal"


def test_crypto_codex_model_tier_fallback():
    """Codex 코인 모델 fallback 체인: tier별 → 기본 → 주식 tier별 → 주식 기본"""
    # tier별 미설정 → CRYPTO_CODEX_MODEL fallback
    s1 = Settings(_env_file=None, CRYPTO_CODEX_MODEL="codex-base")
    assert s1.get_crypto_llm_model_for_provider(
        LLMProvider.CODEX_CLI, LLMTier.TIER1, Tier1Profile.SCAN,
    ) == "codex-base"
    assert s1.get_crypto_llm_model_for_provider(
        LLMProvider.CODEX_CLI, LLMTier.TIER1, Tier1Profile.ANALYSIS,
    ) == "codex-base"
    assert s1.get_crypto_llm_model_for_provider(
        LLMProvider.CODEX_CLI, LLMTier.TIER2,
    ) == "codex-base"

    # 모두 미설정 → 주식 CODEX_MODEL_TIER1 / CODEX_MODEL fallback
    s2 = Settings(
        _env_file=None,
        CODEX_MODEL="codex-stock",
        CODEX_MODEL_TIER1="codex-stock-t1",
        CODEX_MODEL_TIER2="codex-stock-t2",
    )
    assert s2.get_crypto_llm_model_for_provider(
        LLMProvider.CODEX_CLI, LLMTier.TIER1, Tier1Profile.SCAN,
    ) == "codex-stock-t1"
    assert s2.get_crypto_llm_model_for_provider(
        LLMProvider.CODEX_CLI, LLMTier.TIER2,
    ) == "codex-stock-t2"

    # scan만 설정 → analysis는 scan fallback
    s3 = Settings(
        _env_file=None,
        CRYPTO_CODEX_MODEL_TIER1_SCAN="codex-scan-only",
    )
    assert s3.get_crypto_llm_model_for_provider(
        LLMProvider.CODEX_CLI, LLMTier.TIER1, Tier1Profile.ANALYSIS,
    ) == "codex-scan-only"

    # analysis만 설정 → scan은 analysis fallback
    s4 = Settings(
        _env_file=None,
        CRYPTO_CODEX_MODEL_TIER1_ANALYSIS="codex-analysis-only",
    )
    assert s4.get_crypto_llm_model_for_provider(
        LLMProvider.CODEX_CLI, LLMTier.TIER1, Tier1Profile.SCAN,
    ) == "codex-analysis-only"


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


def test_kis_virtual_profile_runtime_settings():
    settings = Settings(
        _env_file=None,
        KIS_ACCOUNT_TYPE="VIRTUAL",
        DATABASE_URL="sqlite:///./data/app.db",
    )

    assert settings.kis_account_type_normalized == "VIRTUAL"
    assert settings.is_paper_trading is True
    assert settings.is_real_trading is False
    assert settings.kis_runtime_database_url.endswith("app.virtual.db")
    assert settings.kis_token_file.endswith("kis_token.virtual.json")
    assert settings.kis_default_port == 9000
    assert settings.kis_log_suffix == "virtual"
    assert settings.kis_rest_policy["min_interval_seconds"] == 0.8


def test_kis_real_profile_runtime_settings():
    settings = Settings(
        _env_file=None,
        KIS_ACCOUNT_TYPE="REAL",
        DATABASE_URL="sqlite:///./data/app.db",
    )

    assert settings.kis_account_type_normalized == "REAL"
    assert settings.is_paper_trading is False
    assert settings.is_real_trading is True
    assert settings.kis_runtime_database_url.endswith("app.real.db")
    assert settings.kis_token_file.endswith("kis_token.real.json")
    assert settings.kis_default_port == 9100
    assert settings.kis_log_suffix == "real"
    assert settings.kis_rest_policy["min_interval_seconds"] == 0.15


def test_invalid_kis_account_type_raises():
    settings = Settings(_env_file=None, KIS_ACCOUNT_TYPE="paper")

    with pytest.raises(ValueError, match="KIS_ACCOUNT_TYPE"):
        _ = settings.kis_account_type_normalized


def test_event_runtime_defaults():
    settings = Settings(_env_file=None)

    assert settings.sql_echo is False
    assert settings.event_detector_dedup_seconds == 180
    assert settings.event_analysis_cooldown_seconds == 300
    assert settings.event_dynamic_limits_cache_seconds == 900
