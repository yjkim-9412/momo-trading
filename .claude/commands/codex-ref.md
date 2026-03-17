# Codex CLI 레퍼런스 조회

Codex CLI 관련 개발·디버깅 시 로컬 레퍼런스와 프로젝트 내 구현을 참조한다.

## 사용자 인자

$ARGUMENTS — 검색할 키워드 (예: session, exec, reasoning_effort, sandbox, events 등)

## 로컬 레퍼런스

- 인덱스: `docs/codex-cli/README.md` (설치, 인증, 기본 사용법, CLI 명령어 요약)
- exec 명령어: `docs/codex-cli/exec.md` (옵션, stdin 전달, review 서브커맨드)
- 세션 관리: `docs/codex-cli/session.md` (resume, ephemeral, 멀티세션)
- 이벤트 스트림: `docs/codex-cli/events.md` (JSONL 형식, 이벤트 타입)
- 모델/설정: `docs/codex-cli/models.md` (모델 목록, reasoning_effort, verbosity)

## 프로젝트 내 Codex 구현 파일

| 파일 | 역할 |
|------|------|
| `analysis/llm/codex_cli_provider.py` | Codex CLI subprocess 호출, 세션 관리, JSONL 파싱 |
| `analysis/llm/llm_factory.py` | LLM 라우팅 (Claude Code / Codex CLI 전환) |
| `analysis/llm/base.py` | `LLMProviderProtocol`, `LLMSessionProtocol` 정의 |
| `core/config.py` | `CODEX_MODEL`, `CODEX_REASONING_EFFORT` 등 환경변수 |
| `trading/enums.py` | `LLMProvider.CODEX_CLI`, `LLMTier.TIER1/TIER2` |

## 환경변수 (.env)

```bash
LLM_PROVIDER=CODEX_CLI          # 또는 CLAUDE_CODE
CODEX_MODEL=gpt-5.4             # 기본 모델
CODEX_MODEL_TIER1=              # Tier1 전용 (비어있으면 CODEX_MODEL 사용)
CODEX_MODEL_TIER2=              # Tier2 전용
CODEX_REASONING_EFFORT=         # 기본 추론 강도
CODEX_REASONING_EFFORT_TIER1=   # Tier1 추론 강도
CODEX_REASONING_EFFORT_TIER2=xhigh  # Tier2 추론 강도 (최고)
CODEX_CLI_PATH=                 # CLI 경로 (비어있으면 자동 탐색)
```

reasoning_effort 옵션: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`

## 실행 절차

"$ARGUMENTS" 키워드에 대해:

### Step 1: 로컬 레퍼런스에서 검색
```bash
find docs/codex-cli -name "*.md" | xargs grep -li "$ARGUMENTS"
```
`docs/codex-cli/README.md` 인덱스에서 해당 항목을 찾아 상세 문서를 읽는다.

### Step 2: 상세 문서 조회
해당 문서를 Read 도구로 읽어 옵션, 이벤트 형식, 설정 등을 확인한다.

### Step 3: 프로젝트 내 구현 확인
`analysis/llm/codex_cli_provider.py`에서 해당 기능의 구현을 확인하고, 로컬 레퍼런스와 일치하는지 검증한다.

### Step 4: (필요시) 공식 레포 최신 확인
로컬 문서로 부족할 경우에만 GitHub 레포를 직접 조회:
```bash
gh api repos/openai/codex/contents/{path} --jq '.content' | base64 -d
```
