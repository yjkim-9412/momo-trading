"""Shared risk policy constants for prompt and runtime alignment."""

BULL_THEME_RR_FLOOR = 1.0
DEFENSIVE_RR_FLOOR = 1.2
CRYPTO_MOMENTUM_RR_FLOOR = 2.0
CRYPTO_DEFENSIVE_RR_FLOOR = 1.5
CRYPTO_AGGRESSIVE_MOMENTUM_RR_FLOOR = 1.6

CRYPTO_TRADING_STYLE_CONSERVATIVE = "CONSERVATIVE"
CRYPTO_TRADING_STYLE_AGGRESSIVE = "AGGRESSIVE"

DEFAULT_RR_FLOOR = {
    "THEME": BULL_THEME_RR_FLOOR,
    "BULL": BULL_THEME_RR_FLOOR,
    "BEAR": DEFENSIVE_RR_FLOOR,
    "SIDEWAYS": DEFENSIVE_RR_FLOOR,
}

CRYPTO_REGIME_ALIASES = {
    "BULL": "BULL_RUN",
    "BEAR": "BEAR_MARKET",
    "SIDEWAYS": "CONSOLIDATION",
    "ALT_SEASON": "ALTSEASON",
}

CRYPTO_DEFAULT_RR_FLOOR = {
    "BULL_RUN": CRYPTO_MOMENTUM_RR_FLOOR,
    "ALTSEASON": CRYPTO_MOMENTUM_RR_FLOOR,
    "THEME": CRYPTO_MOMENTUM_RR_FLOOR,
    "BEAR_MARKET": CRYPTO_DEFENSIVE_RR_FLOOR,
    "CONSOLIDATION": CRYPTO_DEFENSIVE_RR_FLOOR,
    # legacy aliases
    "BULL": CRYPTO_MOMENTUM_RR_FLOOR,
    "BEAR": CRYPTO_DEFENSIVE_RR_FLOOR,
    "SIDEWAYS": CRYPTO_DEFENSIVE_RR_FLOOR,
    "ALT_SEASON": CRYPTO_MOMENTUM_RR_FLOOR,
}

CRYPTO_AGGRESSIVE_RR_FLOOR = {
    "BULL_RUN": CRYPTO_AGGRESSIVE_MOMENTUM_RR_FLOOR,
    "ALTSEASON": CRYPTO_AGGRESSIVE_MOMENTUM_RR_FLOOR,
    "THEME": CRYPTO_AGGRESSIVE_MOMENTUM_RR_FLOOR,
    "BEAR_MARKET": CRYPTO_DEFENSIVE_RR_FLOOR,
    "CONSOLIDATION": CRYPTO_DEFENSIVE_RR_FLOOR,
    # legacy aliases
    "BULL": CRYPTO_AGGRESSIVE_MOMENTUM_RR_FLOOR,
    "BEAR": CRYPTO_DEFENSIVE_RR_FLOOR,
    "SIDEWAYS": CRYPTO_DEFENSIVE_RR_FLOOR,
    "ALT_SEASON": CRYPTO_AGGRESSIVE_MOMENTUM_RR_FLOOR,
}

CRYPTO_RR_DEFAULTS_BY_MODE = {
    CRYPTO_TRADING_STYLE_CONSERVATIVE: CRYPTO_DEFAULT_RR_FLOOR,
    CRYPTO_TRADING_STYLE_AGGRESSIVE: CRYPTO_AGGRESSIVE_RR_FLOOR,
}


def normalize_crypto_regime(market_regime: str | None) -> str:
    """정규화된 코인 시장 국면 문자열을 반환한다."""

    regime = str(market_regime or "").strip().upper()
    if not regime:
        return ""
    return CRYPTO_REGIME_ALIASES.get(regime, regime)


def normalize_crypto_trading_style_mode(trading_style_mode: str | None) -> str:
    """정규화된 코인 매매 성향 문자열을 반환한다."""

    raw = str(trading_style_mode or "").strip().upper()
    if raw == CRYPTO_TRADING_STYLE_AGGRESSIVE:
        return CRYPTO_TRADING_STYLE_AGGRESSIVE
    return CRYPTO_TRADING_STYLE_CONSERVATIVE


def get_crypto_rr_defaults_for_mode(trading_style_mode: str | None) -> dict[str, float]:
    """코인 매매 성향별 기본 RR floor 집합을 반환한다."""

    mode = normalize_crypto_trading_style_mode(trading_style_mode)
    return dict(CRYPTO_RR_DEFAULTS_BY_MODE[mode])


def get_crypto_rr_thresholds_for_mode(trading_style_mode: str | None) -> tuple[float, float]:
    """코인 매매 성향별 (강세, 방어) RR floor를 반환한다."""

    defaults = get_crypto_rr_defaults_for_mode(trading_style_mode)
    return (
        float(defaults["BULL_RUN"]),
        float(defaults["BEAR_MARKET"]),
    )


def get_crypto_trading_style_profile(trading_style_mode: str | None) -> dict[str, str | float]:
    """코인 매매 성향의 표시용/런타임용 프로필을 반환한다."""

    mode = normalize_crypto_trading_style_mode(trading_style_mode)
    momentum_rr_floor, defensive_rr_floor = get_crypto_rr_thresholds_for_mode(mode)
    if mode == CRYPTO_TRADING_STYLE_AGGRESSIVE:
        return {
            "mode": mode,
            "label": "공격적",
            "summary": "강세장 RR 1.6 허용, 거래대금 유지형 돌파는 BUY 우선 검토",
            "momentum_rr_floor": momentum_rr_floor,
            "defensive_rr_floor": defensive_rr_floor,
        }
    return {
        "mode": mode,
        "label": "보수적",
        "summary": "RR 2.0 우선, 늦은 진입과 애매한 돌파는 엄격 차단",
        "momentum_rr_floor": momentum_rr_floor,
        "defensive_rr_floor": defensive_rr_floor,
    }


def resolve_rr_floor(
    market_regime: str,
    rr_floor_overrides: dict[str, float] | None = None,
    *,
    defaults: dict[str, float] | None = None,
) -> float:
    """Resolve RR floor with `regime > ALL > default` precedence."""

    regime = str(market_regime or "").upper()
    base_defaults = {str(key).upper(): float(value) for key, value in (defaults or DEFAULT_RR_FLOOR).items()}
    overrides = {str(key).upper(): float(value) for key, value in (rr_floor_overrides or {}).items()}

    if regime in overrides:
        return overrides[regime]
    if "ALL" in overrides:
        return overrides["ALL"]
    return base_defaults.get(regime, base_defaults["SIDEWAYS"])


def resolve_crypto_rr_floor(
    market_regime: str,
    trading_style_mode: str | None = None,
    rr_floor_overrides: dict[str, float] | None = None,
) -> float:
    """코인 매매 성향과 시장 국면을 반영한 RR floor를 반환한다."""

    regime = normalize_crypto_regime(market_regime)
    defaults = get_crypto_rr_defaults_for_mode(trading_style_mode)
    return resolve_rr_floor(
        regime,
        rr_floor_overrides,
        defaults=defaults,
    )
