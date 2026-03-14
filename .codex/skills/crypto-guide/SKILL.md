---
name: crypto-guide
description: 빗썸 암호화폐 거래 시스템 구조 가이드. 코인 관련 개발, 디버깅, 새 기능 추가, BrokerClient Protocol 구현, CRYPTO scope 작업 시 사용.
---

# 코인(빗썸) 시스템 가이드

코인 관련 개발·디버깅·새 기능 추가 시 시스템 구조를 참조한다.

**도메인 구조 다이어그램:** `docs/crypto-architecture.md` (Mermaid 6종)

## 코인 도메인 구조

### Market Scope 분리

| Scope | 시장 | 브로커 | 24/7 | 통화 |
|-------|------|--------|------|------|
| `KRX` | KOSPI, KOSDAQ | KIS (MCP + REST) | N | KRW |
| `US` | NASDAQ, NYSE, AMEX | KIS (REST) | N | USD |
| `CRYPTO` | BITHUMB | Bithumb REST API | **Y** | KRW |

### Protocol 인터페이스 계층

| Protocol | 파일 | KIS 구현체 | Bithumb 구현체 |
|----------|------|-----------|---------------|
| `BrokerClient` | `trading/broker_base.py` | `MCPClient` | `BithumbClient` |
| `MarketDataProvider` | `trading/broker_base.py` | `MCPClient` | `BithumbClient` |
| `RealtimeProvider` | `trading/broker_base.py` | `KISWebSocket` | `BithumbWebSocket` |
| `MarketScannerProtocol` | `agent/scanner_base.py` | `MarketScanner` | `CryptoScanner` |

## 핵심 파일 맵

### 인프라
| 파일 | 역할 |
|------|------|
| `trading/broker_base.py` | BrokerClient / MarketDataProvider / RealtimeProvider Protocol |
| `trading/bithumb_client.py` | 빗썸 REST 클라이언트 (JWT 인증, rate limiting) |
| `agent/scanner_base.py` | MarketScannerProtocol |
| `agent/crypto_scanner.py` | 코인 스캐너 (24h 거래대금/등락률 기반) |
| `analysis/llm/llm_factory.py` | 코인 scope LLM provider 선택 및 Codex 장애 시 Claude fallback |
| `trading/market_profile.py` | `is_crypto_market()`, `MARKET_SCOPE_CRYPTO` |
| `core/config.py` | `CRYPTO_*`, `BITHUMB_*` 환경변수 |
| `scheduler/market_calendar.py` | 24/7 세션 (`CRYPTO_ACTIVE`) |
| `strategy/risk_manager.py` | `CRYPTO_RR_FLOOR` |
| `analysis/llm/prompts/market_scan.py` | 크립토 스캔 프롬프트 |
| `analysis/llm/prompts/stock_analysis.py` | 크립토 Tier1 분석 프롬프트 |
| `analysis/llm/prompts/final_review.py` | 크립토 Tier2 리뷰 프롬프트 |

### 코인 전용 DB (주식과 완전 분리, 10개 테이블)
`models/coin_asset.py`, `coin_order.py`, `coin_broker_order.py`, `coin_trade_result.py`, `coin_holding.py`, `coin_activity_log.py`, `coin_daily_report.py`, `coin_trading_rule.py`, `coin_analysis_result.py`, `coin_recommendation.py`

### API & 운영
| 파일 | 역할 |
|------|------|
| `api/routes/admin_coin.py` | `/admin-coin` 전용 API (`/api/v1/admin-coin/*`, `/system/status`, `/agent/state`, 활동/LLM 상세) |
| `start-coin.sh` | 코인 전용 실행 (Docker 불필요) |
| `.env.example-coin` | 환경변수 예제 |

## 환경변수 (주식과 완전 독립)

`.env.example-coin` 참조. 주요 변수:

```
BITHUMB_API_KEY / BITHUMB_API_SECRET    # API 인증
CRYPTO_ENABLED                          # 코인 기능 on/off
CRYPTO_TRADING_ENABLED                  # 실제 주문 허용
CRYPTO_AUTONOMY_MODE                    # SEMI_AUTO / AUTONOMOUS
CRYPTO_SCAN_INTERVAL_HOURS              # 스캔 주기 (기본 4h)
CRYPTO_WATCHLIST_SYMBOLS                # 감시 코인 목록
CRYPTO_LLM_PROVIDER                     # CLAUDE_CODE / CODEX_CLI (비어있으면 LLM_PROVIDER)
CRYPTO_LLM_MODEL_TIER1_SCAN             # Claude 스캔 모델
CRYPTO_LLM_MODEL_TIER1_ANALYSIS         # Claude 분석 모델
CRYPTO_LLM_MODEL_TIER2                  # Claude 검토 모델
CRYPTO_CLAUDE_EFFORT_TIER1_SCAN         # Claude 스캔 effort
CRYPTO_CLAUDE_EFFORT_TIER1_ANALYSIS     # Claude 분석 effort
CRYPTO_CLAUDE_EFFORT_TIER2              # Claude 검토 effort
CRYPTO_CODEX_MODEL                      # Codex 모델
CRYPTO_CODEX_REASONING_EFFORT_TIER1_SCAN      # Codex 스캔 추론
CRYPTO_CODEX_REASONING_EFFORT_TIER1_ANALYSIS  # Codex 분석 추론
CRYPTO_CODEX_REASONING_EFFORT_TIER2           # Codex 검토 추론
CRYPTO_MAX_POSITION_PCT                 # 코인당 최대 비중
CRYPTO_MIN_CASH_RATIO                   # 최소 현금 비중
```

## 코인 구현 주의사항

- 빗썸 rate limit: 공식 Public **150**/s, Private **140**/s, 주문 **10**/s. 코드는 보수적 10/5 semaphore
- 소수점 수량: `OrderRequest.quantity = float` (주식은 정수만 전달)
- 캔들 정렬: newest-first → oldest-first 재정렬 (미국장과 동일 방어)
- JWT 인증: PyJWT HS256. `Authorization: Bearer {jwt}`. Payload: access_key, nonce(UUID), timestamp(ms), query_hash(SHA512)
- 시장 국면: BULL_RUN / BEAR_MARKET / CONSOLIDATION / ALTSEASON / ALT_SEASON (R:R floor에 양쪽 변형 매핑)
- R:R floor: BULL_RUN=2.0, BEAR_MARKET=1.5 (주식보다 넓게)
- Product Policy: 크립토는 항상 COMMON (Spot only)
- 24/7 스케줄: buy cutoff / 강제 청산 없음
- 코인 스캔은 `get_market_overview()` 1회 조회 결과를 `get_volume_rank(..., overview_data=...)` / `get_surge_data(..., overview_data=...)`에 재사용한다. overview가 깨지면 watchlist/보유 코인 현재가로 degraded 스캔을 시도한다.
- `BithumbClient.get_current_price()` / overview ticker 정규화에서 공통 `change` 필드는 숫자 변화량으로 맞춘다. 원본 방향 문자열은 `change_direction`, 부호 있는 변화량은 `signed_change_price`로 별도 보존한다.
- `BithumbClient._public_get()`는 HTTP status, content-type, body preview를 함께 로그에 남긴다. `Expecting value`만 보고 원인을 추측하지 말고 upstream HTML/빈 본문/5xx를 먼저 확인한다.
- 코인 scope에서 `CRYPTO_LLM_PROVIDER=CODEX_CLI`이고 Codex 인증/세션 갱신이 실패하면 `analysis/llm/llm_factory.py`가 `CLAUDE_CODE` fallback을 1회 시도한다.
- 주문 체결 확인: `POST /v1/orders` 접수 후 `GET /v1/order?uuid=...` 개별 조회로 상태를 추적한다
- 보유 코인 현재가: `_fetch_coin_prices()` 벌크 ticker 조회. 실패 시 avg_buy_price 폴백
- DB 완전 분리: 10개 `coin_*` 테이블, 주식 FK 없음
- 활동 로그 → CoinActivityLog, 추천 → CoinRecommendation (직접 쿼리)
- 코인 어드민 상태 패널은 `/api/v1/admin-coin/system/status` alias 필드(`trading_enabled`, `autonomy_mode`, `scheduler_running`, `agent_running`, `sse_clients`)와 `/api/v1/admin-coin/agent/state` 파이프라인 스냅샷을 함께 사용한다.
- 코인 활동 피드는 `CoinActivityLog.detail`와 `execution_time_ms`를 그대로 노출해 LLM system prompt / prompt / response를 인라인으로 점검한다.
- 코인 어드민 수동 스캔 버튼은 HTML inline handler를 쓰지 않고 단일 JS 바인딩만 사용한다. 버튼 상태는 `agent/state`를 기준으로 `요청 중 → 시작 대기 → 진행 중 → 완료/스킵` 흐름을 표시한다.
- SSE: `coin_sse_manager` 독립 인스턴스 (activity_logger 자동 분기)
- 주요 에러: 400 `invalid_parameter`/`invalid_price`, 401 `jwt_verification`/`expired_jwt`/`NotAllowIP`, 422 `order_not_ready`, 500 `server_error`
- Content-Type: `application/json; charset=utf-8`
- API 버전: v2.1.0

## 에이전트 파이프라인 (코인)

```
Scheduler (4h 고정 cron)
  └─ TradingAgent.run_cycle(market="BITHUMB")
       ├─ CryptoScanner.scan()             # 전체 코인 티커 → AI 선별
       │    └─ get_market_overview() 1회 조회 → volume/surge 파생 → LLM Tier1 스캔
       └─ 종목별 분석 (동일 파이프라인)
            ├─ BithumbClient 데이터 수집 (현재가, 일봉, 분봉)
            ├─ ChartAnalyzer (RSI, MACD, BB — 주식과 동일)
            ├─ Tier1 분석 (크립토 전용 프롬프트)
            ├─ Tier2 리뷰 (크립토 전용 프롬프트)
            ├─ RiskManager (CRYPTO_RR_FLOOR 적용)
            └─ DecisionMaker → 자동주문 또는 추천 생성
```

## 스킬 유지보수 규칙

**코인 관련 코드 변경 시 이 스킬도 반드시 함께 업데이트할 것.**

다음 변경이 발생하면 이 파일(`.codex/skills/crypto-guide/SKILL.md`)을 수정:
- 새 파일 추가/삭제 → 핵심 파일 맵 테이블 업데이트
- Protocol 메서드 시그니처 변경 → 인터페이스 계층 테이블 업데이트
- 환경변수 추가/제거 → 환경변수 섹션 업데이트
- 주의사항 추가 → 코인 구현 주의사항 섹션 업데이트

Claude Code 스킬(`.claude/commands/crypto-guide.md`)도 동일하게 동기화할 것.
