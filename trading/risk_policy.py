"""Shared risk policy constants for prompt and runtime alignment."""

BULL_THEME_RR_FLOOR = 1.0
DEFENSIVE_RR_FLOOR = 1.2
CRYPTO_MOMENTUM_RR_FLOOR = 2.0
CRYPTO_DEFENSIVE_RR_FLOOR = 1.5

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


def normalize_crypto_regime(market_regime: str | None) -> str:
    """정규화된 코인 시장 국면 문자열을 반환한다."""

    regime = str(market_regime or "").strip().upper()
    if not regime:
        return ""
    return CRYPTO_REGIME_ALIASES.get(regime, regime)


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
