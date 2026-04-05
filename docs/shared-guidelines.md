# MOMO Trading — 공통 운영 지침

CLAUDE.md와 AGENTS.md에서 공유하는 운영 규칙. 양쪽 지침 파일이 이 문서를 참조한다.

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

## 주식 로드맵 눌림형 운영

- `ROADMAP_PULLBACK_ENABLED_KRX` 또는 `ROADMAP_PULLBACK_ENABLED_US`가 켜진 시장은 신규 BUY 후보를 `20/60일선 눌림형` 하드 게이트로 제한한다.
- 하드 게이트 기본 규칙은 `SMA20 >= SMA60`, `SMA60` 최근 기울기 비하락, 현재가가 `SMA20` 또는 `SMA60` 눌림 밴드 안에 있을 때만 통과다.
- 이 모드에서는 `PRICE_SURGE`, `PRICE_DROP`, `VOLUME_SPIKE`가 신규 진입 실시간 트리거가 아니다. 대신 `INDICATOR_SIGNAL` 기반 `ROADMAP_SMA20_PULLBACK`, `ROADMAP_SMA60_PULLBACK` 진입 신호만 재분석 트리거로 사용한다.
- 신규 BUY는 기본적으로 `FIRST_TRANCHE` 분할 진입으로 기록하고, `SMA60_PULLBACK` 구간은 `SECOND_TRANCHE` 후보로만 해석한다.
- 로드맵 모드에서도 보유 포지션의 `STOP_LOSS_HIT`, `TAKE_PROFIT_HIT` 실시간 청산은 그대로 유지한다.

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
- 빗썸 rate limit 공식 한도는 Public **150 req/s**, Private **140 req/s**, 주문(생성/취소) **10 req/s**. 코드(`BithumbClient`)는 burst 방지를 위해 보수적으로 Public 10, Private 5 semaphore를 설정한다. 필요 시 상향 가능하나 두 브로커의 rate limiter를 공유하지 말 것.
- 빗썸 캔들 응답은 newest-first일 수 있다. KIS 해외 일봉과 동일한 방어로 `BithumbClient`에서 oldest-first 정렬한다. 분석 파이프라인의 이중 정렬 방어는 코인에도 적용된다.
- 코인 수량은 소수점이다 (`OrderRequest.quantity = float`). 주식은 항상 정수만 전달하므로 하위호환. `Decimal`은 `BithumbClient` 내부 계산에서만 사용하고, API 경계에서 float로 변환한다.
- 코인은 24/7 시장이므로 주식식 `buy_cutoff`, `장 시작/마감`, 장종료 일괄 `force_liquidation` 개념을 그대로 쓰지 않는다. `market_calendar.is_trading_hours("BITHUMB")`는 항상 True를 반환한다.
- 대신 코인 포지션은 `CRYPTO_TIMEBOX_HOURS` 기준 rolling timebox 정산을 사용한다. timebox 만료 청산은 장마감 개념이 아니라 포지션별 최대 보유시간 리스크 정책으로 유지할 것.
- 코인 보유종목 실시간 감시 복원은 스케줄러 startup 훅에만 의존하지 않는다. `CoinRealtimeMonitor.start()`에서 현재 보유종목을 먼저 desired set에 복원하고, 이후 order/asset update 및 holdings check에서 self-heal 되도록 유지할 것.
- 코인 시장 국면은 `BULL_RUN`/`BEAR_MARKET`/`CONSOLIDATION`/`ALTSEASON`이다. 주식의 `BULL`/`BEAR`/`SIDEWAYS`/`THEME`와 다르지만, `risk_manager.CRYPTO_RR_FLOOR`에서 두 체계 모두 매핑한다.
- 코인 Tier2 스트레스 테스트는 -10%/-7% (주식의 -5%/-3%보다 넓다). 코인 변동성 기준을 주식 수준으로 좁히지 말 것.
- `/admin-coin` 페이지는 주식 `/admin`과 완전 별도 SPA이다. API prefix는 `/api/v1/admin-coin/*`, SSE는 독립 `coin_sse_manager`를 사용한다. 주식 SSE와 코인 SSE를 공유하지 말 것.
- 코인 DB는 주식과 완전 분리된 10개 `coin_*` 테이블을 사용한다. 주식 테이블과 FK가 없으므로 코인 데이터가 주식 쿼리에 영향을 주지 않는다. 코인 활동 로그는 `CoinActivityLog`, 추천은 `CoinRecommendation` 테이블에 저장한다.
- 코인 활동 로그는 `activity_logger`가 `market_scope == "CRYPTO"`일 때 자동으로 `CoinActivityLog` 테이블과 `coin_sse_manager`로 분기한다. 주식 `sse_manager`와 코인 `coin_sse_manager`를 혼용하지 말 것.
- 보유 코인 현재가는 `BithumbClient._fetch_coin_prices()`가 벌크 ticker 조회(1 API call)로 해결한다. `get_account_balance()`와 `get_holdings()` 모두 현재가 기반 평가. 조회 실패 시 `avg_buy_price` 폴백.
- 코인 LLM Provider는 주식과 독립 설정 가능 (`CRYPTO_LLM_PROVIDER`). Tier1은 SCAN/ANALYSIS 프로필별 모델·effort 분리. Claude effort와 Codex reasoning effort 모두 코인 전용 환경변수로 제어.
- 코인 구현 수정 시 반드시 같이 확인할 테스트:
  - `is_crypto_market()` 정규화 (기존 KRX/US 회귀 포함)
  - 빗썸 캔들 oldest-first 정렬
  - 빗썸 rate limit 동작
  - `CRYPTO_*` 환경변수 독립성 (주식 설정 영향 없음)
  - 코인 DB 테이블 FK 정합성 (coin_assets ↔ coin_orders/holdings/analysis_results)

## 테스트 실행 규칙

- 테스트는 항상 저장소 루트에서 실행할 것.
- **OS/셸에 맞는 가상환경 Python을 직접 호출할 것.** bare `pytest`, `python -m pytest`, `python3 -m pytest` 사용 금지.
- 이 저장소는 가상환경이 분리돼 있을 수 있다:
  - WSL / Linux / macOS (bash, zsh): `venv/`
  - Windows (PowerShell, cmd): `.venv\\Scripts\\`
- WSL / Linux / macOS 실행:
  - 전체: `venv/bin/python -m pytest tests/ -v`
  - 파일: `venv/bin/python -m pytest tests/test_admin_coin_unittest.py -v`
  - 키워드: `venv/bin/python -m pytest tests/ -k watchlist -v`
  - 시작 확인: `test -x venv/bin/python && venv/bin/python --version`
- Windows 실행:
  - 전체: `.venv\\Scripts\\python.exe -m pytest tests\\ -v`
  - 파일: `.venv\\Scripts\\python.exe -m pytest tests\\test_admin_coin_unittest.py -v`
  - 시작 확인: `if exist .venv\\Scripts\\python.exe .venv\\Scripts\\python.exe --version`
- 가상환경이 없으면 OS에 맞게 생성/복구:
  - WSL / Linux / macOS: `python3 -m venv venv && venv/bin/pip install -r requirements.txt`
  - Windows: `py -m venv .venv && .venv\\Scripts\\pip.exe install -r requirements.txt`
- bash 세션에서는 `.venv/bin/python`을 가정하지 말 것. 이 저장소의 `.venv`는 Windows 레이아웃일 수 있다.
- Windows 세션에서는 `venv/bin/python`을 사용하지 말 것.
- 테스트가 실패하면 system Python으로 재시도하지 말고, 먼저 현재 세션이 WSL/Linux/mac인지 Windows인지 확인한 뒤 해당 가상환경 경로를 사용할 것.
