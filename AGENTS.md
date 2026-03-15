# MOMO Trading — Codex 지침

AI 기반 한국/미국 주식 자동매매 시스템. LLM 다단계 분석(스크리닝 → 기술적 분석 → 최종 검토)과 실시간 WebSocket 이벤트 감지를 결합하여 장중 자율 매매부터 스윙 오버나이트 보유까지 지원한다.

## 증권사 API 참조

KIS Open API 구현, 디버깅, 새 API 연동 시 반드시 `kis-api-ref` 스킬을 사용하여 공식 샘플코드를 참조할 것.

- 공식 레포: https://github.com/koreainvestment/open-trading-api
- 스킬: `.codex/skills/kis-api-ref/`
- LLM용 샘플은 `examples_llm/` 폴더에, 사용자용 통합 예제는 `examples_user/` 폴더에 위치

## 빗썸 API 참조

빗썸 API 구현, 디버깅, 새 API 연동 시 `bithumb-api-ref` 스킬을 사용하여 로컬 레퍼런스를 참조할 것.

- 스킬: `.codex/skills/bithumb-api-ref/`
- 로컬 레퍼런스: `docs/bithumb-api/` (PUBLIC 9개 + PRIVATE 25개 = 34개 엔드포인트)

## 코인(빗썸) 시스템 가이드

코인 관련 개발·디버깅 시 `crypto-guide` 스킬을 사용하여 코인 시스템 구조를 참조할 것.

- 스킬: `.codex/skills/crypto-guide/`
- 도메인 구조 다이어그램: `docs/crypto-architecture.md` (Mermaid 6종)
- 환경변수 예제: `.env.example-coin`
- 빗썸 API 레퍼런스: `docs/bithumb-api/` (34개 엔드포인트)
- **코인 관련 코드 변경(파일 추가/삭제, 인터페이스 변경, 환경변수 추가 등) 시 반드시 `crypto-guide` 스킬(`.codex/skills/crypto-guide/SKILL.md`)도 함께 업데이트할 것.**

## 기술 스택

| 구분 | 기술 |
|------|------|
| Web Framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) |
| DB Migration | Alembic |
| Validation | Pydantic v2 |
| 기술적 분석 | pandas + pandas-ta |
| 실시간 통신 | WebSocket (KIS), SSE (Admin) |
| 스케줄러 | APScheduler (장 시작/안전 앵커는 cron, 장중 재스캔은 adaptive one-shot) |
| 증권사 API | KIS REST API (주식) + Bithumb REST API (코인) |
| LLM | Claude Code CLI / Codex CLI (2-Tier) |
| 로깅 | loguru |
| 테스트 | pytest + pytest-asyncio + unittest |

## 아키텍처

### 요청 흐름
```
API Routes → Services → Repositories(AsyncBaseRepository[T]) → SQLAlchemy Models
             ↕ schemas/(Pydantic)
             ↕ dependencies/(Annotated[Type, Depends(factory)])
```

### 에이전트 파이프라인 (장중)
```
Scheduler (고정 오픈 스캔 + adaptive 장중 재스캔)
  └─ TradingAgent.run_cycle()
       ├─ MarketScanner.scan()          # 거래량순위 + 등락률 + AI 스크리닝 (Tier1)
       │    └─ 종목 선정 + market_regime 판단
       │
       └─ 종목별 병렬 분석 (semaphore=3)
            ├─ 상품 정책 체크 (레버리지/인버스 분류)
            ├─ MCP 데이터 수집 (현재가, 일봉 60일, 분봉 5분)
            ├─ 차트 분석 (RSI, MACD, Bollinger, SMA)
            ├─ Tier1 분석 (LLM) → BUY/SELL/HOLD 판단
            ├─ 트레이딩 규칙 게이트 (피드백 학습 기반)
            ├─ Tier2 최종 리뷰 (LLM) → 승인/거절
            ├─ 전략 평가 (StableShort / AggressiveShort)
            ├─ 리스크 체크 (일일한도, 단일주문한도, 최소현금비율)
            ├─ DecisionMaker.execute() → 자동주문 또는 추천 생성
            └─ schedule_hint 생성 → Scheduler가 다음 one-shot 재스캔 예약
```

### 핵심 컴포넌트

| 컴포넌트 | 파일 | 핵심 메서드 |
|----------|------|------------|
| TradingAgent | `agent/trading_agent/` | `run_cycle()`, `_run_trading_cycle()`, `_analyze_and_trade()` |
| MarketScanner | `agent/market_scanner.py` | `scan()` |
| DecisionMaker | `agent/decision_maker.py` | `execute()`, `_execute_autonomous()` |
| TradingScheduler | `scheduler/scheduler.py` | `start()`, `_market_open_scan()`, `_adaptive_rescan()`, `_schedule_next_adaptive_rescan()` |
| CryptoScanner | `agent/crypto_scanner.py` | `scan()` |
| BithumbClient | `trading/bithumb_client.py` | `get_current_price()`, `place_order()`, `get_market_overview()` |
| StableShortStrategy | `strategy/stable_short.py` | `evaluate()` |
| AggressiveShortStrategy | `strategy/aggressive_short.py` | `evaluate()` |
| RiskManager | `strategy/risk_manager.py` | `check()` |

### 2-Tier LLM 시스템

| Tier | 용도 | Claude Code | Codex CLI |
|------|------|-------------|-----------|
| Tier1 | 빠른 스캔/분석 | haiku + medium | CODEX_MODEL + CODEX_REASONING_EFFORT_TIER1 |
| Tier2 | 프리미엄 최종 리뷰 | sonnet + high | CODEX_MODEL + xhigh |

- 프로바이더 전환: `.env` 의 `LLM_PROVIDER` (CLAUDE_CODE / CODEX_CLI)
- 구현: `analysis/llm/claude_code_provider.py`, `analysis/llm/codex_cli_provider.py`
- 라우팅: `analysis/llm/llm_factory.py`
- 설정: `core/config.py` (CODEX_MODEL, CODEX_REASONING_EFFORT 등)

### 실시간 이벤트

WebSocket → EventDetector → EventBus:
- `VOLUME_SPIKE`, `PRICE_SURGE`, `PRICE_DROP` → 즉시 분석
- `STOP_LOSS_HIT`, `TAKE_PROFIT_HIT` → 즉시 매도
- 이 경로는 scheduled 재스캔 예산을 차감하지 않는다.

### 피드백 학습

장후 리뷰 → 성공/실패 패턴 추출 → 트레이딩 규칙 자동 생성 → 다음 날 적용

### 인터페이스 추상화

| Protocol | 파일 | 역할 |
|----------|------|------|
| `BrokerClient` | `trading/broker_base.py` | 시세 조회·주문·잔고 계약 |
| `MarketDataProvider` | `trading/broker_base.py` | 스캐닝용 벌크 데이터 |
| `RealtimeProvider` | `trading/broker_base.py` | WebSocket 실시간 시세 |
| `MarketScannerProtocol` | `agent/scanner_base.py` | 시장 스캔 결과 반환 |

## 코드 컨벤션

- Python 3.11+, async/await 기반
- snake_case (변수/함수/모듈), PascalCase (클래스), UPPER_SNAKE_CASE (상수)
- Type hints 필수 (Pydantic v2 모델, Protocol 기반 인터페이스)
- loguru 로깅 (logger.info/warning/error)
- 테스트: `pytest` + `pytest-asyncio` + `unittest` 혼용, `tests/` 디렉토리

## 공통 운영 지침

분리된 장 구조, 스케줄 구조, 미국장/코인 구현 회고, 테스트 실행 규칙은 아래 문서에 정의되어 있다. 반드시 참조할 것.

- `docs/shared-guidelines.md`

## 주요 디렉토리

```
agent/          — AI 트레이딩 에이전트 (스캔, 분석, 의사결정)
  trading_agent/ — Mixin 패키지 (StateMixin → PortfolioMixin → AnalysisMixin → CycleMixin → EventMixin)
analysis/       — LLM 프로바이더, 차트 분석
api/            — FastAPI 라우트
core/           — 설정 (Settings), 공통 유틸
models/         — SQLAlchemy 모델
schemas/        — Pydantic 스키마
services/       — 비즈니스 로직
repositories/   — DB 접근 계층
strategy/       — 매매 전략, 리스크 관리
trading/        — enum, 마켓 프로필, 상품 정책
scheduler/      — APScheduler 작업
realtime/       — WebSocket, 이벤트 버스
admin/          — 관리자 대시보드
backtesting/    — 백테스팅 엔진
```
