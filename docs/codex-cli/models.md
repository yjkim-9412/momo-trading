# Codex CLI 모델 및 설정

> Codex CLI에서 사용 가능한 모델과 reasoning_effort 등 설정을 정리한다.

## 기본 모델

Codex CLI의 기본 모델은 `o4-mini`이다. `--model` 플래그 또는 `config.toml`의 `model` 키로 변경 가능.

```bash
# CLI 플래그
codex exec --model gpt-4.1 --json - <<< "task"

# config.toml
model = "o4-mini"

# -c 오버라이드
codex exec -c model=o3 --json -
```

## 지원 모델

OpenAI Responses API를 지원하는 모든 모델을 사용할 수 있다.

| 모델 | 특성 | 용도 |
|------|------|------|
| `o4-mini` | Codex 기본 모델, 빠르고 경제적 | 일반 작업 |
| `o3` | 고성능 추론 모델 | 복잡한 분석 |
| `gpt-4.1` | 범용 모델 | 코드 생성/설명 |
| `gpt-5.4` | 최신 범용 모델 | 고품질 분석 (프로젝트 기본) |

> 최신 지원 모델 목록은 [OpenAI Platform](https://platform.openai.com/docs/models)을 참조.

## Reasoning Effort

모델의 추론 깊이를 조절한다. 추론 모델(o-시리즈)에서 효과적이다.

### 사용 가능한 값

| 값 | 설명 |
|----|------|
| `none` | 추론 없음 |
| `minimal` | 최소 추론 |
| `low` | 낮은 추론 |
| `medium` (기본) | 중간 추론 |
| `high` | 높은 추론 |
| `xhigh` | 최대 추론 |

### 설정 방법

```bash
# -c 오버라이드 (가장 일반적)
codex exec -c model_reasoning_effort=high --json -

# config.toml
model_reasoning_effort = "medium"
```

### Reasoning Summary

추론 과정 요약을 제어한다.

| 값 | 설명 |
|----|------|
| `auto` (기본) | 자동 |
| `concise` | 간결한 요약 |
| `detailed` | 상세 요약 |
| `none` | 요약 비활성 |

### Plan Mode Reasoning Effort

Plan 모드 전용 reasoning effort 오버라이드.

```toml
# config.toml
plan_mode_reasoning_effort = "medium"
```

미설정 시 Plan 프리셋의 기본값(현재 `medium`)을 사용한다. `none`으로 설정하면 "추론 없음"을 의미한다 (전역 기본값 상속 아님).

## Verbosity

GPT-5 모델에서 출력 길이/상세도를 제어한다.

| 값 | 설명 |
|----|------|
| `low` | 간결한 출력 |
| `medium` (기본) | 중간 출력 |
| `high` | 상세한 출력 |

```bash
codex exec -c verbosity=high --json -
```

## 모델 프로바이더

### 기본 프로바이더 (OpenAI)

```toml
# config.toml
model = "o4-mini"
model_provider = "openai"

[model_providers.openai]
name = "OpenAI"
base_url = "https://api.openai.com/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
```

### 커스텀 프로바이더

OpenAI Responses API 호환 프로바이더를 추가할 수 있다.

```toml
[model_providers.custom]
name = "Custom Provider"
base_url = "https://api.custom.com/v1"
env_key = "CUSTOM_API_KEY"
wire_api = "responses"
```

### 주요 프로바이더 설정

| 필드 | 타입 | 설명 |
|------|------|------|
| `name` | string | 표시 이름 |
| `base_url` | string | API 기본 URL |
| `env_key` | string | API 키 환경변수명 |
| `wire_api` | string | 프로토콜 (`responses` 만 지원) |
| `query_params` | map | URL 쿼리 파라미터 |
| `http_headers` | map | 추가 HTTP 헤더 |

> `wire_api = "chat"`는 더 이상 지원되지 않는다. `"responses"`만 사용 가능.

### 로컬 모델 (OSS)

```bash
# Ollama
codex --oss --local-provider ollama

# LM Studio
codex --oss --local-provider lmstudio
```

## Service Tier

OpenAI API의 서비스 등급을 지정한다.

```toml
# config.toml
service_tier = "default"
```

## 프로젝트 내 모델 설정

`core/config.py`에서 Codex CLI 모델과 reasoning effort를 환경변수로 관리한다.

### 환경변수

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `CODEX_MODEL` | `gpt-5.4` | 기본 Codex 모델 |
| `CODEX_MODEL_TIER1` | (CODEX_MODEL) | Tier1 분석 모델 |
| `CODEX_MODEL_TIER2` | (CODEX_MODEL) | Tier2 최종검토 모델 |
| `CODEX_REASONING_EFFORT` | (없음) | 기본 reasoning effort |
| `CODEX_REASONING_EFFORT_TIER1` | (없음) | Tier1 reasoning effort |
| `CODEX_REASONING_EFFORT_TIER1_SCAN` | (없음) | Tier1 스캔 reasoning effort |
| `CODEX_REASONING_EFFORT_TIER1_ANALYSIS` | (없음) | Tier1 분석 reasoning effort |
| `CODEX_REASONING_EFFORT_TIER2` | `xhigh` | Tier2 reasoning effort |
| `CODEX_REASONING_EFFORT_REPORT` | (없음) | 리포트 생성 reasoning effort |
| `CODEX_CLI_PATH` | (없음) | codex 바이너리 경로 |

### 코인 전용 환경변수

| 환경변수 | 설명 |
|----------|------|
| `CRYPTO_CODEX_MODEL` | 코인 Codex 기본 모델 |
| `CRYPTO_CODEX_MODEL_TIER1_SCAN` | 코인 스캔용 모델 |
| `CRYPTO_CODEX_MODEL_TIER1_ANALYSIS` | 코인 분석용 모델 |
| `CRYPTO_CODEX_MODEL_TIER2` | 코인 최종검토 모델 |
| `CRYPTO_CODEX_REASONING_EFFORT_*` | 코인 전용 reasoning effort |

### 유효 reasoning effort 값

```python
VALID_CODEX_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
```

> `none`은 Codex 공식 스펙에는 있으나 프로젝트에서는 사용하지 않는다.

### 모델 ID 형식

프로젝트 내부에서 Codex CLI 모델은 `codex:{model_name}` 형식으로 표현된다:

```python
self._resolved_model = f"codex:{self._model}" if self._model else "codex"
# 예: "codex:gpt-5.4", "codex:o4-mini"
```
