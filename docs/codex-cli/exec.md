# codex exec - 비인터랙티브 실행

> `codex exec`는 Codex CLI를 비인터랙티브 모드로 실행하는 서브커맨드다.
> 프로그래밍 방식 호출, CI/CD 파이프라인, subprocess 통합에 사용된다.

## 기본 문법

```bash
codex exec [OPTIONS] [PROMPT]
codex exec [OPTIONS] -          # stdin에서 프롬프트 읽기
codex exec resume [SESSION_ID] [PROMPT]
codex exec review [OPTIONS]
```

`PROMPT` 위치에 `-`를 전달하면 stdin에서 프롬프트를 읽는다.

## 주요 옵션

### `--json`

stdout에 JSONL(JSON Lines) 형식으로 이벤트를 출력한다. 프로그래밍 방식 호출 시 필수.

```bash
codex exec --json - <<< "explain this code"
```

각 이벤트 타입에 대한 상세 설명은 [events.md](./events.md) 참조.

### `--model` / `-m`

사용할 모델을 지정한다. 기본값은 `o4-mini`.

```bash
codex exec --model gpt-4.1 --json - <<< "summarize README"
```

### `--sandbox` / `-s`

샌드박스 모드를 선택한다.

| 값 | 설명 |
|----|------|
| `read-only` (기본) | 파일 시스템 읽기 전용 |
| `workspace-write` | 작업 디렉토리 쓰기 허용 |
| `danger-full-access` | 모든 접근 허용 (위험) |

```bash
codex exec --sandbox read-only --json - <<< "analyze this repo"
```

### `--ephemeral`

세션 파일을 디스크에 저장하지 않는다. 일회성 호출에 적합.

```bash
codex exec --ephemeral --json - <<< "one-shot question"
```

### `--skip-git-repo-check`

Git 저장소가 아닌 곳에서도 실행을 허용한다.

```bash
codex exec --skip-git-repo-check --json - <<< "help me"
```

### `-o` / `--output-last-message`

에이전트의 마지막 메시지를 지정한 파일에 기록한다. JSONL 파싱 없이 결과 텍스트만 필요할 때 유용하다.

```bash
codex exec --json -o /tmp/result.txt - <<< "explain main.py"
cat /tmp/result.txt
```

### `-c` / `--config`

`~/.codex/config.toml`의 설정값을 오버라이드한다. dotted path로 중첩 값 지정 가능.

```bash
# model_reasoning_effort 오버라이드
codex exec -c model_reasoning_effort=high --json - <<< "deep analysis"

# 모델 변경
codex exec -c model=o3 --json - <<< "complex task"

# 여러 설정 동시 오버라이드
codex exec -c model=o3 -c model_reasoning_effort=xhigh --json - <<< "task"
```

값은 TOML 문법으로 파싱된다. 파싱 실패 시 문자열 리터럴로 처리된다.

```bash
# 배열 값
codex exec -c 'sandbox_permissions=["disk-full-read-access"]' --json -

# 중첩 키
codex exec -c shell_environment_policy.inherit=all --json -
```

### `--image` / `-i`

프롬프트에 이미지를 첨부한다.

```bash
codex exec --image screenshot.png --json - <<< "what's in this image?"
```

### `--output-schema`

모델의 최종 응답 형태를 JSON Schema로 지정한다.

```bash
codex exec --output-schema schema.json --json - <<< "analyze"
```

### `--full-auto`

`-a on-request --sandbox workspace-write`의 축약. 샌드박스 내 자동 실행.

```bash
codex exec --full-auto --json - <<< "fix all lint errors"
```

### `--cd` / `-C`

에이전트의 작업 디렉토리(working root)를 지정한다.

```bash
codex exec --cd /path/to/project --json - <<< "analyze this project"
```

### `--color`

출력 색상 설정. `always`, `never`, `auto` (기본).

## stdin 프롬프트 전달

프롬프트 인자 위치에 `-`를 사용하면 stdin에서 읽는다. 프로그래밍 방식 호출 시 권장.

```bash
# heredoc
codex exec --json - <<'EOF'
다음 코드를 분석하세요:
def hello(): print("world")
EOF

# 파이프
echo "explain this repo" | codex exec --json -

# Python subprocess (asyncio)
proc = await asyncio.create_subprocess_exec(
    "codex", "exec", "--json", "-o", output_path, "-",
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
)
stdout, _ = await proc.communicate(input=prompt.encode())
```

## 프로젝트 내 사용 패턴

`analysis/llm/codex_cli_provider.py`에서 세 가지 모드로 `codex exec`를 호출한다:

### 1. Ephemeral 모드 (세션 없음)

세션을 사용하지 않는 일회성 호출. `--ephemeral` 플래그 사용.

```bash
codex exec --json --skip-git-repo-check --ephemeral \
  --sandbox read-only -o /tmp/result.txt \
  --model gpt-5.4 -c model_reasoning_effort=medium -
```

### 2. 새 세션 시작

첫 호출 시 세션을 생성한다. `--ephemeral` 없이 호출하면 세션이 만들어진다.

```bash
codex exec --json --skip-git-repo-check \
  --sandbox read-only -o /tmp/result.txt \
  --model gpt-5.4 -c model_reasoning_effort=medium -
```

stdout JSONL의 `thread.started` 이벤트에서 `thread_id`를 획득한다.

### 3. 세션 재개

기존 세션에 추가 프롬프트를 전달한다.

```bash
codex exec resume --json --skip-git-repo-check \
  -o /tmp/result.txt \
  --model gpt-5.4 -c model_reasoning_effort=medium \
  {session_id} -
```

자세한 세션 관리는 [session.md](./session.md) 참조.

## review 서브커맨드

코드 리뷰를 비인터랙티브로 실행한다.

```bash
# 커밋되지 않은 변경 리뷰
codex exec review --uncommitted

# 특정 베이스 브랜치 대비 리뷰
codex exec review --base main

# 특정 커밋 리뷰
codex exec review --commit abc1234

# 커스텀 리뷰 지시
codex exec review "focus on security issues"
```

| 옵션 | 설명 |
|------|------|
| `--uncommitted` | staged, unstaged, untracked 변경 리뷰 |
| `--base BRANCH` | 지정 브랜치 대비 변경 리뷰 |
| `--commit SHA` | 특정 커밋의 변경 리뷰 |
| `--title TITLE` | 리뷰 요약에 표시할 커밋 제목 (`--commit` 필요) |

## CI/CD 예시

```yaml
# GitHub Actions
- name: Codex 분석 실행
  run: |
    npm install -g @openai/codex
    export OPENAI_API_KEY="${{ secrets.OPENAI_KEY }}"
    codex exec --json --ephemeral --sandbox read-only \
      -o result.txt - <<< "analyze code quality"
    cat result.txt
```
