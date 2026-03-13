# MOMO Trading — Codex 지침

AI 기반 한국/미국 주식 자동매매 시스템. LLM 다단계 분석(스크리닝 → 기술적 분석 → 최종 검토)과 실시간 WebSocket 이벤트 감지를 결합하여 장중 자율 매매부터 스윙 오버나이트 보유까지 지원한다.

## 증권사 API 참조

KIS Open API 구현, 디버깅, 새 API 연동 시 반드시 `kis-api-ref` 스킬을 사용하여 공식 샘플코드를 참조할 것.

- 공식 레포: https://github.com/koreainvestment/open-trading-api
- 스킬: `.codex/skills/kis-api-ref/`
- LLM용 샘플은 `examples_llm/` 폴더에, 사용자용 통합 예제는 `examples_user/` 폴더에 위치

## 기술 스택

| 구분 | 기술 |
|------|------|
| Web Framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) |
| DB Migration | Alembic |
| Validation | Pydantic v2 |
| 기술적 분석 | pandas + pandas-ta |
| 실시간 통신 | WebSocket (KIS), SSE (Admin) |
| 스케줄러 | APScheduler (KRX 장 시간 기준 cron) |
| 증권사 API | KIS REST API 직접 호출 |
| LLM | Claude Code CLI / Codex CLI (2-Tier) |
| 로깅 | loguru |
| 테스트 | pytest + pytest-asyncio |

## 아키텍처

### 요청 흐름
```
API Routes → Services → Repositories(AsyncBaseRepository[T]) → SQLAlchemy Models
             ↕ schemas/(Pydantic)
             ↕ dependencies/(Annotated[Type, Depends(factory)])
```

### 에이전트 파이프라인 (장중)
```
Scheduler (APScheduler, KST/EST cron)
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
            └─ DecisionMaker.execute() → 자동주문 또는 추천 생성
```

### 핵심 컴포넌트

| 컴포넌트 | 파일 | 핵심 메서드 |
|----------|------|------------|
| TradingAgent | `agent/trading_agent.py` | `run_cycle()`, `_run_trading_cycle()`, `_analyze_and_trade()` |
| MarketScanner | `agent/market_scanner.py` | `scan()` |
| DecisionMaker | `agent/decision_maker.py` | `execute()`, `_execute_autonomous()` |
| TradingScheduler | `scheduler/scheduler.py` | `start()`, `_setup_jobs()`, `_market_open_scan()` |
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

### 피드백 학습

장후 리뷰 → 성공/실패 패턴 추출 → 트레이딩 규칙 자동 생성 → 다음 날 적용

## 분리된 장 구조

- 운영 장은 `KRX` 와 `US` 두 runtime scope로 분리한다.
- `market_scope` 는 스케줄, 리포트, 리스크, LLM 세션, 실시간 구독을 나누는 기준이다.
- 실제 주문/시세용 `market` 코드는 `KRX`, `NASDAQ`, `NYSE`, `AMEX` 같은 거래소 코드를 그대로 유지한다.
- 미국장은 거래소 단위로 주문하지만, 장중 런타임과 장후 리뷰는 `US` scope로 묶어 처리한다.

## 코드 컨벤션

- Python 3.11+, async/await 기반
- snake_case (변수/함수/모듈), PascalCase (클래스), UPPER_SNAKE_CASE (상수)
- Type hints 필수 (Pydantic v2 모델, Protocol 기반 인터페이스)
- loguru 로깅 (logger.info/warning/error)
- 테스트: pytest + pytest-asyncio, `tests/` 디렉토리

## 주요 디렉토리

```
agent/          — AI 트레이딩 에이전트 (스캔, 분석, 의사결정)
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
