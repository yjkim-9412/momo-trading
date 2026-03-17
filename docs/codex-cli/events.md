# Codex CLI JSONL 이벤트 스트림

> `codex exec --json` 사용 시 stdout에 JSONL(JSON Lines) 형식으로 이벤트가 출력된다.
> 각 줄은 독립적인 JSON 객체이며, `type` 필드로 이벤트를 구분한다.

## 이벤트 형식

```
{"type":"thread.started","thread_id":"01968b2f-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}
{"type":"turn.started"}
{"type":"item.started","item":{"id":"...","type":"reasoning","text":""}}
{"type":"item.completed","item":{"id":"...","type":"agent_message","text":"분석 결과..."}}
{"type":"turn.completed","usage":{"input_tokens":1234,"cached_input_tokens":500,"output_tokens":567}}
```

## 주요 이벤트 타입

### `thread.started`

세션(thread)이 시작될 때 첫 이벤트로 발생한다.

```json
{
  "type": "thread.started",
  "thread_id": "01968b2f-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `thread_id` | string | 세션 UUID. `resume` 시 이 값을 사용 |

### `turn.started`

새 프롬프트가 모델로 전송되면 발생한다. 하나의 turn은 프롬프트 처리 전체를 포함.

```json
{
  "type": "turn.started"
}
```

### `turn.completed`

turn 처리가 완료되면 발생한다. 토큰 사용량 정보를 포함.

```json
{
  "type": "turn.completed",
  "usage": {
    "input_tokens": 1234,
    "cached_input_tokens": 500,
    "output_tokens": 567
  }
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `usage.input_tokens` | int | 입력 토큰 수 |
| `usage.cached_input_tokens` | int | 캐시된 입력 토큰 수 |
| `usage.output_tokens` | int | 출력 토큰 수 |

### `turn.failed`

turn 처리 중 오류가 발생하면 발생한다.

```json
{
  "type": "turn.failed",
  "error": {
    "message": "API request failed: 429 rate limit exceeded"
  }
}
```

### `item.started`

새 아이템이 thread에 추가될 때 발생한다. 아이템은 보통 "in progress" 상태.

```json
{
  "type": "item.started",
  "item": {
    "id": "item_001",
    "type": "reasoning",
    "text": ""
  }
}
```

### `item.updated`

아이템의 상태가 업데이트될 때 발생한다.

```json
{
  "type": "item.updated",
  "item": {
    "id": "item_001",
    "type": "command_execution",
    "command": "ls -la",
    "aggregated_output": "total 48\n...",
    "exit_code": null,
    "status": "in_progress"
  }
}
```

### `item.completed`

아이템이 최종 상태(성공/실패)에 도달하면 발생한다.

```json
{
  "type": "item.completed",
  "item": {
    "id": "item_002",
    "type": "agent_message",
    "text": "분석 결과입니다..."
  }
}
```

### `error`

스트림 수준의 복구 불가능한 오류.

```json
{
  "type": "error",
  "message": "Connection lost"
}
```

## 아이템 타입 (item.type)

| 타입 | 설명 | 주요 필드 |
|------|------|-----------|
| `agent_message` | 에이전트 응답 텍스트 | `text` |
| `reasoning` | 추론 요약 | `text` |
| `command_execution` | 셸 명령 실행 | `command`, `aggregated_output`, `exit_code`, `status` |
| `file_change` | 파일 변경 | `changes[]`, `status` |
| `mcp_tool_call` | MCP 도구 호출 | `server`, `tool`, `arguments`, `result`, `status` |
| `collab_tool_call` | 협업 도구 호출 | `tool`, `sender_thread_id`, `receiver_thread_ids`, `status` |
| `web_search` | 웹 검색 | `query`, `action` |
| `todo_list` | 할 일 목록 | `items[]` (각 항목: `text`, `completed`) |
| `error` | 비치명적 오류 | `message` |

### agent_message 상세

에이전트의 자연어 응답 또는 구조화된 출력(JSON).

```json
{
  "id": "msg_001",
  "type": "agent_message",
  "text": "분석 결과입니다. 다음과 같은 패턴이 발견되었습니다..."
}
```

### command_execution 상세

모델이 실행한 셸 명령의 추적 정보.

```json
{
  "id": "cmd_001",
  "type": "command_execution",
  "command": "python3 test.py",
  "aggregated_output": "test passed\n",
  "exit_code": 0,
  "status": "completed"
}
```

| status | 설명 |
|--------|------|
| `in_progress` | 명령 실행 중 |
| `completed` | 정상 완료 |
| `failed` | 실행 실패 |
| `declined` | 사용자가 거부 |

### file_change 상세

에이전트의 파일 변경 작업.

```json
{
  "id": "fc_001",
  "type": "file_change",
  "changes": [
    {"path": "src/main.py", "kind": "update"},
    {"path": "src/utils.py", "kind": "add"}
  ],
  "status": "completed"
}
```

| kind | 설명 |
|------|------|
| `add` | 새 파일 생성 |
| `delete` | 파일 삭제 |
| `update` | 기존 파일 수정 |

## 프로젝트 내 JSONL 파싱

`CodexCLIProvider.parse_event_stream()`이 JSONL 이벤트를 파싱한다.

```python
@classmethod
def parse_event_stream(cls, raw_stdout: str) -> dict[str, Any]:
    session_id = None
    usage: dict[str, Any] = {}
    fallback_text = ""

    for raw_line in raw_stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue

        event = json.loads(line)
        event_type = event.get("type")

        if event_type == "thread.started":
            session_id = event.get("thread_id")

        elif event_type == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                fallback_text = item["text"]

        elif event_type == "turn.completed":
            usage = event.get("usage", {})

    return {
        "session_id": session_id,
        "usage": usage,
        "fallback_text": fallback_text,
    }
```

### 응답 텍스트 우선순위

1. `-o` 플래그로 지정된 출력 파일 (에이전트의 마지막 메시지)
2. `item.completed`의 `agent_message.text` (fallback)

```python
@staticmethod
def _read_result_text(output_path: str, fallback_text: str) -> str:
    try:
        with open(output_path, encoding="utf-8") as file:
            result_text = file.read().strip()
        if result_text:
            return result_text
    except OSError:
        pass
    return fallback_text.strip()
```

## Usage 추적

`turn.completed` 이벤트의 `usage` 필드에서 토큰 사용량을 추적한다.

```python
# usage 구조
{
    "input_tokens": 1234,
    "cached_input_tokens": 500,
    "output_tokens": 567
}

# 누적 추적
self.cumulative_usage["total_input_tokens"] += input_tokens
self.cumulative_usage["total_output_tokens"] += output_tokens
self.cumulative_usage["total_cached_input_tokens"] += cached_input_tokens
```
