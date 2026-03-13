from __future__ import annotations

import os
import shutil

from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading.enums import LLMProvider, LLMTier

VALID_CODEX_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


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

    # === AI / LLM ===
    LLM_PROVIDER: str = LLMProvider.CLAUDE_CODE.value

    # Claude Code CLI
    CLAUDE_CODE_MODEL: str = "sonnet"  # 기본 모델 (Tier별 미지정 시 사용)
    CLAUDE_CODE_MODEL_TIER1: str = "haiku"  # Tier1 (스캔/분석): 빠른 모델
    CLAUDE_CODE_MODEL_TIER2: str = "sonnet"  # Tier2 (최종 검토): 정확한 모델
    CLAUDE_CODE_PATH: str = ""  # 비어있으면 자동 탐색 (예: /opt/homebrew/bin/claude)

    # Codex CLI
    CODEX_MODEL: str = "gpt-5.4"
    CODEX_MODEL_TIER1: str = ""
    CODEX_MODEL_TIER2: str = ""
    CODEX_REASONING_EFFORT: str = ""
    CODEX_REASONING_EFFORT_TIER1: str = ""
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
        """스케줄링용 시장 그룹 목록 (US → NASDAQ 대표)"""
        from trading.market_profile import is_us_market, normalize_market

        raw = self._parse_csv(self.ENABLED_MARKETS) or [self.PRIMARY_MARKET]
        seen: list[str] = []
        for m in raw:
            norm = normalize_market(m)
            # US 계열은 대표 마켓(NASDAQ)으로 통합
            if is_us_market(norm):
                if not any(is_us_market(s) for s in seen):
                    seen.append(norm)
            elif norm not in seen:
                seen.append(norm)
        return seen

    @property
    def primary_market_code(self) -> str:
        """대표 시장 코드"""
        from trading.market_profile import normalize_market

        return normalize_market(self.PRIMARY_MARKET)

    @property
    def scan_markets(self) -> list[str]:
        """시장 스캔 대상 목록"""
        return self.scan_markets_for(self.primary_market_code)

    def scan_markets_for(self, market: str) -> list[str]:
        """지정 시장의 스캔 대상 목록"""
        from trading.market_profile import expand_scan_markets, is_us_market, normalize_market

        m = normalize_market(market)
        if is_us_market(m):
            items = self._parse_csv(self.US_SCAN_MARKETS)
            return expand_scan_markets(items)
        return [m]

    def get_market_config(self, market: str) -> dict:
        """시장별 매수마감/강제청산 시간 설정 반환"""
        from trading.market_profile import is_us_market, normalize_market

        if is_us_market(normalize_market(market)):
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
    ) -> str | None:
        """provider/tier 조합의 reasoning effort 반환"""
        if provider == LLMProvider.CLAUDE_CODE:
            return "medium" if tier == LLMTier.TIER1 else "high"

        for field_name, raw_value in self._get_codex_reasoning_effort_candidates(tier):
            normalized, is_valid = self._parse_codex_reasoning_effort(raw_value)
            if normalized and is_valid:
                return normalized
        return None

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

        if not self.KIS_APP_KEY and not self.KIS_PAPER_APP_KEY:
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
    ) -> list[tuple[str, str]]:
        """Codex reasoning effort 우선순위 후보 반환"""
        if tier == LLMTier.TIER1:
            tier_field = "CODEX_REASONING_EFFORT_TIER1"
        else:
            tier_field = "CODEX_REASONING_EFFORT_TIER2"
        return [
            (tier_field, getattr(self, tier_field)),
            ("CODEX_REASONING_EFFORT", self.CODEX_REASONING_EFFORT),
        ]

    @staticmethod
    def _parse_codex_reasoning_effort(raw_value: str) -> tuple[str | None, bool]:
        """Codex reasoning effort 정규화 및 유효성 판정"""
        normalized = (raw_value or "").strip().lower()
        if not normalized:
            return None, True
        return normalized, normalized in VALID_CODEX_REASONING_EFFORTS

    def _validate_codex_reasoning_efforts(self) -> None:
        """Codex reasoning effort 설정 유효성 검증"""
        for field_name in (
            "CODEX_REASONING_EFFORT",
            "CODEX_REASONING_EFFORT_TIER1",
            "CODEX_REASONING_EFFORT_TIER2",
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
