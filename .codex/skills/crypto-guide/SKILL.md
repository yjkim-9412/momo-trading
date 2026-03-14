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

| 파일 | 역할 |
|------|------|
| `trading/broker_base.py` | BrokerClient / MarketDataProvider / RealtimeProvider Protocol |
| `trading/bithumb_client.py` | 빗썸 REST 클라이언트 (JWT 인증, rate limiting) |
| `agent/scanner_base.py` | MarketScannerProtocol |
| `agent/crypto_scanner.py` | 코인 스캐너 (24h 거래대금/등락률 기반) |
| `trading/market_profile.py` | `is_crypto_market()`, `MARKET_SCOPE_CRYPTO` |
| `core/config.py` | `CRYPTO_*`, `BITHUMB_*` 환경변수 |
| `scheduler/market_calendar.py` | 24/7 세션 (`CRYPTO_ACTIVE`) |
| `strategy/risk_manager.py` | `CRYPTO_RR_FLOOR` |
| `trading/product_policy.py` | 크립토 Spot only 조기 리턴 |
| `analysis/llm/prompts/market_scan.py` | 크립토 스캔 프롬프트 |
| `analysis/llm/prompts/stock_analysis.py` | 크립토 Tier1 분석 프롬프트 |
| `analysis/llm/prompts/final_review.py` | 크립토 Tier2 리뷰 프롬프트 |
| `api/routes/admin_coin.py` | `/admin-coin` 전용 API |

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
CRYPTO_CODEX_MODEL                      # 코인 전용 Codex 모델
CRYPTO_CODEX_REASONING_EFFORT           # 코인 전용 Codex 추론 강도
CRYPTO_MAX_POSITION_PCT                 # 코인당 최대 비중
CRYPTO_MIN_CASH_RATIO                   # 최소 현금 비중
```

## 코인 구현 주의사항

- 빗썸 rate limit: public 10/s, private 5/s (BithumbClient 내부 관리)
- 소수점 수량: `OrderRequest.quantity = float` (주식은 정수만 전달)
- 캔들 정렬: newest-first → oldest-first 재정렬 (미국장과 동일 방어)
- JWT 인증: PyJWT + HS256. `Api-Key` + `Api-Sign` 헤더
- 시장 국면: BULL_RUN / BEAR_MARKET / CONSOLIDATION / ALTSEASON
- R:R floor: BULL_RUN=2.0, BEAR_MARKET=1.5 (주식보다 넓게)
- Product Policy: 크립토는 항상 COMMON (Spot only)
- 24/7 스케줄: buy cutoff / 강제 청산 없음

## 에이전트 파이프라인 (코인)

```
Scheduler (4h 고정 cron)
  └─ TradingAgent.run_cycle(market="BITHUMB")
       ├─ CryptoScanner.scan()             # 전체 코인 티커 → AI 선별
       │    └─ get_market_overview() → LLM Tier1 스캔
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
