from __future__ import annotations

import os
import shutil

from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading.enums import LLMProvider, LLMTier, Tier1Profile

VALID_CODEX_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
REPORT_LLM_PHASES = {"report", "after_hours"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    APP_NAME: str = "momo-trading"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "local"  # local | staging | production

    DATABASE_URL: str = "sqlite:///./data/app.db"
    LOG_LEVEL: str = "DEBUG"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # === KIS MCP 서버 ===
    KIS_MCP_URL: str = "http://localhost:3100/sse"

    # KIS API 인증
    KIS_APP_KEY: str = ""
    KIS_APP_SECRET: str = ""
    KIS_PAPER_APP_KEY: str = ""
    KIS_PAPER_APP_SECRET: str = ""
    KIS_HTS_ID: str = ""
    KIS_ACCT_STOCK: str = ""
    KIS_PAPER_STOCK: str = ""
    KIS_PROD_TYPE: str = ""
    KIS_ACCOUNT_TYPE: str = "VIRTUAL"

    # KIS WebSocket
    KIS_WS_URL_DOMESTIC: str = "ws://ops.koreainvestment.com:21000"
    KIS_WS_URL_OVERSEAS: str = "ws://ops.koreainvestment.com:31000"

    # === Multi-market Runtime ===
    ENABLED_MARKETS: str = "KRX"
    PRIMARY_MARKET: str = "KRX"
    US_SCAN_MARKETS: str = "NASDAQ,NYSE,AMEX"
    US_TRADING_ENABLED: bool = False
    US_PREMARKET_ENABLED: bool = False
    US_AFTERMARKET_ENABLED: bool = False
    US_WATCHLIST_SYMBOLS: str = "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD"
    US_SCAN_LIMIT: int = 12
    US_DYNAMIC_DISCOVERY_ENABLED: bool = True  # MCP 동적 발굴 + 워치리스트 병합
    BASE_CURRENCY: str = "KRW"
    FX_RATE_SOURCE: str = "KIS"
    US_LEVERAGED_PRODUCTS_ENABLED: bool = True
    US_INVERSE_PRODUCTS_ENABLED: bool = True
    US_LEVERAGE_ALLOWED_SESSIONS: str = "US_REGULAR"
    US_LEVERAGE_ALLOWED_STRATEGIES: str = "STABLE_SHORT"
    US_LEVERAGE_MAX_SINGLE_ORDER_RATIO: float = 0.3
    US_LEVERAGE_ALLOWLIST: str = ""
    US_LEVERAGE_DENYLIST: str = ""
    US_BUY_CUTOFF_HOUR: int = 15  # 미국장 신규 매수 마감 시각 (ET)
    US_BUY_CUTOFF_MINUTE: int = 30
    US_FORCE_LIQUIDATION_HOUR: int = 15  # 미국장 강제 청산 시각 (ET)
    US_FORCE_LIQUIDATION_MINUTE: int = 40
    US_LEVERAGE_KEYWORDS: str = "2X,3X,ULTRA,ULTRAPRO,LEVERAGED"
    US_INVERSE_KEYWORDS: str = "INVERSE,SHORT,BEAR"

    # === Bithumb API 인증 (주식 KIS와 완전 별도) ===
    BITHUMB_API_KEY: str = ""
    BITHUMB_API_SECRET: str = ""
    BITHUMB_WS_URL_PUBLIC: str = "wss://ws-api.bithumb.com/websocket/v1"
    BITHUMB_WS_URL_PRIVATE: str = "wss://ws-api.bithumb.com/websocket/v1/private"

    # === Crypto 운영 (주식 TRADING_ENABLED, AUTONOMY_MODE 등과 독립) ===
    CRYPTO_ENABLED: bool = False
    CRYPTO_PRIMARY_MARKET: str = "BITHUMB"
    CRYPTO_TRADING_ENABLED: bool = False
    CRYPTO_AUTONOMY_MODE: str = "SEMI_AUTO"  # SEMI_AUTO / AUTONOMOUS
    CRYPTO_TRADING_STYLE_MODE: str = "CONSERVATIVE"  # CONSERVATIVE / AGGRESSIVE
    CRYPTO_RECOMMENDATION_EXPIRE_MIN: int = 30
    CRYPTO_TIMEBOX_HOURS: int = 12

    # === Crypto 스캔 ===
    CRYPTO_SCAN_INTERVAL_HOURS: int = 4
    CRYPTO_HOLDINGS_CHECK_INTERVAL_HOURS: int = 2
    CRYPTO_WATCHLIST_SYMBOLS: str = "BTC,ETH,XRP,SOL,ADA,DOGE"
    CRYPTO_SCAN_LIMIT: int = 15
    CRYPTO_DYNAMIC_DISCOVERY_ENABLED: bool = True  # 전체 overview 발굴 + watchlist 시드 병합
    CRYPTO_DISCOVERY_REFRESH_MINUTES: int = 360  # 전체 market universe 새로고침 주기
    CRYPTO_DISCOVERY_UNIVERSE_SIZE: int = 30  # broad refresh 후 유지할 상위 유동성 코인 수

    # === Crypto 리스크 (주식 리스크 설정과 독립) ===
    CRYPTO_MAX_POSITION_PCT: float = 20.0
    CRYPTO_MIN_CASH_RATIO: float = 0.10
    CRYPTO_MAX_SINGLE_ORDER_KRW: int = 0  # 0 = AI 자율 결정
    CRYPTO_MAX_DAILY_TRADES: int = 0  # 0 = 무제한
    CRYPTO_MIN_BUY_QUANTITY: float = 0.0

    # === Crypto LLM (비어있으면 주식 LLM 설정을 그대로 사용) ===
    CRYPTO_LLM_PROVIDER: str = ""  # CLAUDE_CODE / CODEX_CLI, 비어있으면 LLM_PROVIDER 사용
    CRYPTO_LLM_MODEL_TIER1_SCAN: str = ""  # 코인 스캔용 모델 (비어있으면 주식 Tier1 모델)
    CRYPTO_LLM_MODEL_TIER1_ANALYSIS: str = ""  # 코인 분석용 모델 (비어있으면 스캔 모델 → 주식 모델)
    CRYPTO_LLM_MODEL_TIER2: str = ""  # 코인 최종검토 모델
    # Claude Code effort (비어있으면 주식 기본값: TIER1=medium, TIER2=high)
    CRYPTO_CLAUDE_EFFORT_TIER1_SCAN: str = ""  # 코인 스캔 effort
    CRYPTO_CLAUDE_EFFORT_TIER1_ANALYSIS: str = ""  # 코인 분석 effort
    CRYPTO_CLAUDE_EFFORT_TIER2: str = ""  # 코인 최종검토 effort
    CRYPTO_CLAUDE_EFFORT_REPORT: str = ""  # 코인 체크포인트 리포트 effort (예: max)
    # Codex CLI 설정
    CRYPTO_CODEX_MODEL: str = ""  # 코인 Codex 기본 모델 (fallback), 비어있으면 CODEX_MODEL 사용
    CRYPTO_CODEX_MODEL_TIER1_SCAN: str = ""  # 코인 스캔용 Codex 모델
    CRYPTO_CODEX_MODEL_TIER1_ANALYSIS: str = ""  # 코인 분석용 Codex 모델
    CRYPTO_CODEX_MODEL_TIER2: str = ""  # 코인 최종검토용 Codex 모델
    CRYPTO_CODEX_REASONING_EFFORT_REPORT: str = ""  # 코인 체크포인트 리포트 추론 강도
    CRYPTO_CODEX_REASONING_EFFORT_TIER1_SCAN: str = ""  # 코인 스캔 추론 강도
    CRYPTO_CODEX_REASONING_EFFORT_TIER1_ANALYSIS: str = ""  # 코인 분석 추론 강도
    CRYPTO_CODEX_REASONING_EFFORT_TIER2: str = ""  # 코인 최종검토 추론 강도

    # === AI / LLM ===
    LLM_PROVIDER: str = LLMProvider.CLAUDE_CODE.value

    # Claude Code CLI
    CLAUDE_CODE_MODEL: str = "sonnet"  # 기본 모델 (Tier별 미지정 시 사용)
    CLAUDE_CODE_MODEL_TIER1: str = "haiku"  # Tier1 (스캔/분석): 빠른 모델
    CLAUDE_CODE_MODEL_TIER2: str = "sonnet"  # Tier2 (최종 검토): 정확한 모델
    CLAUDE_CODE_EFFORT_REPORT: str = ""  # 리포트/회고 생성 effort (예: max)
    CLAUDE_CODE_PATH: str = ""  # 비어있으면 자동 탐색 (예: /opt/homebrew/bin/claude)

    # Codex CLI
    CODEX_MODEL: str = "gpt-5.4"
    CODEX_MODEL_TIER1: str = ""
    CODEX_MODEL_TIER2: str = ""
    CODEX_REASONING_EFFORT: str = ""
    CODEX_REASONING_EFFORT_REPORT: str = ""  # 리포트/회고 생성 추론 강도
    CODEX_REASONING_EFFORT_TIER1: str = ""
    CODEX_REASONING_EFFORT_TIER1_SCAN: str = ""
    CODEX_REASONING_EFFORT_TIER1_ANALYSIS: str = ""
    CODEX_REASONING_EFFORT_TIER2: str = "xhigh"
    CODEX_CLI_PATH: str = ""

    # === AI Agent ===
    AUTONOMY_MODE: str = "AUTONOMOUS"  # AUTONOMOUS / SEMI_AUTO
    RECOMMENDATION_EXPIRE_MIN: int = 60
    MIN_BUY_QUANTITY: int = 1

    # === Trading Safety ===
    TRADING_ENABLED: bool = True
    DAY_TRADING_ONLY: bool = False  # True=당일 청산 필수, False=스윙 (유망 종목 오버나이트 보유)
    BUY_CUTOFF_HOUR: int = 14  # 신규 매수 마감 시각 (14시 이후 매수 차단)
    BUY_CUTOFF_MINUTE: int = 30
    FORCE_LIQUIDATION_HOUR: int = 15  # 강제 청산 시각 (종가경매 전)
    FORCE_LIQUIDATION_MINUTE: int = 10
    MAX_HOLD_DAYS_STABLE: int = 5  # STABLE_SHORT 최대 보유일
    MAX_HOLD_DAYS_AGGRESSIVE: int = 3  # AGGRESSIVE_SHORT 최대 보유일
    MAX_DAILY_TRADES: int = 0  # 0 = 무제한
    MAX_SINGLE_ORDER_KRW: int = 0  # 0 = AI 자율 결정 (시스템 하드 리밋 없음)

    # === AI Risk Tuning ===
    AI_RISK_TUNING_ENABLED: bool = True
    RISK_APPETITE: str = "AGGRESSIVE"  # CONSERVATIVE / MODERATE / AGGRESSIVE
    MIN_CASH_RATIO: float = 0.05  # 최소 현금 비중 5% (빠른 대응 위한 여유금)

    # === Scheduler ===
    SCHEDULER_ENABLED: bool = True
    AI_DYNAMIC_RESCAN_ENABLED: bool = True
    AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION: int = 3
    AI_DYNAMIC_RESCAN_ALLOWED_INTERVALS: str = "15,30,45,60,90,120"
    AI_DYNAMIC_RESCAN_MIN_INTERVAL_MINUTES: int = 15
    AI_DYNAMIC_RESCAN_MAX_INTERVAL_MINUTES: int = 120
    AI_DYNAMIC_RESCAN_DEFAULT_INTERVAL_MINUTES: int = 60

    @property
    def async_database_url(self) -> str:
        """Sync URL에서 async 드라이버 URL을 자동 생성"""
        url = self.DATABASE_URL
        if url.startswith("sqlite:///"):
            return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("mysql://"):
            return url.replace("mysql://", "mysql+aiomysql://", 1)
        return url

    @property
    def is_local(self) -> bool:
        return self.ENVIRONMENT == "local"

    @property
    def is_paper_trading(self) -> bool:
        return self.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL"

    @staticmethod
    def _parse_csv(raw_value: str, *, upper: bool = False) -> list[str]:
        """콤마 구분 문자열을 리스트로 변환"""
        items = [item.strip() for item in (raw_value or "").split(",")]
        filtered = [item for item in items if item]
        if upper:
            return [item.upper() for item in filtered]
        return filtered

    @property
    def enabled_markets_list(self) -> list[str]:
        """활성 시장 목록"""
        from trading.market_profile import expand_scan_markets

        items = self._parse_csv(self.ENABLED_MARKETS)
        return expand_scan_markets(items or [self.PRIMARY_MARKET])

    @property
    def enabled_market_groups(self) -> list[str]:
        """스케줄링용 시장 그룹 목록 (US → NASDAQ 대표, CRYPTO → BITHUMB)"""
        from trading.market_profile import is_crypto_market, is_us_market, normalize_market

        raw = self._parse_csv(self.ENABLED_MARKETS) or [self.PRIMARY_MARKET]
        # CRYPTO_ENABLED이면 CRYPTO도 시장 그룹에 포함
        if self.CRYPTO_ENABLED and not any(
            s.upper() in {"CRYPTO", "BITHUMB", "BTH", "COIN"} for s in raw
        ):
            raw.append(self.crypto_primary_market_code)
        seen: list[str] = []
        for m in raw:
            norm = normalize_market(m)
            if is_crypto_market(norm):
                if not any(is_crypto_market(s) for s in seen):
                    seen.append(norm)
            elif is_us_market(norm):
                if not any(is_us_market(s) for s in seen):
                    seen.append(norm)
            elif norm not in seen:
                seen.append(norm)
        return seen

    @property
    def has_stock_markets(self) -> bool:
        """KRX 또는 US 시장이 활성화되어 있는지 확인 (코인 전용 모드 판별)"""
        from trading.market_profile import is_crypto_market

        return any(not is_crypto_market(m) for m in self.enabled_market_groups)

    @property
    def has_crypto_markets(self) -> bool:
        """코인 시장이 활성화되어 있는지 확인"""
        from trading.market_profile import is_crypto_market

        return any(is_crypto_market(m) for m in self.enabled_market_groups)

    @property
    def primary_market_code(self) -> str:
        """대표 시장 코드"""
        from trading.market_profile import normalize_market

        return normalize_market(self.PRIMARY_MARKET)

    @property
    def crypto_primary_market_code(self) -> str:
        """코인 대표 시장 코드 (비어있거나 잘못되면 BITHUMB 고정)"""
        from trading.market_profile import is_crypto_market, normalize_market

        normalized = normalize_market(self.CRYPTO_PRIMARY_MARKET, default="BITHUMB")
        if is_crypto_market(normalized):
            return normalized

        logger.warning(
            "CRYPTO_PRIMARY_MARKET={}는 지원되지 않는 코인 시장 코드입니다. BITHUMB로 고정합니다.",
            self.CRYPTO_PRIMARY_MARKET,
        )
        return "BITHUMB"

    @property
    def crypto_timebox_hours(self) -> int:
        """코인 최대 보유시간 설정을 12h/24h로 정규화한다."""
        raw = int(self.CRYPTO_TIMEBOX_HOURS or 12)
        if raw in {12, 24}:
            return raw

        logger.warning(
            "CRYPTO_TIMEBOX_HOURS={}는 지원되지 않습니다. 12시간으로 고정합니다.",
            self.CRYPTO_TIMEBOX_HOURS,
        )
        return 12

    @property
    def crypto_trading_style_mode(self) -> str:
        """코인 매매 성향 모드를 정규화한다."""
        raw = (self.CRYPTO_TRADING_STYLE_MODE or "CONSERVATIVE").strip().upper()
        if raw in {"CONSERVATIVE", "AGGRESSIVE"}:
            return raw

        logger.warning(
            "CRYPTO_TRADING_STYLE_MODE={}는 지원되지 않습니다. CONSERVATIVE로 고정합니다.",
            self.CRYPTO_TRADING_STYLE_MODE,
        )
        return "CONSERVATIVE"

    @property
    def scan_markets(self) -> list[str]:
        """시장 스캔 대상 목록"""
        return self.scan_markets_for(self.primary_market_code)

    def scan_markets_for(self, market: str) -> list[str]:
        """지정 시장의 스캔 대상 목록"""
        from trading.market_profile import (
            expand_scan_markets, is_crypto_market, is_us_market, normalize_market,
        )

        m = normalize_market(market)
        if is_crypto_market(m):
            return [self.crypto_primary_market_code]
        if is_us_market(m):
            items = self._parse_csv(self.US_SCAN_MARKETS)
            return expand_scan_markets(items)
        return [m]

    def get_market_config(self, market: str) -> dict:
        """시장별 매수마감/강제청산 시간 설정 반환 (크립토는 24/7 → 제한 없음)"""
        from trading.market_profile import is_crypto_market, is_us_market, normalize_market

        norm = normalize_market(market)
        if is_crypto_market(norm):
            return {
                "buy_cutoff_hour": None,
                "buy_cutoff_minute": None,
                "force_liquidation_hour": None,
                "force_liquidation_minute": None,
            }
        if is_us_market(norm):
            return {
                "buy_cutoff_hour": self.US_BUY_CUTOFF_HOUR,
                "buy_cutoff_minute": self.US_BUY_CUTOFF_MINUTE,
                "force_liquidation_hour": self.US_FORCE_LIQUIDATION_HOUR,
                "force_liquidation_minute": self.US_FORCE_LIQUIDATION_MINUTE,
            }
        return {
            "buy_cutoff_hour": self.BUY_CUTOFF_HOUR,
            "buy_cutoff_minute": self.BUY_CUTOFF_MINUTE,
            "force_liquidation_hour": self.FORCE_LIQUIDATION_HOUR,
            "force_liquidation_minute": self.FORCE_LIQUIDATION_MINUTE,
        }

    def market_has_buy_cutoff(self, market: str) -> bool:
        """시장에 신규 매수 cutoff 개념이 존재하는지 반환"""
        mkt_cfg = self.get_market_config(market)
        return (
            mkt_cfg["buy_cutoff_hour"] is not None
            and mkt_cfg["buy_cutoff_minute"] is not None
        )

    def market_has_force_liquidation(self, market: str) -> bool:
        """시장에 강제 청산 cutoff 개념이 존재하는지 반환"""
        mkt_cfg = self.get_market_config(market)
        return (
            mkt_cfg["force_liquidation_hour"] is not None
            and mkt_cfg["force_liquidation_minute"] is not None
        )

    def is_trading_enabled_for_market(self, market: str) -> bool:
        """시장별 실주문 허용 여부 반환"""
        from trading.market_profile import is_crypto_market, is_us_market, normalize_market

        norm = normalize_market(market)
        if is_crypto_market(norm):
            return bool(self.CRYPTO_TRADING_ENABLED)
        if is_us_market(norm):
            return bool(self.TRADING_ENABLED and self.US_TRADING_ENABLED)
        return bool(self.TRADING_ENABLED)

    def autonomy_mode_for_market(self, market: str) -> str:
        """시장별 autonomy mode 반환"""
        from trading.market_profile import is_crypto_market, normalize_market

        norm = normalize_market(market)
        if is_crypto_market(norm):
            return (self.CRYPTO_AUTONOMY_MODE or self.AUTONOMY_MODE or "SEMI_AUTO").upper()
        return (self.AUTONOMY_MODE or "SEMI_AUTO").upper()

    def risk_appetite_for_market(self, market: str) -> str:
        """시장별 AI risk tuning 성향 반환"""
        from trading.market_profile import is_crypto_market, normalize_market

        norm = normalize_market(market)
        if is_crypto_market(norm):
            return self.crypto_trading_style_mode
        return (self.RISK_APPETITE or "MODERATE").upper()

    def recommendation_expire_minutes_for_market(self, market: str) -> int:
        """시장별 추천 만료 시간 반환"""
        from trading.market_profile import is_crypto_market, normalize_market

        norm = normalize_market(market)
        if is_crypto_market(norm):
            return max(
                1,
                int(self.CRYPTO_RECOMMENDATION_EXPIRE_MIN or self.RECOMMENDATION_EXPIRE_MIN or 30),
            )
        return max(1, int(self.RECOMMENDATION_EXPIRE_MIN or 60))

    def should_use_adaptive_rescan(self, market: str) -> bool:
        """시장별 adaptive rescan 사용 여부"""
        from trading.market_profile import is_crypto_market, normalize_market

        norm = normalize_market(market)
        return bool(self.AI_DYNAMIC_RESCAN_ENABLED and not is_crypto_market(norm))

    @property
    def crypto_watchlist_symbols(self) -> list[str]:
        """크립토 스캔용 감시 코인 목록"""
        return self._parse_csv(self.CRYPTO_WATCHLIST_SYMBOLS, upper=True)[: self.CRYPTO_SCAN_LIMIT]

    @property
    def crypto_llm_provider(self) -> LLMProvider:
        """코인 전용 LLM provider (비어있으면 주식 설정 사용)"""
        raw = (self.CRYPTO_LLM_PROVIDER or "").strip().upper()
        if not raw:
            return self.llm_provider
        try:
            return LLMProvider(raw)
        except ValueError:
            logger.warning("알 수 없는 CRYPTO_LLM_PROVIDER={} → 주식 설정 사용", raw)
            return self.llm_provider

    def llm_provider_for_scope(self, scope: str | None = None) -> LLMProvider:
        """scope 기준 provider 반환"""
        from trading.market_profile import normalize_market_scope

        if scope and normalize_market_scope(scope) == "CRYPTO":
            return self.crypto_llm_provider
        return self.llm_provider

    def get_crypto_llm_model(
        self,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> str:
        """크립토 전용 LLM 모델 반환 (SCAN/ANALYSIS 프로필 분리, 비어있으면 주식 설정)"""
        return self.get_crypto_llm_model_for_provider(
            self.crypto_llm_provider,
            tier,
            profile,
        )

    def get_crypto_llm_model_for_provider(
        self,
        provider: LLMProvider,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> str:
        """크립토 scope에서 특정 provider가 사용할 모델 반환"""
        if provider == LLMProvider.CLAUDE_CODE:
            if tier == LLMTier.TIER1:
                if profile == Tier1Profile.SCAN:
                    return (
                        self.CRYPTO_LLM_MODEL_TIER1_SCAN
                        or self.CRYPTO_LLM_MODEL_TIER1_ANALYSIS
                        or self.get_llm_model(provider, tier)
                    )
                return (
                    self.CRYPTO_LLM_MODEL_TIER1_ANALYSIS
                    or self.CRYPTO_LLM_MODEL_TIER1_SCAN
                    or self.get_llm_model(provider, tier)
                )
            return self.CRYPTO_LLM_MODEL_TIER2 or self.get_llm_model(provider, tier)

        # Codex: Claude와 동일하게 tier/profile별 모델 분리
        if tier == LLMTier.TIER1:
            if profile == Tier1Profile.SCAN:
                return (
                    self.CRYPTO_CODEX_MODEL_TIER1_SCAN
                    or self.CRYPTO_CODEX_MODEL_TIER1_ANALYSIS
                    or self.CRYPTO_CODEX_MODEL
                    or self.get_llm_model(provider, tier)
                )
            return (
                self.CRYPTO_CODEX_MODEL_TIER1_ANALYSIS
                or self.CRYPTO_CODEX_MODEL_TIER1_SCAN
                or self.CRYPTO_CODEX_MODEL
                or self.get_llm_model(provider, tier)
            )
        return (
            self.CRYPTO_CODEX_MODEL_TIER2
            or self.CRYPTO_CODEX_MODEL
            or self.get_llm_model(provider, tier)
        )

    def get_crypto_llm_reasoning_effort(
        self,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> str | None:
        """코인 전용 추론 강도 (Claude/Codex 모두 지원, 비어있으면 주식 설정)"""
        return self.get_crypto_llm_reasoning_effort_for_provider(
            self.crypto_llm_provider,
            tier,
            profile,
        )

    def get_crypto_llm_reasoning_effort_for_provider(
        self,
        provider: LLMProvider,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> str | None:
        """크립토 scope에서 특정 provider가 사용할 추론 강도 반환"""
        if provider == LLMProvider.CLAUDE_CODE:
            if tier == LLMTier.TIER1:
                raw = (
                    self.CRYPTO_CLAUDE_EFFORT_TIER1_SCAN
                    if profile == Tier1Profile.SCAN
                    else self.CRYPTO_CLAUDE_EFFORT_TIER1_ANALYSIS
                )
                default = "medium"
            else:
                raw = self.CRYPTO_CLAUDE_EFFORT_TIER2
                default = "high"
            normalized = (raw or "").strip().lower()
            return normalized if normalized else default

        if tier == LLMTier.TIER1:
            raw = (
                self.CRYPTO_CODEX_REASONING_EFFORT_TIER1_SCAN
                if profile == Tier1Profile.SCAN
                else self.CRYPTO_CODEX_REASONING_EFFORT_TIER1_ANALYSIS
            )
        else:
            raw = self.CRYPTO_CODEX_REASONING_EFFORT_TIER2

        normalized = (raw or "").strip().lower()
        if normalized and normalized in VALID_CODEX_REASONING_EFFORTS:
            return normalized
        return self.get_llm_reasoning_effort(provider, tier, profile)

    def get_report_llm_reasoning_effort_for_provider(
        self,
        provider: LLMProvider,
    ) -> str | None:
        """장마감/회고 리포트 생성용 추론 강도 반환"""
        if provider == LLMProvider.CLAUDE_CODE:
            normalized = (self.CLAUDE_CODE_EFFORT_REPORT or "").strip().lower()
            return normalized if normalized else "high"

        normalized, is_valid = self._parse_codex_reasoning_effort(
            self.CODEX_REASONING_EFFORT_REPORT
        )
        if normalized and is_valid:
            return normalized
        return "xhigh"

    def get_crypto_report_llm_reasoning_effort_for_provider(
        self,
        provider: LLMProvider,
    ) -> str | None:
        """코인 체크포인트 리포트 생성용 추론 강도 반환"""
        if provider == LLMProvider.CLAUDE_CODE:
            normalized = (self.CRYPTO_CLAUDE_EFFORT_REPORT or "").strip().lower()
            if normalized:
                return normalized
            return self.get_report_llm_reasoning_effort_for_provider(provider)

        normalized, is_valid = self._parse_codex_reasoning_effort(
            self.CRYPTO_CODEX_REASONING_EFFORT_REPORT
        )
        if normalized and is_valid:
            return normalized
        return self.get_report_llm_reasoning_effort_for_provider(provider)

    def get_llm_model_for_scope(
        self,
        scope: str | None,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> str:
        """scope 기준 모델명 반환"""
        return self.get_llm_model_for_scope_provider(
            scope,
            self.llm_provider_for_scope(scope),
            tier,
            profile,
        )

    def get_llm_model_for_scope_provider(
        self,
        scope: str | None,
        provider: LLMProvider,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> str:
        """scope와 provider 기준 모델명 반환"""
        from trading.market_profile import normalize_market_scope

        if scope and normalize_market_scope(scope) == "CRYPTO":
            return self.get_crypto_llm_model_for_provider(provider, tier, profile)
        return self.get_llm_model(provider, tier)

    def get_llm_reasoning_effort_for_scope(
        self,
        scope: str | None,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
        phase: str | None = None,
    ) -> str | None:
        """scope 기준 reasoning effort 반환"""
        return self.get_llm_reasoning_effort_for_scope_provider(
            scope,
            self.llm_provider_for_scope(scope),
            tier,
            profile,
            phase,
        )

    def get_llm_reasoning_effort_for_scope_provider(
        self,
        scope: str | None,
        provider: LLMProvider,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
        phase: str | None = None,
    ) -> str | None:
        """scope와 provider 기준 reasoning effort 반환"""
        from trading.market_profile import normalize_market_scope

        if self._is_report_phase(phase):
            if scope and normalize_market_scope(scope) == "CRYPTO":
                return self.get_crypto_report_llm_reasoning_effort_for_provider(provider)
            return self.get_report_llm_reasoning_effort_for_provider(provider)

        if scope and normalize_market_scope(scope) == "CRYPTO":
            return self.get_crypto_llm_reasoning_effort_for_provider(provider, tier, profile)
        return self.get_llm_reasoning_effort(provider, tier, profile)

    @property
    def us_watchlist_symbols(self) -> list[str]:
        """미국장 스캔용 우선 감시 종목"""
        return self._parse_csv(self.US_WATCHLIST_SYMBOLS, upper=True)[: self.US_SCAN_LIMIT]

    @property
    def us_leverage_allowed_sessions_list(self) -> list[str]:
        """미국 레버리지 상품 허용 세션 목록"""
        return self._parse_csv(self.US_LEVERAGE_ALLOWED_SESSIONS, upper=True)

    @property
    def us_leverage_allowed_strategies_list(self) -> list[str]:
        """미국 레버리지 상품 허용 전략 목록"""
        return self._parse_csv(self.US_LEVERAGE_ALLOWED_STRATEGIES, upper=True)

    @property
    def us_leverage_allowlist_symbols(self) -> list[str]:
        """미국 레버리지 상품 명시 허용 티커"""
        return self._parse_csv(self.US_LEVERAGE_ALLOWLIST, upper=True)

    @property
    def us_leverage_denylist_symbols(self) -> list[str]:
        """미국 레버리지 상품 명시 차단 티커"""
        return self._parse_csv(self.US_LEVERAGE_DENYLIST, upper=True)

    @property
    def us_leverage_keywords_list(self) -> list[str]:
        """미국 레버리지 상품명 판별 키워드"""
        return self._parse_csv(self.US_LEVERAGE_KEYWORDS, upper=True)

    @property
    def us_inverse_keywords_list(self) -> list[str]:
        """미국 인버스 상품명 판별 키워드"""
        return self._parse_csv(self.US_INVERSE_KEYWORDS, upper=True)

    @property
    def ai_dynamic_rescan_allowed_intervals_list(self) -> list[int]:
        """AI 동적 재스캔 허용 간격 목록"""
        parsed: list[int] = []
        for raw in self._parse_csv(self.AI_DYNAMIC_RESCAN_ALLOWED_INTERVALS):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                parsed.append(value)

        minimum = max(1, int(self.AI_DYNAMIC_RESCAN_MIN_INTERVAL_MINUTES or 15))
        maximum = max(minimum, int(self.AI_DYNAMIC_RESCAN_MAX_INTERVAL_MINUTES or 120))
        intervals = sorted({
            min(maximum, max(minimum, value))
            for value in parsed
        })
        if intervals:
            return intervals
        return [15, 30, 45, 60, 90, 120]

    @property
    def llm_provider(self) -> LLMProvider:
        """선택된 LLM provider 반환"""
        raw_value = (self.LLM_PROVIDER or LLMProvider.CLAUDE_CODE.value).upper()
        try:
            return LLMProvider(raw_value)
        except ValueError:
            logger.warning(
                "알 수 없는 LLM_PROVIDER={} → CLAUDE_CODE로 대체",
                self.LLM_PROVIDER,
            )
            return LLMProvider.CLAUDE_CODE

    def get_llm_model(self, provider: LLMProvider, tier: LLMTier) -> str:
        """provider/tier 조합의 모델명 반환"""
        if provider == LLMProvider.CLAUDE_CODE:
            if tier == LLMTier.TIER1:
                return self.CLAUDE_CODE_MODEL_TIER1 or self.CLAUDE_CODE_MODEL or "haiku"
            return self.CLAUDE_CODE_MODEL_TIER2 or self.CLAUDE_CODE_MODEL or "sonnet"

        if tier == LLMTier.TIER1:
            return self.CODEX_MODEL_TIER1 or self.CODEX_MODEL or "gpt-5.4"
        return self.CODEX_MODEL_TIER2 or self.CODEX_MODEL or "gpt-5.4"

    def get_llm_reasoning_effort(
        self,
        provider: LLMProvider,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> str | None:
        """provider/tier 조합의 reasoning effort 반환"""
        if provider == LLMProvider.CLAUDE_CODE:
            return "medium" if tier == LLMTier.TIER1 else "high"

        for field_name, raw_value in self._get_codex_reasoning_effort_candidates(
            tier,
            profile,
        ):
            normalized, is_valid = self._parse_codex_reasoning_effort(raw_value)
            if normalized and is_valid:
                return normalized
        return self._get_default_codex_reasoning_effort(tier, profile)

    def get_llm_cli_path(self, provider: LLMProvider) -> str | None:
        """provider별 CLI 경로 탐색"""
        if provider == LLMProvider.CLAUDE_CODE:
            return self._find_claude_path()
        return self._find_codex_path()

    def validate_on_startup(self) -> None:
        """시작 시 필수 설정 검증 — 누락된 키에 대해 경고 로그"""
        provider = self.llm_provider
        cli_path = self.get_llm_cli_path(provider)
        if cli_path:
            logger.info("{} CLI 감지: {}", provider.value, cli_path)
        elif provider == LLMProvider.CLAUDE_CODE:
            logger.warning(
                "Claude Code CLI를 찾을 수 없음. "
                "CLAUDE_CODE_PATH를 설정하거나 claude CLI를 설치하세요."
            )
        else:
            logger.warning(
                "Codex CLI를 찾을 수 없음. "
                "CODEX_CLI_PATH를 설정하거나 codex CLI를 설치하세요."
            )

        self._validate_codex_reasoning_efforts()

        if self.has_stock_markets and not self.KIS_APP_KEY and not self.KIS_PAPER_APP_KEY:
            logger.warning(
                "KIS API 키 미설정: KIS_APP_KEY, KIS_PAPER_APP_KEY 모두 비어있음. "
                "실매매/모의투자 모두 불가합니다."
            )

        if not self.TRADING_ENABLED:
            logger.info("TRADING_ENABLED=false: 매매 기능이 비활성화 상태입니다.")

        if self.BASE_CURRENCY.upper() != "KRW":
            logger.warning(
                "BASE_CURRENCY={}는 아직 부분 지원입니다. "
                "현재 리스크 관리는 KRW 기준에 맞춰져 있습니다.",
                self.BASE_CURRENCY,
            )

    def _get_codex_reasoning_effort_candidates(
        self,
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> list[tuple[str, str]]:
        """Codex reasoning effort 우선순위 후보 반환"""
        if tier == LLMTier.TIER1:
            profile_field = (
                "CODEX_REASONING_EFFORT_TIER1_SCAN"
                if profile == Tier1Profile.SCAN
                else "CODEX_REASONING_EFFORT_TIER1_ANALYSIS"
            )
            return [
                (profile_field, getattr(self, profile_field)),
                ("CODEX_REASONING_EFFORT_TIER1", self.CODEX_REASONING_EFFORT_TIER1),
                ("CODEX_REASONING_EFFORT", self.CODEX_REASONING_EFFORT),
            ]
        return [
            ("CODEX_REASONING_EFFORT_TIER2", self.CODEX_REASONING_EFFORT_TIER2),
            ("CODEX_REASONING_EFFORT", self.CODEX_REASONING_EFFORT),
        ]

    @staticmethod
    def _get_default_codex_reasoning_effort(
        tier: LLMTier,
        profile: Tier1Profile | None = None,
    ) -> str:
        """Codex reasoning effort 기본값"""
        if tier == LLMTier.TIER2:
            return "xhigh"
        if profile == Tier1Profile.SCAN:
            return "low"
        return "medium"

    @staticmethod
    def _parse_codex_reasoning_effort(raw_value: str) -> tuple[str | None, bool]:
        """Codex reasoning effort 정규화 및 유효성 판정"""
        normalized = (raw_value or "").strip().lower()
        if not normalized:
            return None, True
        return normalized, normalized in VALID_CODEX_REASONING_EFFORTS

    @staticmethod
    def _is_report_phase(phase: str | None) -> bool:
        """리포트/회고 생성 phase 여부"""
        normalized = (phase or "").strip().lower()
        return normalized in REPORT_LLM_PHASES

    def _validate_codex_reasoning_efforts(self) -> None:
        """Codex reasoning effort 설정 유효성 검증"""
        for field_name in (
            "CODEX_REASONING_EFFORT",
            "CODEX_REASONING_EFFORT_REPORT",
            "CODEX_REASONING_EFFORT_TIER1",
            "CODEX_REASONING_EFFORT_TIER1_SCAN",
            "CODEX_REASONING_EFFORT_TIER1_ANALYSIS",
            "CODEX_REASONING_EFFORT_TIER2",
            "CRYPTO_CODEX_REASONING_EFFORT_REPORT",
            "CRYPTO_CODEX_REASONING_EFFORT_TIER1_SCAN",
            "CRYPTO_CODEX_REASONING_EFFORT_TIER1_ANALYSIS",
            "CRYPTO_CODEX_REASONING_EFFORT_TIER2",
        ):
            raw_value = getattr(self, field_name)
            normalized, is_valid = self._parse_codex_reasoning_effort(raw_value)
            if normalized and not is_valid:
                logger.warning(
                    "잘못된 {}={} → 무시합니다. 지원값: {}",
                    field_name,
                    raw_value,
                    ", ".join(VALID_CODEX_REASONING_EFFORTS),
                )

    @staticmethod
    def _find_executable_path(
        configured_path: str,
        command: str,
        candidates: list[str],
    ) -> str | None:
        """설정값, PATH, 후보 경로 순으로 실행파일 탐색"""
        if configured_path:
            return configured_path

        path = shutil.which(command)
        if path:
            return path

        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _find_claude_path(self) -> str | None:
        """claude CLI 경로 탐색 (설정값 → PATH → 일반적 설치 경로)"""
        return self._find_executable_path(
            configured_path=self.CLAUDE_CODE_PATH,
            command="claude",
            candidates=[
                "/opt/homebrew/bin/claude",
                "/usr/local/bin/claude",
                os.path.expanduser("~/.local/bin/claude"),
                os.path.expanduser("~/.npm-global/bin/claude"),
            ],
        )

    def _find_codex_path(self) -> str | None:
        """codex CLI 경로 탐색 (설정값 → PATH → 일반적 설치 경로)"""
        return self._find_executable_path(
            configured_path=self.CODEX_CLI_PATH,
            command="codex",
            candidates=[
                "/opt/homebrew/bin/codex",
                "/usr/local/bin/codex",
                os.path.expanduser("~/.local/bin/codex"),
                os.path.expanduser("~/.npm-global/bin/codex"),
            ],
        )


settings = Settings()
