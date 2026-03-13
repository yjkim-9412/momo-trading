# Codex CLI 레퍼런스 조회

Codex CLI 관련 개발·디버깅 시 프로젝트 내 구현 코드와 공식 레포를 참조한다.

**공식 레포:** `openai/codex`

## 사용자 인자

$ARGUMENTS — 검색할 키워드 (예: session, exec, reasoning_effort, sandbox 등)

## 프로젝트 내 Codex 구현 파일

| 파일 | 역할 |
|------|------|
| `analysis/llm/codex_cli_provider.py` | Codex CLI subprocess 호출, 세션 관리, JSONL 파싱 |
| `analysis/llm/llm_factory.py` | LLM 라우팅 (Claude Code ↔ Codex CLI 전환) |
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

추론 강도 옵션: `minimal`, `low`, `medium`, `high`, `xhigh`

## Codex CLI 명령어 구조

### 단발 호출 (세션 없음)
```bash
codex exec --json --skip-git-repo-check --ephemeral --sandbox read-only \
  --model gpt-5.4 -o /tmp/output.txt -
```

### 새 세션 시작
```bash
codex exec --json --skip-git-repo-check --sandbox read-only \
  --model gpt-5.4 -o /tmp/output.txt -
```

### 세션 재개
```bash
codex exec resume --json --skip-git-repo-check \
  --model gpt-5.4 -o /tmp/output.txt {session_id} -
```

### Reasoning effort override
```bash
codex exec ... -c model_reasoning_effort=xhigh -
```

## 세션 관리 패턴

```
start_session()  → _session_enabled=True, ID=None
  ├─ 첫 호출 → sandbox read-only (세션 생성) → session_id 획득
  ├─ 이후 호출 → resume {session_id} (컨텍스트 유지)
  │
  pause_session() → _session_enabled=False (병렬 구간)
  │  ├─ 병렬 Tier1 분석들 → --ephemeral (독립 호출)
  │
  resume_session(sid) → _session_enabled=True (세션 복귀)
  │  └─ Tier2 리뷰 → resume {session_id}
  │
  end_session() → 세션 종료, ID 초기화
```

## JSONL 이벤트 스트림

stdout에서 JSONL 이벤트를 파싱:
- `thread.started` → `thread_id` (세션 ID)
- `item.completed` → `item.text` (응답 텍스트)
- `turn.completed` → `usage` (토큰 사용량)

결과 텍스트: `-o` 출력 파일 우선, 없으면 `item.completed` 텍스트 사용

## 실행 절차

"$ARGUMENTS" 키워드에 대해:

### Step 1: 프로젝트 내 코드 확인
먼저 프로젝트 내 관련 구현을 확인:
- `analysis/llm/codex_cli_provider.py` 에서 해당 기능 구현 확인
- `core/config.py` 에서 관련 설정 확인

### Step 2: 공식 레포에서 검색
```bash
gh search code --repo openai/codex "$ARGUMENTS" --limit 10
```

### Step 3: 공식 문서/소스 조회
```bash
gh api repos/openai/codex/contents/codex-cli --jq '.[] | "\(.type) \(.name)"'
gh api repos/openai/codex/contents/{파일경로} --jq '.content' | base64 -d
```

### Step 4: 결과 정리
- 프로젝트 내 구현과 공식 레포의 차이점 안내
- 필요한 설정 변경이나 코드 수정 제안
