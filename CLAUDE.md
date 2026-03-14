# MOMO Trading — Claude Code 지침

## 증권사 API 참조

KIS Open API 구현, 디버깅, 새 API 연동 시 반드시 `/kis-api-ref` 스킬을 사용하여 공식 샘플코드를 참조할 것.

- 공식 레포: https://github.com/koreainvestment/open-trading-api
- 스킬 사용법: `/kis-api-ref {검색어}` (예: `/kis-api-ref 주문`, `/kis-api-ref volume_rank`)
- LLM용 샘플은 `examples_llm/` 폴더에, 사용자용 통합 예제는 `examples_user/` 폴더에 위치

## 빗썸 API 참조

Bithumb API 구현, 디버깅, 새 API 연동 시 `/bithumb-api-ref` 스킬을 사용하여 로컬 레퍼런스를 참조할 것.

- 공식 문서: https://apidocs.bithumb.com/v2.1.0/reference/
- 스킬 사용법: `/bithumb-api-ref {검색어}` (예: `/bithumb-api-ref 주문`, `/bithumb-api-ref 캔들`)
- 로컬 레퍼런스는 `docs/bithumb-api/` 폴더에 위치 (PUBLIC 9개 + PRIVATE 25개 = 34개 엔드포인트)

## Codex CLI 참조

Codex CLI 관련 개발·디버깅 시 `/codex-ref` 스킬을 사용하여 공식 레포와 프로젝트 내 구현을 참조할 것.

- 공식 레포: https://github.com/openai/codex
- 스킬 사용법: `/codex-ref {검색어}` (예: `/codex-ref session`, `/codex-ref reasoning_effort`)
- 프로젝트 내 구현: `analysis/llm/codex_cli_provider.py`, `core/config.py`

## TradingAgent 구조

`agent/trading_agent/`는 Mixin 기반 패키지로, `from agent.trading_agent import trading_agent` 경로는 그대로 유지된다.

| Mixin | 역할 |
|-------|------|
| `_state_mixin.py` | `__init__`, 상태 관리, 런타임 (MRO 최우선) |
| `_portfolio_mixin.py` | 포트폴리오 스냅샷, 상품분류, 데이터 검증 |
| `_analysis_mixin.py` | 분석 파이프라인 (Tier1/Tier2), 컨텍스트 빌더 |
| `_cycle_mixin.py` | 사이클 오케스트레이션, 스케줄, 장마감 리뷰 |
| `_event_mixin.py` | 실시간 이벤트 핸들러 |
| `_types.py` | `MarketState` dataclass, 공유 상수 |

Mixin 파일은 서로를 임포트하지 않으며, 상호 호출은 `self.*`로 런타임 해결한다.

## 코인(빗썸) 시스템 가이드

코인 관련 개발·디버깅 시 `/crypto-guide` 스킬을 사용하여 코인 시스템 구조를 참조할 것.

- 스킬 사용법: `/crypto-guide {검색어}` (예: `/crypto-guide 주문`, `/crypto-guide 스캐너`)
- 도메인 구조 다이어그램: `docs/crypto-architecture.md` (Mermaid 6종)
- 환경변수 예제: `.env.example-coin`
- **코인 관련 코드 변경(파일 추가/삭제, 인터페이스 변경, 환경변수 추가 등) 시 반드시 `/crypto-guide` 스킬(`.claude/commands/crypto-guide.md`)도 함께 업데이트할 것.**

## 분리된 장 구조

- 운영 장은 `KRX`, `US`, `CRYPTO` 세 runtime scope로 분리한다.
- `market_scope` 는 스케줄, 리포트, 리스크, LLM 세션, 실시간 구독을 나누는 기준이다.
- 실제 주문/시세용 `market` 코드는 `KRX`, `NASDAQ`, `NYSE`, `AMEX`, `BITHUMB` 같은 거래소 코드를 그대로 유지한다.
- 미국장은 거래소 단위로 주문하지만, 장중 런타임과 장후 리뷰는 `US` scope로 묶어 처리한다.
- 코인은 `BITHUMB` 거래소 코드를 사용하며, `CRYPTO` scope로 묶어 처리한다.

## 스케줄 구조

- `장 시작 스캔`은 시장별 고정 cron으로 유지한다.
- `장중 재스캔`은 adaptive one-shot 구조다.
  - `TradingAgent.run_cycle()` 종료 후 `schedule_hint`가 생성된다.
  - 스케줄러는 시장별 `adaptive_rescan_*` job 하나만 유지한다.
  - 허용 간격은 `15/30/45/60/90/120분` 버킷으로 보정된다.
- scheduled 재스캔 예산은 `오픈 스캔 제외` 기준으로 `AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION`만큼만 사용한다.
- `보유종목 점검`, `강제 청산`, `장마감 리뷰`, `포트폴리오 정산`, `데이터 수집`은 여전히 고정 스케줄이다.
- 실시간 이벤트 기반 분석은 scheduled 재스캔 예산을 차감하지 않는다.
- 롤백 시에는 `AI_DYNAMIC_RESCAN_ENABLED=false`로 두면 기존 고정 `11:00/13:00` 장중 재스캔으로 복귀한다.

## 미국장 구현 회고

- 미국 프리마켓은 `US_PREMARKET_ENABLED=true`일 때 정식 분석 세션이다. 스케줄 기준은 `03:50 ET` 준비, `04:05 ET` 오픈 스캔이며, 그 이후 장중 재스캔은 `schedule_hint` 기반 adaptive one-shot으로 이어진다. 미국장 스케줄은 `scheduler/scheduler.py` 프로필과 adaptive 흐름을 기준으로 사용하고, 정규장 시간 하드코딩을 다시 넣지 말 것.
- 미국장 API 실패 원인은 `프리마켓 조회 불가`가 아니라 `해외 시세 burst 호출`이었다. 종목 병렬 분석과 종목 내부 `현재가 + 일봉 + 분봉` 동시 조회가 겹치면 KIS가 `초당 거래건수를 초과하였습니다.`를 반환할 수 있다. 해외 quote 경로는 공통 limiter/직렬화 경로를 유지할 것.
- KIS 해외 `dailyprice`와 `inquire-time-itemchartprice`는 최신순 응답일 수 있다. 지표 계산기는 `oldest -> newest`를 가정하므로, 미국장 시세는 `trading/mcp_client.py`에서 정렬하고 `agent/trading_agent/`에서 DataFrame 단계에서 다시 정렬하는 이중 방어를 유지할 것.
- 미국 단기매매는 실시간 현재가와 같은 가격 축이 우선이다. 해외 일봉은 `MODP="0"` 비수정주가 기준으로 맞추고, 수정주가 일봉과 실시간 현재가를 섞지 말 것. 단기 AI 분석 경로에서 `MODP="1"` 복귀는 금지한다.
- 미국장 Tier2 가격 필드는 반드시 시장 통화 기준이다. 미국 종목의 `entry_price`, `target_price`, `stop_loss_price`, `take_profit_price`에 원화를 넣지 말 것. 원화처럼 보이는 응답은 시장 통화로 정규화하고, 주문 직전에는 실시간 현재가 대비 지정가 sanity guard를 통과한 값만 주문 경로로 보낼 것.
- `KIS_ACCOUNT_TYPE=VIRTUAL`에서는 미국 `US_PRE`/`US_AFTER` 주문이 KIS 서버에서 거절될 수 있다. 모의계좌의 미국 프리마켓/애프터마켓은 분석 세션으로 유지하되, 자동주문은 추천 fallback으로 전환하는 현재 동작을 유지할 것.
- `현재가 vs SMA/VWAP 절대가격 지표 충돌`은 LLM 품질 문제가 아니라 입력 데이터 정합성 문제일 가능성이 높다. 분석 전 `latest_daily_close`, `latest_minute_close`와 현재가 괴리를 검사하고, 큰 괴리는 분석 차단과 상세 로그로 처리할 것.
- 일봉 다건 누적으로 만든 `VWAP`는 intraday 판단에 부적합하다. 미국 단기매매에서는 일봉 지표에서 VWAP를 제외하고, 분봉 기반 intraday 분석에서만 VWAP 맥락을 사용한다.
- 운영 중 미국장 로그 해석 규칙:
  - 여러 종목이 동시에 `현재가·일봉 조회 실패`면 우선 rate limit/burst 의심
  - `데이터 정합성 차단`이면 가격 축, 정렬, `MODP` 기준을 먼저 점검
- 미국장 구현 수정 시 반드시 같이 확인할 테스트:
  - 해외 일봉 `MODP=0`
  - 해외 일봉/분봉 정렬
  - 해외 quote 직렬화 및 rate limit 재시도
  - 분석 전 데이터 정합성 차단

## 코인(빗썸) 구현 회고

- 코인 장은 `CRYPTO` scope로 주식(KRX/US)과 완전 격리 운영된다. `MarketState`는 `normalize_market_scope("BITHUMB")` → `"CRYPTO"` 기준으로 자동 생성되므로, 독립 `cycle_lock`, `session_ids`, `trading_date`를 갖는다.
- 코인 환경변수는 주식과 완전 분리한다. `CRYPTO_TRADING_ENABLED`, `CRYPTO_AUTONOMY_MODE`, `CRYPTO_MAX_DAILY_TRADES` 등은 주식의 `TRADING_ENABLED`, `AUTONOMY_MODE`와 독립이다. 코인 설정을 주식 설정에 섞지 말 것.
- 빗썸 API 인증은 JWT Bearer (PyJWT + HS256). KIS의 OAuth2 토큰과 완전 다른 체계이므로 `BithumbClient`에서 자체 관리한다. 빗썸 인증 로직을 KIS 경로에 섞지 말 것.
- 빗썸 rate limit은 public 10 req/s, private 5 req/s. KIS의 8 req/s + 해외 1 req/s와 독립된 `BithumbClient` 내부 semaphore로 관리한다. 두 브로커의 rate limiter를 공유하지 말 것.
- 빗썸 캔들 응답은 newest-first일 수 있다. KIS 해외 일봉과 동일한 방어로 `BithumbClient`에서 oldest-first 정렬한다. 분석 파이프라인의 이중 정렬 방어는 코인에도 적용된다.
- 코인 수량은 소수점이다 (`OrderRequest.quantity = float`). 주식은 항상 정수만 전달하므로 하위호환. `Decimal`은 `BithumbClient` 내부 계산에서만 사용하고, API 경계에서 float로 변환한다.
- 코인은 24/7 시장이므로 `buy_cutoff`, `force_liquidation`, `장 시작/마감` 개념이 없다. `market_calendar.is_trading_hours("BITHUMB")`는 항상 True를 반환한다. 코인에 시간 기반 매수 차단을 넣지 말 것.
- 코인 시장 국면은 `BULL_RUN`/`BEAR_MARKET`/`CONSOLIDATION`/`ALTSEASON`이다. 주식의 `BULL`/`BEAR`/`SIDEWAYS`/`THEME`와 다르지만, `risk_manager.CRYPTO_RR_FLOOR`에서 두 체계 모두 매핑한다.
- 코인 Tier2 스트레스 테스트는 -10%/-7% (주식의 -5%/-3%보다 넓다). 코인 변동성 기준을 주식 수준으로 좁히지 말 것.
- `/admin-coin` 페이지는 주식 `/admin`과 완전 별도 SPA이다. API prefix는 `/api/v1/coin/*`, SSE는 독립 `coin_sse_manager`를 사용한다. 주식 SSE와 코인 SSE를 공유하지 말 것.
- 코인 구현 수정 시 반드시 같이 확인할 테스트:
  - `is_crypto_market()` 정규화 (기존 KRX/US 회귀 포함)
  - 빗썸 캔들 oldest-first 정렬
  - 빗썸 rate limit 동작
  - `CRYPTO_*` 환경변수 독립성 (주식 설정 영향 없음)

## 테스트

- 테스트 실행 시 반드시 `.venv` 가상환경의 Python을 사용할 것.
- 실행: `.venv/bin/python -m pytest tests/ -v`
- `.venv`가 없으면 `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`로 먼저 생성할 것.
