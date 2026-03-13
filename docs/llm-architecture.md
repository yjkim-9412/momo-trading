# LLM Provider Architecture

## 1. 전체 시스템 아키텍처

```mermaid
flowchart TB
    subgraph HOST["Host OS - macOS/Linux"]
        CLAUDE_CLI["claude CLI"]
        CODEX_CLI["codex CLI"]
    end

    subgraph DOCKER["Docker Compose"]
        subgraph MOMO["momo-trading :9000"]
            MAIN["main.py - FastAPI"]
            FACTORY["LLMFactory"]
            T1["TIER1 Provider"]
            T2["TIER2 Provider"]
            CONFIG["Settings"]
        end
        subgraph KIS["kis-mcp :3000"]
            MCP["KIS MCP Server"]
        end
    end

    MAIN --> FACTORY
    FACTORY --> T1
    FACTORY --> T2
    CONFIG -.-> FACTORY
    T1 -->|subprocess| CLAUDE_CLI
    T1 -->|subprocess| CODEX_CLI
    T2 -->|subprocess| CLAUDE_CLI
    T2 -->|subprocess| CODEX_CLI
    MAIN <-->|HTTP/SSE| MCP
```

> **참고:** Docker 컨테이너 내부에는 Claude/Codex CLI가 설치되어 있지 않음.
> Provider가 `subprocess`로 CLI를 호출하므로, 호스트에 CLI가 설치되어 있어야 동작.

---

## 2. LLM Provider 클래스 구조

```mermaid
classDiagram
    class LLMProviderProtocol {
        <<Protocol>>
        +provider: LLMProvider
        +display_name: str
        +tier: LLMTier
        +configured_model: str
        +configured_reasoning_effort: str | None
        +model_id: str
        +generate(prompt, system_prompt) str
        +is_available() bool
    }

    class LLMSessionProtocol {
        <<Protocol>>
        +start_session()$ str | None
        +end_session()$ str | None
        +pause_session()$ str | None
        +resume_session(session_id)$
        +get_session_id()$ str | None
        +get_usage_snapshot()$ dict
        +get_usage_report()$ dict
    }

    class ClaudeCodeProvider {
        -_active_session_id: str | None
        -_session_initialized: bool
        -_session_lock: asyncio.Lock
        -cumulative_usage: dict
        -_tier: LLMTier
        -_model: str
        -_reasoning_effort: str
        +generate(prompt, system_prompt) str
        +is_available() bool
        +start_session()$ str
        +end_session()$ str | None
        +pause_session()$ str | None
        +resume_session(session_id)$
        -_find_claude() str | None
        -_execute(cmd, prompt) str
        -_track_usage(resp)
    }

    class CodexCLIProvider {
        -_active_session_id: str | None
        -_session_enabled: bool
        -_session_initialized: bool
        -_session_lock: asyncio.Lock
        -cumulative_usage: dict
        -_tier: LLMTier
        -_model: str
        -_reasoning_effort: str
        +generate(prompt, system_prompt) str
        +is_available() bool
        +start_session()$ str | None
        +end_session()$ str | None
        +pause_session()$ str | None
        +resume_session(session_id)$
        -_find_codex() str | None
        -_build_command(codex_path) tuple
        -_execute(cmd, prompt, output_path) str
        -parse_event_stream(raw_stdout)$ dict
        -_track_usage(usage)
    }

    class LLMFactory {
        -_selected_provider: LLMProvider
        -_providers: dict~LLMTier, Provider~
        +generate(prompt, tier, system_prompt) tuple
        +generate_tier1(prompt, system_prompt) tuple
        +generate_tier2(prompt, system_prompt) tuple
        +start_session() str | None
        +end_session() str | None
        +pause_session() str | None
        +resume_session(session_id)
        +get_llm_usage() dict
        +get_llm_status() dict
    }

    class LLMTier {
        <<Enum>>
        TIER1 = "빠른 스캔/선별"
        TIER2 = "프리미엄 최종 검토"
    }

    class LLMProvider {
        <<Enum>>
        CLAUDE_CODE
        CODEX_CLI
    }

    LLMProviderProtocol <|.. ClaudeCodeProvider
    LLMProviderProtocol <|.. CodexCLIProvider
    LLMSessionProtocol <|.. ClaudeCodeProvider
    LLMSessionProtocol <|.. CodexCLIProvider
    LLMFactory --> LLMProviderProtocol : routes to
    LLMFactory --> LLMTier : uses
    LLMFactory --> LLMProvider : selects by
```

---

## 3. Provider 라우팅 흐름

```mermaid
flowchart LR
    ENV[".env LLM_PROVIDER"]
    SETTINGS["Settings.llm_provider"]
    FACTORY["LLMFactory"]

    subgraph CLAUDE["Claude Code"]
        CT1["TIER1: haiku / medium"]
        CT2["TIER2: sonnet / high"]
    end

    subgraph CODEX["Codex CLI"]
        XT1["TIER1: gpt-5.4 / default"]
        XT2["TIER2: gpt-5.4 / xhigh"]
    end

    ENV --> SETTINGS
    SETTINGS --> FACTORY
    FACTORY -->|CLAUDE_CODE| CLAUDE
    FACTORY -->|CODEX_CLI| CODEX
```

---

## 4. 트레이딩 사이클 세션 시퀀스

```mermaid
sequenceDiagram
    participant Scheduler as Scheduler<br/>(main.py)
    participant Factory as LLMFactory
    participant Provider as Claude/Codex Provider
    participant CLI as CLI subprocess<br/>(claude/codex)

    Note over Scheduler,CLI: 트레이딩 사이클 시작

    Scheduler->>Factory: start_session()
    Factory->>Provider: start_session()
    Provider-->>Factory: session_id

    rect rgb(230, 245, 230)
        Note over Scheduler,CLI: Phase 1 — 시장 스캔 (TIER1)
        Scheduler->>Factory: generate_tier1(시장 스캔 프롬프트)
        Factory->>Provider: generate(prompt) [TIER1]
        Provider->>CLI: subprocess exec<br/>--session-id {id}
        CLI-->>Provider: JSON/JSONL 응답
        Provider-->>Factory: 스캔 결과 텍스트
        Factory-->>Scheduler: (결과, provider명)
    end

    rect rgb(255, 243, 224)
        Note over Scheduler,CLI: Phase 2 — 병렬 종목 분석 (세션 일시정지)
        Scheduler->>Factory: pause_session()
        Factory->>Provider: pause_session()
        Provider-->>Factory: session_id (보존)

        par 종목 A 분석
            Scheduler->>Factory: generate_tier1(종목A 분석)
            Factory->>Provider: generate(prompt) [no session]
            Provider->>CLI: subprocess exec<br/>--no-session / --ephemeral
            CLI-->>Provider: 응답
        and 종목 B 분석
            Scheduler->>Factory: generate_tier1(종목B 분석)
            Factory->>Provider: generate(prompt) [no session]
            Provider->>CLI: subprocess exec<br/>--no-session / --ephemeral
            CLI-->>Provider: 응답
        end
    end

    rect rgb(232, 234, 246)
        Note over Scheduler,CLI: Phase 3 — 최종 검토 (TIER2, 세션 재개)
        Scheduler->>Factory: resume_session(session_id)
        Factory->>Provider: resume_session(session_id)

        Scheduler->>Factory: generate_tier2(최종 검토 프롬프트)
        Factory->>Provider: generate(prompt) [TIER2]
        Provider->>CLI: subprocess exec<br/>--resume {id}
        CLI-->>Provider: JSON/JSONL 응답
        Provider-->>Factory: 최종 리포트
        Factory-->>Scheduler: (결과, provider명)
    end

    Scheduler->>Factory: end_session()
    Factory->>Provider: end_session()

    Note over Scheduler,CLI: 트레이딩 사이클 종료
```

---

## 5. CLI 호출 상세

### Claude Code CLI

```mermaid
flowchart LR
    PROMPT["Prompt via stdin"]

    BASE["claude -p --output-format json --max-turns 1"]
    MODEL["--model haiku or sonnet"]
    EFFORT["--effort medium or high"]
    SESSION{"Session state?"}
    NEW["--session-id uuid"]
    RESUME["--resume uuid"]
    NONE["--no-session-persistence"]

    JSON["JSON response"]

    PROMPT --> BASE --> MODEL --> EFFORT --> SESSION
    SESSION -->|first call| NEW --> JSON
    SESSION -->|subsequent| RESUME --> JSON
    SESSION -->|no session| NONE --> JSON
```

### Codex CLI

```mermaid
flowchart LR
    PROMPT["Prompt via stdin"]

    BASE["codex exec --json --skip-git-repo-check -o result.txt"]
    MODEL["--model gpt-5.4"]
    EFFORT["-c model_reasoning_effort=<profile/default>"]
    SESSION{"Session state?"}
    NEW["--sandbox read-only"]
    RESUME["resume thread_id"]
    NONE["--ephemeral --sandbox read-only"]

    JSONL["JSONL event stream"]

    PROMPT --> BASE --> MODEL --> EFFORT --> SESSION
    SESSION -->|first call| NEW --> JSONL
    SESSION -->|subsequent| RESUME --> JSONL
    SESSION -->|no session| NONE --> JSONL
```

---

## 6. CLI 경로 탐색 순서

```mermaid
flowchart TD
    START["get_llm_cli_path"]
    CHECK_CONFIG{"Config path set?"}
    USE_CONFIG["Use configured path"]
    CHECK_PATH{"shutil.which found?"}
    USE_PATH["Use PATH result"]
    CHECK_CANDIDATES{"Candidate paths?"}
    USE_CANDIDATE["Use candidate path"]
    NOT_FOUND["None - CLI not found"]

    START --> CHECK_CONFIG
    CHECK_CONFIG -->|Yes| USE_CONFIG
    CHECK_CONFIG -->|No| CHECK_PATH
    CHECK_PATH -->|Found| USE_PATH
    CHECK_PATH -->|Not found| CHECK_CANDIDATES
    CHECK_CANDIDATES -->|Found| USE_CANDIDATE
    CHECK_CANDIDATES -->|Not found| NOT_FOUND
```

탐색 후보 경로:
- `/opt/homebrew/bin/claude` | `/opt/homebrew/bin/codex`
- `/usr/local/bin/claude` | `/usr/local/bin/codex`
- `~/.local/bin/claude` | `~/.local/bin/codex`
- `~/.npm-global/bin/claude` | `~/.npm-global/bin/codex`
