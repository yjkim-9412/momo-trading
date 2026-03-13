"""Shared risk policy constants for prompt and runtime alignment."""

BULL_THEME_RR_FLOOR = 1.0
DEFENSIVE_RR_FLOOR = 1.2

DEFAULT_RR_FLOOR = {
    "THEME": BULL_THEME_RR_FLOOR,
    "BULL": BULL_THEME_RR_FLOOR,
    "BEAR": DEFENSIVE_RR_FLOOR,
    "SIDEWAYS": DEFENSIVE_RR_FLOOR,
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
