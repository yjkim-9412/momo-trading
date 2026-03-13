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
