# Codex CLI 세션 관리

> Codex CLI는 대화 히스토리를 유지하는 세션(thread) 기능을 제공한다.
> 세션을 사용하면 이전 컨텍스트를 유지한 채 후속 프롬프트를 전달할 수 있다.

## 세션 모드 요약

| 모드 | 설명 | `--ephemeral` | 세션 파일 |
|------|------|:---:|:---:|
| **Ephemeral** | 세션 없음, 일회성 호출 | O | 저장 안 됨 |
| **새 세션** | 첫 호출 시 세션 생성 | X | `~/.codex/sessions/` |
| **세션 재개** | 기존 세션에 추가 프롬프트 | X | 기존 파일 사용 |

## 새 세션 시작

`--ephemeral` 없이 `codex exec`를 호출하면 새 세션이 생성된다.

```bash
codex exec --json --skip-git-repo-check \
  --sandbox read-only -o /tmp/result.txt \
  --model o4-mini -
```

### 세션 ID 획득

`--json` 플래그 사용 시, stdout의 첫 JSONL 이벤트가 `thread.started`이며 여기서 세션 ID를 얻는다.

```json
{"type":"thread.started","thread_id":"01968b2f-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}
```

Python 예시:

```python
for line in raw_stdout.splitlines():
    if not line.strip().startswith("{"):
        continue
    event = json.loads(line)
    if event.get("type") == "thread.started":
        session_id = event["thread_id"]
        break
```

## 세션 재개 (resume)

기존 세션에 추가 프롬프트를 전달한다.

```bash
codex exec resume [OPTIONS] <SESSION_ID> [PROMPT]
codex exec resume [OPTIONS] <SESSION_ID> -    # stdin에서 프롬프트
```

### resume 옵션

| 옵션 | 설명 |
|------|------|
| `SESSION_ID` | 재개할 세션 UUID 또는 thread name |
| `--last` | 가장 최근 세션 자동 선택 |
| `--all` | 모든 세션 표시 (cwd 필터 무시) |
| `--image` / `-i` | 이미지 첨부 |
| `PROMPT` | 후속 프롬프트 (`-`면 stdin) |

### resume 사용 예시

```bash
# 특정 세션 재개
codex exec resume --json --skip-git-repo-check \
  -o /tmp/result.txt --model o4-mini \
  01968b2f-xxxx-xxxx-xxxx-xxxxxxxxxxxx -

# 가장 최근 세션 재개
codex exec resume --last --json "continue analysis"

# --last와 프롬프트
codex exec resume --last --json - <<< "additional context"
```

### `--last` 동작

`--last` 사용 시 `SESSION_ID` 위치의 인자가 프롬프트로 해석된다:

```bash
# SESSION_ID 없이 --last 사용
codex exec resume --last "this is a prompt, not a session id"
```

## Ephemeral 모드

세션 파일을 디스크에 저장하지 않는 일회성 모드.

```bash
codex exec --ephemeral --json --skip-git-repo-check \
  --sandbox read-only -o /tmp/result.txt -
```

- `thread.started` 이벤트는 여전히 발생하지만, 세션은 재개 불가
- 민감 데이터 처리나 단순 질문에 적합

## 세션 파일 저장 위치

- 기본 경로: `~/.codex/sessions/`
- JSONL 형식으로 대화 히스토리 저장
- `CODEX_SQLITE_HOME` 환경변수로 SQLite 상태 DB 위치 변경 가능

## 프로젝트 내 세션 관리

`analysis/llm/codex_cli_provider.py`의 `CodexCLIProvider`는 scope + phase 기반으로 세션을 관리한다.

### 세션 생명주기

```
start_session(scope, phase)
  -> generate() : 새 세션 생성, thread.started에서 session_id 획득
  -> generate() : session_id로 resume 호출
  -> generate() : session_id로 resume 호출
  ...
pause_session(scope, phase)  : 병렬 구간에서 세션 일시 중지
resume_session(session_id, scope, phase)  : 세션 재개
end_session(scope, phase)    : 세션 종료
```

### 세션 키

scope + phase 조합으로 독립 세션을 관리한다:

| scope | phase | 용도 |
|-------|-------|------|
| `KRX` | `cycle` | 국내 주식 분석 사이클 |
| `KRX` | `report` | 국내 주식 리포트 |
| `CRYPTO` | `cycle` | 코인 분석 사이클 |
| `CRYPTO` | `report` | 코인 리포트 |

### 세션 상태 관리

```python
# 클래스 변수로 세션 상태 관리
_session_states: dict[tuple[str, str], dict[str, Any]] = {}

# 각 세션 상태
{
    "active_session_id": "01968b2f-...",  # 현재 세션 ID
    "session_enabled": True,              # 세션 사용 여부
    "session_initialized": False,         # 첫 호출 완료 여부
}
```

### 명령 구성 로직

1. **세션 비활성**: `--ephemeral` 사용, 매 호출 독립
2. **세션 활성 + 미초기화**: `--ephemeral` 없이 호출, `thread.started`에서 ID 획득
3. **세션 활성 + 초기화 완료**: `resume {session_id}`로 호출

### 직렬화

세션 재개 시 동시 호출을 방지하기 위해 `asyncio.Lock`으로 직렬화한다:

```python
_session_locks: dict[tuple[str, str], asyncio.Lock] = {}

# generate() 내부
if state["session_enabled"] and scope is not None:
    async with self._get_lock(scope, phase):
        return await self._execute(cmd, actual_prompt, output_path, state)
```
