# Codex CLI 레퍼런스

> 출처: [openai/codex](https://github.com/openai/codex) (Rust 구현 기준)
>
> Codex CLI는 OpenAI에서 제공하는 로컬 코딩 에이전트로, 터미널에서 실행된다.

## 설치

```bash
# npm
npm install -g @openai/codex

# Homebrew (macOS)
brew install --cask codex

# 바이너리 직접 다운로드
# https://github.com/openai/codex/releases/latest
# macOS Apple Silicon: codex-aarch64-apple-darwin.tar.gz
# macOS x86_64:        codex-x86_64-apple-darwin.tar.gz
# Linux x86_64:        codex-x86_64-unknown-linux-musl.tar.gz
# Linux arm64:         codex-aarch64-unknown-linux-musl.tar.gz
```

## 인증

```bash
# ChatGPT 계정 로그인 (Plus, Pro, Team, Edu, Enterprise)
codex  # 실행 후 "Sign in with ChatGPT" 선택

# API 키 사용
export OPENAI_API_KEY="your-api-key-here"
```

## 기본 사용법

```bash
# 인터랙티브 REPL
codex

# 프롬프트 전달
codex "explain this codebase to me"

# Full Auto 모드 (샌드박스 내 자동 실행)
codex --approval-mode full-auto "create the fanciest todo-list app"

# 비인터랙티브 (quiet 모드)
codex -q --json "explain utils.ts"
```

## CLI 명령어 요약

| 명령어 | 설명 | 예시 |
|--------|------|------|
| `codex` | 인터랙티브 REPL | `codex` |
| `codex "..."` | 프롬프트와 함께 REPL 시작 | `codex "fix lint errors"` |
| `codex exec` | 비인터랙티브 실행 | `codex exec --json -` |
| `codex exec resume` | 기존 세션 재개 | `codex exec resume {session_id} -` |
| `codex exec review` | 코드 리뷰 실행 | `codex exec review --uncommitted` |
| `codex -q "..."` | Quiet 모드 (비인터랙티브) | `codex -q --json "explain utils.ts"` |

### 주요 플래그

| 플래그 | 단축 | 설명 |
|--------|------|------|
| `--model` | `-m` | 사용할 모델 지정 |
| `--json` | - | stdout에 JSONL 이벤트 출력 |
| `--sandbox` | `-s` | 샌드박스 모드 (`read-only`, `workspace-write`, `danger-full-access`) |
| `--ephemeral` | - | 세션 파일 저장 안 함 |
| `--skip-git-repo-check` | - | Git 저장소 밖에서도 실행 허용 |
| `-o` | - | 마지막 메시지를 파일로 출력 |
| `-c` | `--config` | 설정 오버라이드 (`key=value`) |
| `--approval-mode` | `-a` | 승인 모드 (`suggest`, `auto-edit`, `full-auto`) |

## 보안 모델

| 모드 | 자동 허용 | 승인 필요 |
|------|-----------|-----------|
| **Suggest** (기본) | 파일 읽기 | 모든 파일 쓰기, 모든 셸 명령 |
| **Auto Edit** | 파일 읽기 + 쓰기 | 모든 셸 명령 |
| **Full Auto** | 파일 읽기/쓰기, 셸 명령 (네트워크 차단) | - |

## 설정 파일

`~/.codex/config.toml` (TOML 형식):

```toml
model = "o4-mini"

[model_providers.openai]
name = "OpenAI"
base_url = "https://api.openai.com/v1"
env_key = "OPENAI_API_KEY"
```

## AGENTS.md

Codex에 커스텀 지시를 제공하는 파일. 다음 위치에서 자동 로드:

1. `~/.codex/AGENTS.md` - 개인 글로벌 설정
2. `AGENTS.md` (저장소 루트) - 프로젝트 공유 설정
3. `AGENTS.md` (현재 디렉토리) - 하위 폴더 설정

`--no-project-doc` 또는 `CODEX_DISABLE_PROJECT_DOC=1`로 비활성화.

## 하위 문서

| 문서 | 설명 |
|------|------|
| [exec.md](./exec.md) | `codex exec` 명령어 상세 옵션 |
| [session.md](./session.md) | 세션 관리 (시작, 재개, ephemeral) |
| [events.md](./events.md) | JSONL 이벤트 스트림 형식 |
| [models.md](./models.md) | 모델 및 설정 (reasoning_effort 등) |

## 프로젝트 내 구현

이 프로젝트에서는 `analysis/llm/codex_cli_provider.py`에서 Codex CLI를 subprocess로 호출한다.

- 환경변수: `CODEX_CLI_PATH`, `CODEX_MODEL`, `CODEX_REASONING_EFFORT` 등
- 기본 모델: `gpt-5.4` (`core/config.py`의 `CODEX_MODEL`)
- 세션 관리: scope(KRX/CRYPTO) + phase(cycle/report) 기반 멀티 세션
- 자세한 내용은 각 하위 문서 참조
