# MOMO Trading — Claude Code 지침

## 증권사 API 참조

KIS Open API 구현, 디버깅, 새 API 연동 시 반드시 `/kis-api-ref` 스킬을 사용하여 공식 샘플코드를 참조할 것.

- 공식 레포: https://github.com/koreainvestment/open-trading-api
- 스킬 사용법: `/kis-api-ref {검색어}` (예: `/kis-api-ref 주문`, `/kis-api-ref volume_rank`)
- LLM용 샘플은 `examples_llm/` 폴더에, 사용자용 통합 예제는 `examples_user/` 폴더에 위치

## Codex CLI 참조

Codex CLI 관련 개발·디버깅 시 `/codex-ref` 스킬을 사용하여 프로젝트 내 구현과 공식 레포를 참조할 것.

- 공식 레포: https://github.com/openai/codex
- 스킬 사용법: `/codex-ref {검색어}` (예: `/codex-ref session`, `/codex-ref reasoning_effort`)
- 프로젝트 내 구현: `analysis/llm/codex_cli_provider.py`, `core/config.py`

## 분리된 장 구조

- 운영 장은 `KRX` 와 `US` 두 runtime scope로 분리한다.
- `market_scope` 는 스케줄, 리포트, 리스크, LLM 세션, 실시간 구독을 나누는 기준이다.
- 실제 주문/시세용 `market` 코드는 `KRX`, `NASDAQ`, `NYSE`, `AMEX` 같은 거래소 코드를 그대로 유지한다.
- 미국장은 거래소 단위로 주문하지만, 장중 런타임과 장후 리뷰는 `US` scope로 묶어 처리한다.

## 미국장 구현 회고

- 미국 프리마켓은 `US_PREMARKET_ENABLED=true`일 때 정식 분석 세션이다. 스케줄 기준은 `03:50 ET` 준비, `04:05 ET` 오픈 스캔, `11:00/13:00 ET` 장중 재스캔이며, 미국장 AI 판단은 이 텀에 맞춰 유지해야 한다. 미국장 스케줄은 `scheduler/scheduler.py` 프로필을 기준으로 사용하고, 정규장 시간 하드코딩을 다시 넣지 말 것.
- 미국장 API 실패 원인은 `프리마켓 조회 불가`가 아니라 `해외 시세 burst 호출`이었다. 종목 병렬 분석과 종목 내부 `현재가 + 일봉 + 분봉` 동시 조회가 겹치면 KIS가 `초당 거래건수를 초과하였습니다.`를 반환할 수 있다. 해외 quote 경로는 공통 limiter/직렬화 경로를 유지할 것.
- KIS 해외 `dailyprice`와 `inquire-time-itemchartprice`는 최신순 응답일 수 있다. 지표 계산기는 `oldest -> newest`를 가정하므로, 미국장 시세는 `trading/mcp_client.py`에서 정렬하고 `agent/trading_agent.py`에서 DataFrame 단계에서 다시 정렬하는 이중 방어를 유지할 것.
- 미국 단기매매는 실시간 현재가와 같은 가격 축이 우선이다. 해외 일봉은 `MODP="0"` 비수정주가 기준으로 맞추고, 수정주가 일봉과 실시간 현재가를 섞지 말 것. 단기 AI 분석 경로에서 `MODP="1"` 복귀는 금지한다.
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
