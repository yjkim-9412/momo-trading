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
| `trading/quantity_policy.py` | 코인/주식 수량 정책 분기 (코인 8자리 소수점, 주식 정수) |
| `trading/bithumb_websocket.py` | 빗썸 Public/Private WS 런타임 (`ticker`/`trade` + `myOrder`/`myAsset`) |
| `realtime/coin_stream_manager.py` | 코인 desired/active 구독 상태, admin stream status |
| `realtime/coin_monitor.py` | WS 수신 → `event_detector` / broker ledger / cache 동기화 |
| `agent/scanner_base.py` | MarketScannerProtocol |
| `agent/crypto_scanner.py` | 코인 스캐너 (24h 거래대금/등락률 기반, shared prompt 사용, canonical regime 정규화) |
| `analysis/llm/llm_factory.py` | 코인 scope LLM provider 선택 및 Codex 장애 시 Claude fallback |
| `analysis/feedback/trading_rules.py` | 코인 회고 기반 `coin_trading_rules` 생성/로드 |
| `services/coin_order_service.py` | 코인 수동 주문 preview/place/get/cancel helper + 브로커 에러 표준화 |
| `services/coin_daily_report_service.py` | 코인 체크포인트 회고 리포트 생성 |
| `repositories/coin_daily_report_repository.py` | 코인 체크포인트 리포트 latest/list/applied_cycle 조회 |
| `trading/market_profile.py` | `is_crypto_market()`, `MARKET_SCOPE_CRYPTO` |
| `trading/risk_policy.py` | 코인 canonical regime alias 정규화, shared RR floor 상수 |
| `core/config.py` | `CRYPTO_*`, `BITHUMB_*` 환경변수 |
| `scheduler/market_calendar.py` | 24/7 세션 (`CRYPTO_ACTIVE`) |
| `strategy/risk_manager.py` | `CRYPTO_RR_FLOOR` |
| `analysis/llm/prompts/market_scan.py` | 크립토 스캔 프롬프트 |
| `analysis/llm/prompts/stock_analysis.py` | 크립토 Tier1 분석 프롬프트 |
| `analysis/llm/prompts/final_review.py` | 크립토 Tier2 리뷰 프롬프트 |
| `analysis/llm/prompts/crypto_cycle_review.py` | 코인 체크포인트 회고 프롬프트 |
| `strategy/signal.py` | 코인 BUY `suggested_amount_krw` 포함 공통 시그널 계약 |
| `agent/decision_maker.py` | 코인 BUY 금액 주문 실행 (`price=None`, `quantity=<KRW amount>`) |

### 코인 전용 DB (주식과 완전 분리, 10개 테이블)
`models/coin_asset.py`, `coin_order.py`, `coin_broker_order.py`, `coin_trade_result.py`, `coin_holding.py`, `coin_activity_log.py`, `coin_daily_report.py`, `coin_trading_rule.py`, `coin_analysis_result.py`, `coin_recommendation.py`

### API & 운영
| 파일 | 역할 |
|------|------|
| `api/routes/admin_coin.py` | `/admin-coin` 전용 API (`/api/v1/admin-coin/*`, `/system/status`, `/agent/state`, 체크포인트 리포트/구간 활동 로그) |
| `schemas/coin_order_schema.py` | 코인 수동 주문 preview/place/get/cancel 요청·응답 스키마 |
| `schemas/coin_daily_report_schema.py` | 코인 체크포인트 리포트 응답 스키마 |
| `start-coin.sh` | 코인 전용 실행 (Docker 불필요) |
| `.env.example-coin` | 환경변수 예제 |
| `docs/bithumb-api/websocket/` | 빗썸 WebSocket API 문서 6개 (기본정보, ticker, trade, orderbook, myOrder, myAsset) |

## 환경변수 (주식과 완전 독립)

`.env.example-coin` 참조. 주요 변수:

```
BITHUMB_API_KEY / BITHUMB_API_SECRET    # API 인증
BITHUMB_WS_URL_PUBLIC / BITHUMB_WS_URL_PRIVATE  # 빗썸 Public/Private WS 엔드포인트
CRYPTO_ENABLED                          # 코인 기능 on/off
CRYPTO_PRIMARY_MARKET                   # 코인 기본 시장 코드, 비어있거나 잘못되면 BITHUMB 고정
CRYPTO_TRADING_ENABLED                  # 실제 주문 허용
CRYPTO_AUTONOMY_MODE                    # SEMI_AUTO / AUTONOMOUS
CRYPTO_SCAN_INTERVAL_HOURS              # 자동 스캔 + 선행 회고 체크포인트 주기 (기본 4h)
CRYPTO_WATCHLIST_SYMBOLS                # 감시 코인 목록
CRYPTO_DYNAMIC_DISCOVERY_ENABLED        # broad discovery universe + watchlist 시드 병합
CRYPTO_DISCOVERY_REFRESH_MINUTES        # discovery universe 새로고침 주기 (기본 360분)
CRYPTO_DISCOVERY_UNIVERSE_SIZE          # 유지할 상위 유동성 코인 수 (기본 30개)
CRYPTO_LLM_PROVIDER                     # CLAUDE_CODE / CODEX_CLI (비어있으면 LLM_PROVIDER)
CRYPTO_LLM_MODEL_TIER1_SCAN             # Claude 스캔 모델
CRYPTO_LLM_MODEL_TIER1_ANALYSIS         # Claude 분석 모델
CRYPTO_LLM_MODEL_TIER2                  # Claude 검토 모델
CRYPTO_CLAUDE_EFFORT_TIER1_SCAN         # Claude 스캔 effort
CRYPTO_CLAUDE_EFFORT_TIER1_ANALYSIS     # Claude 분석 effort
CRYPTO_CLAUDE_EFFORT_TIER2              # Claude 검토 effort
CRYPTO_CLAUDE_EFFORT_REPORT             # 코인 체크포인트 리포트 effort (예: max)
CRYPTO_CODEX_MODEL                      # Codex 모델
CRYPTO_CODEX_REASONING_EFFORT_REPORT    # 코인 체크포인트 리포트 추론 (기본 xhigh)
CRYPTO_CODEX_REASONING_EFFORT_TIER1_SCAN      # Codex 스캔 추론
CRYPTO_CODEX_REASONING_EFFORT_TIER1_ANALYSIS  # Codex 분석 추론
CRYPTO_CODEX_REASONING_EFFORT_TIER2           # Codex 검토 추론
CRYPTO_MAX_POSITION_PCT                 # 코인당 최대 비중
CRYPTO_MIN_CASH_RATIO                   # 최소 현금 비중
```

## 코인 구현 주의사항

- 빗썸 rate limit: 공식 Public **150**/s, Private **140**/s, 주문 **10**/s. 코드는 보수적 10/5 semaphore
- 빗썸 WebSocket: 최신 공식 엔드포인트는 `wss://ws-api.bithumb.com/websocket/v1` / `/private`
- Public WS는 `ticker`, `trade`, `orderbook`으로 가격/거래량/호가 이벤트를 만들고, Private WS는 `myOrder`, `myAsset`로 주문/자산 동기화를 수행한다
- 코인 미체결 주문의 source of truth 는 빗썸 REST placeholder 가 아니라 `coin_broker_orders` 원장이다. `AccountManager.get_pending_orders("BITHUMB")` 는 `SUBMITTED` / `OPEN` / `PARTIAL` 상태를 DB에서 읽어 코인 어드민 overview 로 반환한다.
- `myAsset` payload 는 총자산/손익을 직접 주지 않고 `balance` / `locked` 변경만 주므로, 코인 어드민의 `총 자산`, `KRW 현금`, `코인 평가`, `잠금 KRW` 는 WS 이벤트를 트리거로 `/api/v1/admin-coin/account/overview` 를 재조회해 확정한다.
- 소수점 수량: `OrderRequest.quantity = float` (주식은 정수만 전달)
- 코인 소수점 수량은 `trading/quantity_policy.py`를 기준으로 end-to-end 8자리 floor 정책을 유지한다. 포지션 스냅샷, 계좌 컨텍스트, 리스크 캡, Tier2 제안 수량, 빗썸 주문 payload는 코인만 fractional 을 보존하고 주식 API/스키마는 그대로 둔다.
- 코인 BUY 계약은 **수량 중심이 아니라 KRW 금액 중심**이다. `TradeSignal.suggested_amount_krw`가 authoritative 하고, `suggested_quantity`는 `entry_price` 기준 추정 수량이다.
- 코인 BUY 실행은 빗썸 `ord_type="price"` 시장가 매수로 통일한다. `DecisionMaker`/`BithumbClient`는 `place_order(symbol, side="BUY", quantity=<KRW amount>, price=None)` 형태를 사용한다.
- 빗썸 KRW 현물 BUY 최소 주문금액은 `5,000 KRW`이며, 코인 프롬프트와 `RiskManager`가 같은 기준을 공유한다.
- 캔들 정렬: newest-first → oldest-first 재정렬 (미국장과 동일 방어)
- JWT 인증: PyJWT HS256. `Authorization: Bearer {jwt}`. Payload: access_key, nonce(UUID), timestamp(ms), query_hash(SHA512)
- 시장 국면 canonical 값: `BULL_RUN` / `BEAR_MARKET` / `CONSOLIDATION` / `ALTSEASON` / `THEME`
- legacy alias는 `normalize_crypto_regime()`에서 `BEAR -> BEAR_MARKET`, `SIDEWAYS -> CONSOLIDATION`, `ALT_SEASON -> ALTSEASON`으로 정규화한다
- R:R floor: BULL_RUN=2.0, BEAR_MARKET=1.5 (주식보다 넓게)
- 코인 기본 시장 fallback은 `PRIMARY_MARKET`를 공유하지 않는다. 코인 경로는 `CRYPTO_PRIMARY_MARKET`를 우선 사용하고, 값이 비어 있거나 잘못되면 `BITHUMB`로 고정한다.
- 코인 프롬프트 스택(`market_scan.py`, `stock_analysis.py`, `final_review.py`)은 `빗썸 KRW 현물`, `수시간~2일`, `BUY/HOLD 전용`, `24h 거래대금 기반 유동성 확인`을 공통 계약으로 유지한다
- 코인 프롬프트 스택은 `BUY 판단은 KRW 투자금 기준`, `Tier2 BUY는 suggested_amount_krw 필수`, `suggested_quantity는 추정치` 계약을 함께 유지한다.
- 코인 체크포인트 회고 프롬프트는 `analysis/llm/prompts/crypto_cycle_review.py`를 사용하며, 표현도 `직전 구간` / `다음 코인 사이클` 기준으로 유지한다.
- 코인 체크포인트 회고 리포트는 LLM `phase="report"`를 사용한다. Codex는 기본 `xhigh`, Claude는 `CRYPTO_CLAUDE_EFFORT_REPORT=max` 같은 report 전용 override를 줄 수 있다.
- Product Policy: 크립토는 항상 COMMON (Spot only)
- 24/7 스케줄: buy cutoff / 강제 청산 없음
- scheduled 자동 코인 사이클은 `체크포인트 회고 리포트 생성 → refresh_runtime_trading_rules() → CryptoScanner.scan()` 순서로 실행된다.
- manual 코인 사이클은 새 리포트를 만들지 않는다. `_build_trading_context()`가 최신 `coin_daily_reports`를 읽어 `최근 회고 참고` 블록으로 주입한다.
- `coin_daily_reports`는 날짜 1건 unique 저장소가 아니라 체크포인트 이력 저장소다. `report_source`, `trigger_reason`, `applied_cycle_id`, `period_started_at`, `period_ended_at`를 함께 저장하고 `period_ended_at` 기준으로 latest/list를 본다.
- 코인 회고에서 파생된 규칙은 공유 `trading_rules`가 아니라 `coin_trading_rules`에 저장되고, `refresh_runtime_trading_rules()`가 현재 CRYPTO runtime에 즉시 적용한다.
- 코인 스캔은 `CRYPTO_DYNAMIC_DISCOVERY_ENABLED=true`일 때 `get_discovery_universe()`로 저빈도 broad refresh를 수행하고, `CRYPTO_DISCOVERY_UNIVERSE_SIZE` 상위 유동성 코인을 메모리 캐시에 유지한다.
- broad refresh 기본 주기는 `CRYPTO_DISCOVERY_REFRESH_MINUTES=360`이며, 평소 스캔은 cached universe 심볼과 `CRYPTO_WATCHLIST_SYMBOLS`만 `get_ticker_snapshots()`로 selective 조회한다.
- live broad refresh가 실패해도 stale discovery cache가 있으면 `DISCOVERY` 후보를 계속 유지하고, cache도 없을 때만 watchlist/보유 코인 현재가 기반 degraded 스캔으로 내려간다.
- 코인 후보/선정 결과에는 `scan_source`가 붙는다. 정상 경로는 `DISCOVERY` / `WATCHLIST`, degraded 경로는 `WATCHLIST_FALLBACK` / `HOLDING_FALLBACK`를 사용한다.
- `BithumbClient.get_current_price()` / overview ticker 정규화에서 공통 `change` 필드는 숫자 변화량으로 맞춘다. 원본 방향 문자열은 `change_direction`, 부호 있는 변화량은 `signed_change_price`로 별도 보존한다.
- `BithumbClient._public_get()`는 HTTP status, content-type, body preview를 함께 로그에 남긴다. `Expecting value`만 보고 원인을 추측하지 말고 upstream HTML/빈 본문/5xx를 먼저 확인한다.
- 코인 scope에서 `CRYPTO_LLM_PROVIDER=CODEX_CLI`이고 Codex 인증/세션 갱신이 실패하면 `analysis/llm/llm_factory.py`가 `CLAUDE_CODE` fallback을 1회 시도한다.
- 주문 체결 확인: `POST /v1/orders` 접수 후 `GET /v1/order?uuid=...` 개별 조회로 상태를 추적한다
- 보유 코인 현재가: `_fetch_coin_prices()` 벌크 ticker 조회. 실패 시 avg_buy_price 폴백
- `get_account_balance()` / `get_holdings()`는 `/v1/market/all`의 실제 `KRW-*` 마켓에 없는 자산을 비거래성 자산으로 간주해 제외한다. 마켓 카탈로그 조회가 깨지면 stale cache를 우선 쓰고, cache도 없으면 `avg_buy_price=0` 이고 현재가도 없는 자산만 degraded 규칙으로 제외한다.
- DB 완전 분리: 10개 `coin_*` 테이블, 주식 FK 없음
- 활동 로그 → CoinActivityLog, 추천 → CoinRecommendation (직접 쿼리)
- `coin_recommendations`는 `suggested_amount_krw`를 저장하고, `coin_broker_orders`는 `order_type`, `requested_amount_krw`를 저장한다.
- 코인 수동 주문 API는 `/api/v1/admin-coin/orders/preview`, `POST /orders`, `GET /orders/{order_id}`, `DELETE /orders/{order_id}` 로 노출된다.
- 수동 주문은 `CoinOrderService`가 브로커 계약을 계산한다. BUY는 `amount_krw`, SELL은 `quantity`가 authoritative 하며, preview 응답이 실제 빗썸 payload를 미리 보여준다.
- `coin_broker_orders`는 `source`를 저장한다. AI 자동주문은 `AI`, admin 수동 주문 API는 `MANUAL_API`를 사용한다.
- 빗썸 `ord_type="price"` 시장가 매수의 체결가는 `executed_funds / executed_volume`으로 평균 체결단가를 계산한다. REST/WS payload의 `price`를 체결단가로 그대로 신뢰하지 말 것.
- 코인 수동 주문 API 에러는 `validation`, `funds`, `auth`, `order_state`, `upstream` 으로 표준화하고, `broker_error_code`/`broker_error_message`를 함께 반환한다.
- 코인 어드민 중앙 라이브 워크스페이스는 주식 어드민과 동일한 `Agent Monitor → grouped symbol cards → 중요도 기반 toast/브라우저 알림/탭 강조 → SSE reconnect banner` 흐름을 사용한다.
- 코인 어드민 상태/중앙 모니터는 `/api/v1/admin-coin/system/status` alias 필드(`trading_enabled`, `autonomy_mode`, `scheduler_running`, `agent_running`, `sse_clients`)와 `/api/v1/admin-coin/agent/state` 파이프라인 스냅샷을 함께 사용한다.
- 코인 어드민 watchlist는 `/api/v1/admin-coin/watchlist`의 `stream_status`, `is_subscribed`, `thresholds`를 사용해 WS 감시 상태를 렌더링한다.
- 코인 어드민 시스템 상태는 `/system/status`의 `realtime_monitor_running`, `realtime`, `private_sync`를 함께 사용한다.
- 코인 활동 피드는 `CoinActivityLog.detail`와 `execution_time_ms`를 그대로 노출해 LLM system prompt / prompt / response를 인라인으로 점검하고, `/api/v1/admin-coin/activities/feed`는 stock admin과 동일하게 `resolved_trading_date`, `has_more`, `next_cursor(before_created_at,before_id)`를 반환한다.
- 코인 어드민 수동 스캔 버튼은 HTML inline handler를 쓰지 않고 단일 JS 바인딩만 사용한다. 버튼 상태는 `agent/state`를 기준으로 `요청 중 → 시작 대기 → 진행 중 → 완료/스킵` 흐름을 표시한다.
- 수동 코인 스캔(`/api/v1/admin-coin/agent/trigger`)은 `run_cycle()` 완료 후 `reconcile_market_watchlist("BITHUMB")`를 다시 호출해 최근 선정 종목이 즉시 코인 WebSocket desired set에 반영되도록 유지한다.
- 코인 수동 리포트 생성 API는 `POST /api/v1/admin-coin/reports/generate` 이고, 현재 시점 기준 체크포인트 회고를 만든 뒤 활성 규칙을 즉시 재적용한다.
- 수동 리포트는 시장 개요 조회가 실패하면 저장하지 않고 실패 메시지를 반환한다. 자동 pre-cycle 회고도 같은 조건에서는 새 리포트를 만들지 않고 기존 회고/규칙을 유지한다.
- 코인 어드민 최신 리포트 카드에는 `report_source`와 `period_ended_at` 우선 시각을 표시한다.
- 코인 어드민 좌측 내비게이션은 `실시간 모니터링`, `최신 리포트`, `과거 리포트` 목록으로 나뉘며, 중앙 패널에서 선택한 체크포인트 리포트를 상세 조회한다.
- 코인 리포트 상세 하단 활동 로그는 `GET /api/v1/admin-coin/reports/{report_id}/activities` 로 조회하고, 선택한 리포트의 `period_started_at ~ period_ended_at` 구간만 반환한다.
- 코인 시스템 상태 API(`/api/v1/admin-coin/system/status`)는 `bithumb_connectivity`를 포함한다. `dns_api_ok`, `dns_ws_ok`, `public_api_ok`, `last_error`, `checked_at`로 빗썸 연결 상태를 노출한다.
- 크립토 스캔 결과의 `monitoring` 필드는 `null`을 포함할 수 있으므로 `_apply_scan_thresholds()`에서는 `None`/빈값을 float 캐스팅하지 말고 무시해야 한다.
- SSE: `coin_sse_manager` 독립 인스턴스 (activity_logger 자동 분기). 코인 admin stream(`/api/v1/admin-coin/stream`)은 `activity`, `agent_state`, `account_changed` 이벤트를 받아 중앙 Agent Monitor 와 좌측 계좌/미체결 패널을 실시간 갱신한다.
- 코인 어드민 `/api/v1/admin-coin/account/overview` 는 `balance + holdings + pending_orders` 를 함께 반환하며, `balance.locked_krw` 와 `pending_orders[].status/status_detail/submitted_at/updated_at` 를 포함한다.
빗썸 API 상세(WebSocket, JWT 인증, rate limit, 에러 코드)는 `bithumb-api-ref` 스킬을 참조할 것.

## 에이전트 파이프라인 (코인)

```
Scheduler (4h 고정 cron)
  └─ TradingAgent.run_cycle(market="BITHUMB")
       ├─ _prepare_crypto_cycle_feedback() # scheduled 자동: 회고 생성 + 규칙 즉시 반영 / manual: 기존 회고 참조 + 규칙 재적용
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

**동기화 대상 파일:**
1. `.claude/commands/crypto-guide.md` (Claude Code 버전)
2. `.codex/skills/bithumb-api-ref/SKILL.md` (빗썸 API 변경 시)
3. `.claude/commands/bithumb-api-ref.md` (빗썸 API 변경 시)
