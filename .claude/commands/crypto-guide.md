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
| `RealtimeProvider` | `trading/broker_base.py` | `KISWebSocket` | `BithumbWebSocket` (Phase 3) |
| `MarketScannerProtocol` | `agent/scanner_base.py` | `MarketScanner` | `CryptoScanner` |

## 핵심 파일 맵

| 파일 | 역할 |
|------|------|
| `trading/broker_base.py` | BrokerClient / MarketDataProvider / RealtimeProvider Protocol 정의 |
| `trading/bithumb_client.py` | 빗썸 REST API 클라이언트 (BrokerClient 구현체, JWT 인증) |
| `agent/scanner_base.py` | MarketScannerProtocol 정의 |
| `agent/crypto_scanner.py` | 코인 시장 스캐너 (24h 거래대금/등락률 기반) |
| `trading/market_profile.py` | `is_crypto_market()`, `MARKET_SCOPE_CRYPTO`, BITHUMB 프로필 |
| `core/config.py` | `CRYPTO_*`, `BITHUMB_*` 환경변수 (주식과 완전 독립) |
| `scheduler/market_calendar.py` | 24/7 세션 (`CRYPTO_ACTIVE`), 휴장 없음 |
| `strategy/risk_manager.py` | `CRYPTO_RR_FLOOR` (주식보다 넓은 R:R) |
| `trading/product_policy.py` | 크립토 → Spot only 조기 리턴 |
| `analysis/llm/prompts/market_scan.py` | 크립토 스캔 프롬프트 |
| `analysis/llm/prompts/stock_analysis.py` | 크립토 Tier1 분석 프롬프트 |
| `analysis/llm/prompts/final_review.py` | 크립토 Tier2 리뷰 프롬프트 |
| `api/routes/admin_coin.py` | `/admin-coin` 전용 API (18개 엔드포인트) |
| `docs/crypto-architecture.md` | Mermaid 다이어그램 6종 |

## 환경변수 분리 구조

코인 설정은 주식과 **완전 독립**이다. `.env.example-coin` 참조.

| 주식 (KIS) | 코인 (Bithumb) | 역할 |
|------------|---------------|------|
| `KIS_APP_KEY` | `BITHUMB_API_KEY` | API 인증 |
| `TRADING_ENABLED` | `CRYPTO_TRADING_ENABLED` | 주문 실행 허용 |
| `AUTONOMY_MODE` | `CRYPTO_AUTONOMY_MODE` | 자율/반자율 모드 |
| `LLM_PROVIDER` | `CRYPTO_LLM_PROVIDER` | LLM 프로바이더 (CLAUDE_CODE / CODEX_CLI) |
| `CODEX_MODEL` | `CRYPTO_CODEX_MODEL` | Codex 모델 |
| `MAX_DAILY_TRADES` | `CRYPTO_MAX_DAILY_TRADES` | 일일 거래 한도 |
| `MIN_CASH_RATIO` | `CRYPTO_MIN_CASH_RATIO` | 최소 현금 비중 |
| `MAX_SINGLE_ORDER_KRW` | `CRYPTO_MAX_SINGLE_ORDER_KRW` | 1회 주문 한도 |

## 24/7 스케줄 구조

코인은 장 시작/마감 개념이 없다:
- **스캔**: `CRYPTO_SCAN_INTERVAL_HOURS` 간격 고정 cron (기본 4시간)
- **보유 점검**: `CRYPTO_HOLDINGS_CHECK_INTERVAL_HOURS` 간격 (기본 2시간)
- **일일 리뷰**: 매일 00:00 KST (지난 24시간 요약)
- **buy cutoff / 강제 청산**: 없음 (24/7)

## 코인 구현 주의사항

- 빗썸 rate limit: public 10 req/s, private 5 req/s. `BithumbClient` 내부 semaphore로 관리.
- 소수점 수량: `OrderRequest.quantity`가 `float`. 주식은 정수만 전달하므로 하위호환.
- 캔들 정렬: 빗썸은 newest-first → `BithumbClient`에서 oldest-first로 재정렬 (미국장과 동일 방어).
- JWT 인증: `PyJWT` 라이브러리 사용. `Api-Key` + `Api-Sign` (HS256 JWT) 헤더.
- 시장 국면: `BULL_RUN` / `BEAR_MARKET` / `CONSOLIDATION` / `ALTSEASON` (주식의 BULL/BEAR/SIDEWAYS/THEME와 별도).
- R:R floor: BULL_RUN=2.0, BEAR_MARKET=1.5 (주식보다 넓게).
- Product Policy: 크립토는 항상 `COMMON` (Spot only), 레버리지/인버스 분류 Skip.

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
- **인터페이스/프로토콜**: `trading/broker_base.py`, `agent/scanner_base.py`
- **API/라우트**: `api/routes/admin_coin.py`
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
