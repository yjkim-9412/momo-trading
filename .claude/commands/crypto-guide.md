# 코인(빗썸) 시스템 가이드

코인 관련 개발·디버깅·새 기능 추가 시 시스템 구조를 참조한다.

**도메인 구조 다이어그램:** `docs/crypto-architecture.md` (Mermaid 6종)

## 사용자 인자

$ARGUMENTS — 검색할 키워드 (예: 주문, 스캐너, 스케줄, 프롬프트, 리스크, 환경변수 등)

## 코인 도메인 구조

### Market Scope 분리

| Scope | 시장 | 브로커 | 24/7 | 통화 |
|-------|------|--------|------|------|
| `KRX` | KOSPI, KOSDAQ | KIS (MCP + REST) | N | KRW |
| `US` | NASDAQ, NYSE, AMEX | KIS (REST) | N | USD |
| `CRYPTO` | BITHUMB | Bithumb REST API | **Y** | KRW |

`market_scope`는 스케줄, 리포트, 리스크, LLM 세션, 실시간 구독을 나누는 기준이다.
코인은 `CRYPTO` scope로 주식(KRX/US)과 완전 격리 운영된다.

### Protocol 인터페이스 계층

| Protocol | 파일 | KIS 구현체 | Bithumb 구현체 |
|----------|------|-----------|---------------|
| `BrokerClient` | `trading/broker_base.py` | `MCPClient` | `BithumbClient` |
| `MarketDataProvider` | `trading/broker_base.py` | `MCPClient` | `BithumbClient` |
| `RealtimeProvider` | `trading/broker_base.py` | `KISWebSocket` | `BithumbWebSocket` |
| `MarketScannerProtocol` | `agent/scanner_base.py` | `MarketScanner` | `CryptoScanner` |

## 핵심 파일 맵

### 인프라 & 인터페이스
| 파일 | 역할 |
|------|------|
| `trading/broker_base.py` | BrokerClient / MarketDataProvider / RealtimeProvider Protocol 정의 |
| `trading/bithumb_client.py` | 빗썸 REST API 클라이언트 (BrokerClient 구현체, JWT 인증) |
| `trading/quantity_policy.py` | 코인/주식 수량 정책 분기 (코인 8자리 소수점, 주식 정수) |
| `trading/bithumb_websocket.py` | 빗썸 Public/Private WS 런타임 |
| `realtime/coin_stream_manager.py` | 코인 실시간 desired/active 구독 상태 관리 |
| `realtime/coin_monitor.py` | WS 수신 → 이벤트 감지 / 주문·자산 동기화 |
| `agent/scanner_base.py` | MarketScannerProtocol 정의 |
| `agent/crypto_scanner.py` | 코인 시장 스캐너 (24h 거래대금/등락률 기반, shared prompt 사용, canonical regime 정규화) |
| `analysis/llm/llm_factory.py` | 코인 scope LLM provider 라우팅 및 Codex 장애 fallback |
| `trading/market_profile.py` | `is_crypto_market()`, `MARKET_SCOPE_CRYPTO`, BITHUMB 프로필 |
| `trading/risk_policy.py` | 코인 canonical regime alias 정규화, shared RR floor 상수 |
| `core/config.py` | `CRYPTO_*`, `BITHUMB_*` 환경변수 (주식과 완전 독립) |

### 분석 & 리스크
| 파일 | 역할 |
|------|------|
| `scheduler/market_calendar.py` | 24/7 세션 (`CRYPTO_ACTIVE`), 휴장 없음 |
| `strategy/risk_manager.py` | `CRYPTO_RR_FLOOR` (주식보다 넓은 R:R) |
| `trading/product_policy.py` | 크립토 → Spot only 조기 리턴 |
| `analysis/llm/prompts/market_scan.py` | 크립토 스캔 프롬프트 |
| `analysis/llm/prompts/stock_analysis.py` | 크립토 Tier1 분석 프롬프트 |
| `analysis/llm/prompts/final_review.py` | 크립토 Tier2 리뷰 프롬프트 |

### 코인 전용 DB 모델 (주식 테이블과 완전 분리)
| 파일 | 테이블 | 주식 대응 |
|------|--------|----------|
| `models/coin_asset.py` | `coin_assets` | `stocks` |
| `models/coin_order.py` | `coin_orders` | `orders` |
| `models/coin_broker_order.py` | `coin_broker_orders` | `broker_orders` |
| `models/coin_trade_result.py` | `coin_trade_results` | `trade_results` |
| `models/coin_holding.py` | `coin_holdings` | `portfolio_holdings` |
| `models/coin_activity_log.py` | `coin_activity_logs` | `agent_activity_logs` |
| `models/coin_daily_report.py` | `coin_daily_reports` | `daily_reports` |
| `models/coin_trading_rule.py` | `coin_trading_rules` | `trading_rules` |
| `models/coin_analysis_result.py` | `coin_analysis_results` | `analysis_results` |
| `models/coin_recommendation.py` | `coin_recommendations` | `recommendations` |

### API & 운영
| 파일 | 역할 |
|------|------|
| `api/routes/admin_coin.py` | `/admin-coin` 전용 API (`/api/v1/admin-coin/*`, `/system/status`, `/agent/state`, 활동/LLM 상세) |
| `start-coin.sh` | 코인 전용 실행 스크립트 (Docker/MCP 불필요) |
| `.env.example-coin` | 코인 환경변수 예제 (5만원~적극적 프로필) |
| `docs/crypto-architecture.md` | Mermaid 다이어그램 6종 |
| `docs/bithumb-api/websocket/` | 빗썸 WebSocket API 문서 6개 (기본정보, ticker, trade, orderbook, myOrder, myAsset) |

## 환경변수 분리 구조

코인 설정은 주식과 **완전 독립**이다. `.env.example-coin` 참조.

| 주식 (KIS) | 코인 (Bithumb) | 역할 |
|------------|---------------|------|
| `KIS_APP_KEY` | `BITHUMB_API_KEY` | API 인증 |
| `PRIMARY_MARKET` | `CRYPTO_PRIMARY_MARKET` | 기본 시장 코드 |
| `TRADING_ENABLED` | `CRYPTO_TRADING_ENABLED` | 주문 실행 허용 |
| `AUTONOMY_MODE` | `CRYPTO_AUTONOMY_MODE` | 자율/반자율 모드 |
| `LLM_PROVIDER` | `CRYPTO_LLM_PROVIDER` | LLM 프로바이더 |
| `CLAUDE_CODE_MODEL_TIER1` | `CRYPTO_LLM_MODEL_TIER1_SCAN` / `_ANALYSIS` | Claude 모델 (스캔/분석 분리) |
| `CLAUDE_CODE_MODEL_TIER2` | `CRYPTO_LLM_MODEL_TIER2` | Claude 검토 모델 |
| — (하드코딩 medium/high) | `CRYPTO_CLAUDE_EFFORT_TIER1_SCAN` / `_ANALYSIS` / `_TIER2` | Claude effort |
| `CODEX_MODEL` | `CRYPTO_CODEX_MODEL` | Codex 모델 |
| `CODEX_REASONING_EFFORT_TIER1_SCAN` | `CRYPTO_CODEX_REASONING_EFFORT_TIER1_SCAN` | Codex 스캔 추론 |
| `CODEX_REASONING_EFFORT_TIER1_ANALYSIS` | `CRYPTO_CODEX_REASONING_EFFORT_TIER1_ANALYSIS` | Codex 분석 추론 |
| `MAX_DAILY_TRADES` | `CRYPTO_MAX_DAILY_TRADES` | 일일 거래 한도 |
| `MIN_CASH_RATIO` | `CRYPTO_MIN_CASH_RATIO` | 최소 현금 비중 |
| `MAX_SINGLE_ORDER_KRW` | `CRYPTO_MAX_SINGLE_ORDER_KRW` | 1회 주문 한도 |
| — | `CRYPTO_DISCOVERY_REFRESH_MINUTES` | discovery universe 새로고침 주기 |
| — | `CRYPTO_DISCOVERY_UNIVERSE_SIZE` | 유지할 상위 유동성 코인 수 |

## 24/7 스케줄 구조

코인은 장 시작/마감 개념이 없다:
- **스캔**: `CRYPTO_SCAN_INTERVAL_HOURS` 간격 고정 cron (기본 4시간)
- **보유 점검**: `CRYPTO_HOLDINGS_CHECK_INTERVAL_HOURS` 간격 (기본 2시간)
- **일일 리뷰**: 매일 00:00 KST (지난 24시간 요약)
- **buy cutoff / 강제 청산**: 없음 (24/7)

## 코인 구현 주의사항

- 빗썸 rate limit: 공식 한도 Public **150 req/s**, Private **140 req/s**, 주문(생성/취소) **10 req/s**. 코드(`BithumbClient`)는 보수적으로 Public 10, Private 5 semaphore 설정. 필요 시 상향 가능.
- 소수점 수량: `OrderRequest.quantity`가 `float`. 주식은 정수만 전달하므로 하위호환.
- 코인 소수점 수량은 `trading/quantity_policy.py`를 기준으로 end-to-end 8자리 floor 정책을 유지한다. 포지션 스냅샷, 계좌 컨텍스트, 리스크 캡, Tier2 제안 수량, 빗썸 주문 payload는 코인만 fractional 을 보존하고 주식 API/스키마는 그대로 둔다.
- 캔들 정렬: 빗썸은 newest-first → `BithumbClient`에서 oldest-first로 재정렬 (미국장과 동일 방어).
- JWT 인증: `PyJWT` (HS256). `Authorization: Bearer {jwt_token}` 헤더. Content-Type: `application/json; charset=utf-8`.
- 빗썸 WebSocket 최신 공식 엔드포인트: `wss://ws-api.bithumb.com/websocket/v1`, `wss://ws-api.bithumb.com/websocket/v1/private`
- Public WS는 `ticker`, `trade`, `orderbook`; Private WS는 `myOrder`, `myAsset`를 사용한다.
- 코인 미체결 주문의 source of truth 는 빗썸 REST placeholder 가 아니라 `coin_broker_orders` 원장이다. `AccountManager.get_pending_orders("BITHUMB")` 는 `SUBMITTED` / `OPEN` / `PARTIAL` 상태를 DB에서 읽어 코인 어드민 overview 로 반환한다.
- `myAsset` payload 는 총자산/손익을 직접 주지 않고 `balance` / `locked` 변경만 주므로, 코인 어드민의 `총 자산`, `KRW 현금`, `코인 평가`, `잠금 KRW` 는 WS 이벤트를 트리거로 `/api/v1/admin-coin/account/overview` 를 재조회해 확정한다.
- 시장 국면 canonical 값: `BULL_RUN` / `BEAR_MARKET` / `CONSOLIDATION` / `ALTSEASON` / `THEME`
- legacy alias는 `normalize_crypto_regime()`에서 `BEAR -> BEAR_MARKET`, `SIDEWAYS -> CONSOLIDATION`, `ALT_SEASON -> ALTSEASON`으로 정규화한다
- R:R floor: BULL_RUN=2.0, BEAR_MARKET=1.5 (주식보다 넓게).
- 코인 기본 시장 fallback은 주식 `PRIMARY_MARKET`를 공유하지 않는다. 코인 경로는 `CRYPTO_PRIMARY_MARKET`를 우선 사용하고, 값이 비어 있거나 잘못되면 `BITHUMB`로 고정한다.
- 코인 프롬프트 스택(`market_scan.py`, `stock_analysis.py`, `final_review.py`)은 `빗썸 KRW 현물`, `수시간~2일`, `BUY/HOLD 전용`, `24h 거래대금 기반 유동성 확인`을 공통 계약으로 유지한다.
- Product Policy: 크립토는 항상 `COMMON` (Spot only), 레버리지/인버스 분류 Skip.
- 코인 스캔은 `CRYPTO_DYNAMIC_DISCOVERY_ENABLED=true`일 때 `get_discovery_universe()`로 저빈도 broad refresh를 수행하고, `CRYPTO_DISCOVERY_UNIVERSE_SIZE` 상위 유동성 코인을 메모리 캐시에 유지한다.
- broad refresh 기본 주기는 `CRYPTO_DISCOVERY_REFRESH_MINUTES=360`이며, 평소 스캔은 cached universe 심볼과 `CRYPTO_WATCHLIST_SYMBOLS`만 `get_ticker_snapshots()`로 selective 조회한다.
- live broad refresh가 실패해도 stale discovery cache가 있으면 `DISCOVERY` 후보를 계속 유지하고, cache도 없을 때만 watchlist/보유 코인 현재가 기반 degraded 스캔으로 내려간다.
- 코인 스캔은 `get_market_overview()` 1회 조회 결과를 `get_volume_rank(..., overview_data=...)` / `get_surge_data(..., overview_data=...)`에 재사용한다. overview 실패 시 watchlist/보유 코인 현재가로 degraded 스캔을 시도한다.
- 코인 후보/선정 결과에는 `scan_source`가 붙는다. 정상 경로는 `DISCOVERY` / `WATCHLIST`, degraded 경로는 `WATCHLIST_FALLBACK` / `HOLDING_FALLBACK`를 사용한다.
- `BithumbClient.get_current_price()` / overview ticker 정규화에서 공통 `change` 필드는 숫자 변화량으로 맞춘다. 원본 방향 문자열은 `change_direction`, 부호 있는 변화량은 `signed_change_price`로 별도 보존한다.
- `BithumbClient._public_get()`는 HTTP status, content-type, body preview를 함께 로그에 남긴다. `Expecting value`만 보이면 upstream HTML/빈 본문/5xx 가능성을 먼저 확인할 것.
- 코인 scope에서 `CRYPTO_LLM_PROVIDER=CODEX_CLI`이고 Codex 인증/세션 갱신이 실패하면 `analysis/llm/llm_factory.py`가 `CLAUDE_CODE` fallback을 1회 시도한다.
- 주문 체결 확인: `POST /v1/orders` 접수 후 `GET /v1/order?uuid=...` 개별 조회로 상태를 추적한다.
- 보유 코인 현재가: `_fetch_coin_prices(symbols)` 헬퍼로 벌크 ticker 조회 (1 API call). `get_account_balance()`와 `get_holdings()` 모두 현재가 기반 평가. 조회 실패 시 `avg_buy_price` 폴백.
- `get_account_balance()` / `get_holdings()`는 `/v1/market/all`의 실제 `KRW-*` 마켓에 없는 자산을 비거래성 자산으로 간주해 제외한다. 마켓 카탈로그 조회가 깨지면 stale cache를 우선 쓰고, cache도 없으면 `avg_buy_price=0` 이고 현재가도 없는 자산만 degraded 규칙으로 제외한다.
- 코인 DB는 주식 테이블과 완전 분리 (10개 `coin_*` 테이블). FK는 코인 도메인 내부만 참조.
- 코인 활동 로그 → `CoinActivityLog`, 코인 추천 → `CoinRecommendation`. `admin_coin.py`는 주식 Repository가 아닌 직접 쿼리 사용.
- 코인 어드민 상태 패널은 `/api/v1/admin-coin/system/status` alias 필드(`trading_enabled`, `autonomy_mode`, `scheduler_running`, `agent_running`, `sse_clients`)와 `/api/v1/admin-coin/agent/state` 파이프라인 스냅샷을 함께 사용한다.
- 코인 어드민 watchlist는 `/api/v1/admin-coin/watchlist`의 `stream_status`, `is_subscribed`, `thresholds`로 WS 감시 상태를 렌더링한다.
- 코인 어드민 시스템 상태는 `/system/status`의 `realtime_monitor_running`, `realtime`, `private_sync`까지 함께 본다.
- 코인 활동 피드는 `CoinActivityLog.detail`와 `execution_time_ms`를 그대로 노출해 LLM system prompt / prompt / response를 인라인으로 점검한다.
- 코인 어드민 수동 스캔 버튼은 HTML inline handler를 쓰지 않고 단일 JS 바인딩만 사용한다. 버튼 상태는 `agent/state`를 기준으로 `요청 중 → 시작 대기 → 진행 중 → 완료/스킵` 흐름을 표시한다.
- 수동 코인 스캔(`/api/v1/admin-coin/agent/trigger`)은 `run_cycle()` 완료 후 `reconcile_market_watchlist("BITHUMB")`를 다시 호출해 최근 선정 종목이 즉시 코인 WebSocket desired set에 반영되도록 유지한다.
- 크립토 스캔 결과의 `monitoring` 필드는 `null`을 포함할 수 있으므로 `_apply_scan_thresholds()`에서는 `None`/빈값을 float 캐스팅하지 말고 무시해야 한다.
- 코인 SSE는 `coin_sse_manager`(독립 인스턴스)로 브로드캐스트. `activity_logger`가 CRYPTO scope 자동 분기한다.
- 코인 admin stream(`/api/v1/admin-coin/stream`)은 `activity`, `agent_state`, `account_changed` 이벤트를 받아 중앙 Agent Monitor 와 좌측 계좌/미체결 패널을 실시간 갱신한다.
- 코인 어드민 `/api/v1/admin-coin/account/overview` 는 `balance + holdings + pending_orders` 를 함께 반환하며, `balance.locked_krw` 와 `pending_orders[].status/status_detail/submitted_at/updated_at` 를 포함한다.

## 빗썸 WebSocket API

- **Public**: `wss://ws-api.bithumb.com/websocket/v1` (인증 불필요)
- **Private**: `wss://ws-api.bithumb.com/websocket/v1/private` (JWT Bearer)
- **구독 5종**: `ticker` / `trade` / `orderbook` (Public), `myOrder` / `myAsset` (Private)
- **요청 형식**: JSON 배열 `[{ticket}, {type}, ..., {type}, {format}]`
- **연결 제한**: 10 conn/sec/IP, 메시지 5/sec + 100/min, 수신 무제한
- **연결 유지**: RFC 6455 PING/PONG, 120초 idle timeout, 서버 `{"status":"UP"}` 매 10초
- **에러 코드**: `WRONG_FORMAT`, `NO_TICKET`, `NO_TYPE`, `NO_CODES`, `INVALID_PARAM`
- **상세 문서**: `docs/bithumb-api/websocket/` (6개 파일)

## 빗썸 API 요청 제한 (v2.1.0)

| 구분 | 공식 한도 | 코드 설정 (보수적) | 비고 |
|------|----------|------------------|------|
| Public API | 150 req/s | 10 req/s (semaphore) | 시세, 캔들, 호가 |
| Private API | 140 req/s | 5 req/s (semaphore) | 잔고, 입출금 |
| 주문 (생성/취소) | 10 req/s | 별도 제한 없음 | 초과 시 일시 제한 |

- 초과 시: API 사용 일시 제한 → 대기 후 재요청
- 과도한 트래픽 시 별도 공지 없이 제한값 조정 가능
- 요청 간격을 균등 분산하면 제한 가능성 감소

## 빗썸 JWT 인증 구조

```
Authorization: Bearer {jwt_token}
Content-Type: application/json; charset=utf-8
```

**JWT Payload:**
| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `access_key` | String | O | API Key |
| `nonce` | String | O | UUID v4 |
| `timestamp` | Number | O | 밀리초 단위 |
| `query_hash` | String | 파라미터 있을 때 | SHA-512 해시 of query string |
| `query_hash_alg` | String | query_hash 있을 때 | `"SHA512"` |

서명: **HS256** (Secret Key로 서명)

## 빗썸 주요 에러 코드

| HTTP | 코드 | 설명 |
|------|------|------|
| 400 | `invalid_parameter` | 잘못된 파라미터 |
| 400 | `invalid_price` | 주문가격 단위 오류 |
| 400 | `cross_trading` | 기존 주문과 체결 가능성으로 취소 |
| 401 | `jwt_verification` | JWT 토큰 검증 실패 |
| 401 | `expired_jwt` | JWT 만료 |
| 401 | `NotAllowIP` | 허용되지 않는 IP |
| 401 | `out_of_scope` | API 권한 부족 |
| 404 | `order_not_found` | 주문 정보 없음 |
| 422 | `order_not_ready` | 주문 처리 중, 재시도 필요 |
| 500 | `server_error` | 서버 오류, 재시도 |

## 실행 절차

사용자 인자 "$ARGUMENTS"에 대해:

### Step 1: 키워드 매칭

"$ARGUMENTS"를 아래 카테고리와 매칭:
- **주문/거래**: `trading/bithumb_client.py` → `place_order()`, `cancel_order()`
- **시세/캔들**: `trading/bithumb_client.py` → `get_current_price()`, `get_daily_price()`, `get_minute_price()`
- **잔고/계좌**: `trading/bithumb_client.py` → `get_account_balance()`, `get_holdings()`
- **스캐너/스캔**: `agent/crypto_scanner.py`
- **프롬프트/LLM**: `analysis/llm/prompts/market_scan.py`, `stock_analysis.py`, `final_review.py`
- **리스크**: `strategy/risk_manager.py` → `CRYPTO_RR_FLOOR`
- **스케줄**: `scheduler/market_calendar.py`, `scheduler/scheduler.py`
- **환경변수/설정**: `core/config.py` → `CRYPTO_*`, `BITHUMB_*`
- **웹소켓/WebSocket/실시간**: `docs/bithumb-api/websocket/` (기본정보, ticker, trade, orderbook, myOrder, myAsset), `trading/bithumb_websocket.py`, `realtime/coin_stream_manager.py`, `realtime/coin_monitor.py`
- **인터페이스/프로토콜**: `trading/broker_base.py`, `agent/scanner_base.py`
- **DB/모델/테이블**: `models/coin_*.py` (10개 코인 전용 테이블, 주식과 완전 분리)
- **API/라우트**: `api/routes/admin_coin.py`
- **실행/스크립트**: `start-coin.sh` (Docker 불필요, 코인 전용)
- **아키텍처**: `docs/crypto-architecture.md`

### Step 2: 관련 파일 조회

매칭된 카테고리의 핵심 파일을 읽어서 구조와 메서드를 파악한다.

### Step 3: 결과 정리

- 관련 파일 경로, 클래스/메서드 안내
- 환경변수 설정 방법
- 주의사항 및 기존 코드와의 연관성

## 스킬 유지보수 규칙

**코인 관련 코드 변경 시 이 스킬도 반드시 함께 업데이트할 것.**

다음 변경이 발생하면 이 파일(`.claude/commands/crypto-guide.md`)을 수정:
- 새 파일 추가/삭제 → 핵심 파일 맵 테이블 업데이트
- Protocol 메서드 시그니처 변경 → 인터페이스 계층 테이블 업데이트
- 환경변수 추가/제거 → 환경변수 분리 구조 테이블 업데이트
- 스케줄 구조 변경 → 24/7 스케줄 구조 섹션 업데이트
- 주의사항 추가 → 코인 구현 주의사항 섹션 업데이트
- 키워드 카테고리 변경 → Step 1 키워드 매칭 목록 업데이트

Codex 스킬(`.codex/skills/crypto-guide/SKILL.md`)도 동일하게 동기화할 것.
