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

## 공통 운영 지침

분리된 장 구조, 스케줄 구조, 미국장/코인 구현 회고, 테스트 실행 규칙은 아래 문서에 정의되어 있다. 반드시 참조할 것.

- `docs/shared-guidelines.md`
