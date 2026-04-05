"""Claude Code CLI Provider — 로컬 Claude Code 구독으로 LLM 호출

claude -p 모드를 사용하여 API 키 없이 Claude Code 구독 크레딧으로 동작.
세션을 유지하여 사이클 내 맥락(시장 스캔 → 종목 분석 → 리포트)을 공유.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from analysis.llm.base import (
    LLMExecutionPlan,
    LLMProviderCapabilities,
    LLMRequest,
    LLMSessionHandle,
    empty_usage_snapshot,
)
from core.config import settings
from trading.enums import LLMProvider, LLMTier, Tier1Profile
from trading.market_profile import normalize_market_scope


class ClaudeCodeProvider:
    """Claude Code CLI를 subprocess로 호출하는 LLM Provider

    세션 관리:
    - start_session(): 새 세션 시작 (사이클 시작 시)
    - 이후 generate() 호출은 --resume로 같은 세션 이어감
    - end_session(): 세션 종료, ID 반환 (나중에 resume 가능)
    - resume_session(id): 이전 세션 이어서 사용

    세션이 없으면 일회성 호출 (--no-session-persistence)
    """

    CAPABILITIES = LLMProviderCapabilities(
        profile_specific_models=True,
        reasoning_effort_control=True,
        persistent_session=True,
        usage_reporting="json_response",
    )

    # 클래스 레벨 세션 관리 ((scope, phase)별)
    _session_states: dict[tuple[str, str], dict[str, Any]] = {}
    _session_locks: dict[tuple[str, str], asyncio.Lock] = {}

    # 클래스 레벨 누적 사용량
    cumulative_usage: dict = empty_usage_snapshot(LLMProvider.CLAUDE_CODE)

    def __init__(
        self,
        tier: LLMTier = LLMTier.TIER1,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ):
        self._tier = tier
        self._claude_path: str | None = None
        self._model_override = model
        self._reasoning_effort_override = reasoning_effort
        self._model = model if model is not None else settings.get_llm_model(LLMProvider.CLAUDE_CODE, tier)
        self._reasoning_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else settings.get_llm_reasoning_effort(LLMProvider.CLAUDE_CODE, tier)
        )
        self._resolved_model: str = ""

    @classmethod
    def _session_key(cls, scope: str, phase: str) -> tuple[str, str]:
        return normalize_market_scope(scope), phase or "cycle"

    @classmethod
    def _get_state(cls, scope: str, phase: str) -> dict[str, Any]:
        key = cls._session_key(scope, phase)
        return cls._session_states.setdefault(
            key,
            {
                "active_session_id": None,
                "session_initialized": False,
            },
        )

    @classmethod
    def _get_lock(cls, scope: str, phase: str) -> asyncio.Lock:
        """세션 락 (resume 호출 직렬화)"""
        key = cls._session_key(scope, phase)
        if key not in cls._session_locks:
            cls._session_locks[key] = asyncio.Lock()
        return cls._session_locks[key]

    # ── 세션 관리 ──

    @classmethod
    def start_session(cls, scope: str = "KRX", phase: str = "cycle") -> str:
        """새 세션 시작 — 사이클/거래일 시작 시 호출"""
        state = cls._get_state(scope, phase)
        state["active_session_id"] = str(uuid4())
        state["session_initialized"] = False
        logger.info(
            "Claude Code 세션 시작: {} [{}:{}]",
            state["active_session_id"][:8],
            normalize_market_scope(scope),
            phase,
        )
        return state["active_session_id"]

    @classmethod
    def end_session(cls, scope: str = "KRX", phase: str = "cycle") -> str | None:
        """세션 종료 — 세션 ID 반환 (나중에 resume 가능)"""
        key = cls._session_key(scope, phase)
        state = cls._session_states.get(key, {})
        sid = state.get("active_session_id")
        if sid:
            logger.info("Claude Code 세션 종료: {} [{}:{}]", sid[:8], key[0], key[1])
        cls._session_states.pop(key, None)
        return sid

    @classmethod
    def pause_session(cls, scope: str = "KRX", phase: str = "cycle") -> str | None:
        """세션 일시 중지 — 병렬 분석 구간에서 사용

        세션 ID를 보존하되 활성 상태 해제 → generate()가 일회성 호출로 동작.
        병렬 분석 완료 후 resume_session()으로 복원.
        """
        state = cls._get_state(scope, phase)
        sid = state.get("active_session_id")
        if sid:
            logger.info(
                "Claude Code 세션 일시 중지: {} [{}:{}] (병렬 구간)",
                sid[:8],
                normalize_market_scope(scope),
                phase,
            )
        state["active_session_id"] = None
        return sid

    @classmethod
    def resume_session(cls, session_id: str, scope: str = "KRX", phase: str = "cycle") -> None:
        """이전 세션 재개 — 장 재개, 다음 사이클 등"""
        state = cls._get_state(scope, phase)
        state["active_session_id"] = session_id
        state["session_initialized"] = True  # 이미 디스크에 존재
        logger.info(
            "Claude Code 세션 재개: {} [{}:{}]",
            session_id[:8],
            normalize_market_scope(scope),
            phase,
        )

    @classmethod
    def get_session_id(cls, scope: str | None = None, phase: str = "cycle") -> str | None:
        if scope is None:
            for state in cls._session_states.values():
                if state.get("active_session_id"):
                    return state["active_session_id"]
            return None
        return cls._get_state(scope, phase).get("active_session_id")

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.CLAUDE_CODE

    @property
    def display_name(self) -> str:
        return "Claude Code (로컬)"

    @property
    def tier(self) -> LLMTier:
        return self._tier

    @property
    def configured_model(self) -> str:
        if self._model_override is not None:
            return self._model_override
        return settings.get_llm_model(self.provider, self.tier)

    @property
    def configured_reasoning_effort(self) -> str | None:
        if self._reasoning_effort_override is not None:
            return self._reasoning_effort_override
        return settings.get_llm_reasoning_effort(self.provider, self.tier)

    @property
    def model_id(self) -> str:
        return self._resolved_model or f"claude-code:{self._model}"

    @property
    def capabilities(self) -> LLMProviderCapabilities:
        return self.CAPABILITIES

    @classmethod
    def get_session_handle(cls, scope: str = "KRX", phase: str = "cycle") -> LLMSessionHandle:
        scope_key = normalize_market_scope(scope)
        state = cls._get_state(scope, phase)
        external_id = state.get("active_session_id")
        initialized = bool(state.get("session_initialized"))
        return LLMSessionHandle(
            provider=LLMProvider.CLAUDE_CODE,
            scope=scope_key,
            phase=phase or "cycle",
            external_id=external_id,
            session_enabled=bool(external_id),
            initialized=initialized,
        )

    def plan_request(self, request: LLMRequest) -> LLMExecutionPlan:
        effective_profile: Tier1Profile | None = None
        if request.tier == LLMTier.TIER1:
            effective_profile = settings.resolve_runtime_tier1_profile(
                request.requested_profile,
                scope=request.scope,
                phase=request.phase,
            )
        scope = normalize_market_scope(request.scope or "GLOBAL")
        if request.scope is None:
            session_handle = LLMSessionHandle(
                provider=self.provider,
                scope=scope,
                phase=request.phase,
                external_id=None,
                session_enabled=False,
                initialized=False,
            )
        else:
            session_handle = self.get_session_handle(request.scope, request.phase)
        model = self._model_override or settings.get_llm_model_for_scope_provider(
            request.scope,
            self.provider,
            request.tier,
            effective_profile,
        )
        reasoning_effort = (
            request.reasoning_effort_override
            or self._reasoning_effort_override
            or settings.get_llm_reasoning_effort_for_scope_provider(
                request.scope,
                self.provider,
                request.tier,
                effective_profile,
                request.phase,
            )
        )
        return LLMExecutionPlan(
            provider=self.provider,
            tier=request.tier,
            requested_profile=request.requested_profile,
            effective_profile=effective_profile,
            scope=scope,
            phase=request.phase,
            model=model,
            reasoning_effort=reasoning_effort,
            session_mode=session_handle.mode,
            session_handle=session_handle,
            capabilities=self.capabilities,
        )

    def _find_claude(self) -> str | None:
        """claude CLI 경로 탐색"""
        if self._claude_path:
            return self._claude_path
        path = settings.get_llm_cli_path(LLMProvider.CLAUDE_CODE)
        if path:
            self._claude_path = path
            return path
        return None

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        scope: str | None = None,
        phase: str = "cycle",
        reasoning_effort_override: str | None = None,
    ) -> str:
        request = LLMRequest(
            tier=self.tier,
            prompt=prompt,
            system_prompt=system_prompt,
            scope=scope,
            phase=phase,
            requested_profile=None,
            reasoning_effort_override=reasoning_effort_override,
        )
        plan = self.plan_request(request)
        return await self.generate_from_plan(request, plan)

    async def generate_from_plan(
        self,
        request: LLMRequest,
        plan: LLMExecutionPlan,
    ) -> str:
        """claude -p 로 텍스트 생성

        세션이 활성화된 경우:
        - 첫 호출: --session-id로 세션 생성 + system_prompt 설정
        - 이후 호출: --resume로 세션 이어감 (맥락 유지)
          - system_prompt는 사용자 프롬프트 앞에 [역할] 로 포함

        세션이 없는 경우:
        - --no-session-persistence로 일회성 호출
        """
        claude = self._find_claude()
        if not claude:
            raise RuntimeError("claude CLI를 찾을 수 없습니다 (PATH 확인)")

        # Tier별 effort: TIER1(스캔/분석)=medium, TIER2(최종검토)=high
        effort = plan.reasoning_effort or "medium"

        cmd = [
            claude, "-p",
            "--output-format", "json",
            "--model", plan.model,
            "--max-turns", "1",
        ]
        # Haiku는 extended thinking 미지원이므로 effort 생략
        if "haiku" not in (plan.model or "").lower():
            cmd.extend(["--effort", effort])
        cmd.append("--dangerously-skip-permissions")
        self._resolved_model = f"claude-code:{plan.model}"

        actual_prompt = request.prompt

        session_id = None
        session_initialized = False
        state: dict[str, Any] | None = None
        if request.scope is not None:
            state = self.__class__._get_state(request.scope, request.phase)
            session_id = state.get("active_session_id")
            session_initialized = bool(state.get("session_initialized"))

        if session_id:
            if session_initialized:
                # 기존 세션 이어감
                cmd.extend(["--resume", session_id])
                # resume 시 system_prompt 변경 불가 → 프롬프트 앞에 역할 명시
                if request.system_prompt:
                    actual_prompt = f"[역할]\n{request.system_prompt}\n\n[요청]\n{request.prompt}"
            else:
                # 첫 호출: 세션 생성
                cmd.extend(["--session-id", session_id])
                if request.system_prompt:
                    cmd.extend(["--system-prompt", request.system_prompt])
        else:
            # 세션 없음: 일회성
            cmd.extend(["--no-session-persistence"])
            if request.system_prompt:
                cmd.extend(["--system-prompt", request.system_prompt])

        # 세션 사용 시 직렬화 (같은 세션에 동시 resume 방지)
        if session_id and request.scope is not None:
            async with self._get_lock(request.scope, request.phase):
                result = await self._execute(cmd, actual_prompt)
                # 첫 호출 성공 후 세션 초기화 완료 표시
                if state is not None and not state["session_initialized"]:
                    state["session_initialized"] = True
                return result
        else:
            return await self._execute(cmd, actual_prompt)

    @staticmethod
    def _clean_env() -> dict:
        """subprocess용 환경변수 — CLAUDECODE 제거 (중첩 세션 방지)"""
        import os
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        return env

    async def _execute(self, cmd: list, prompt: str) -> str:
        """subprocess 실행 + JSON 파싱"""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._clean_env(),
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=300.0,
        )

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500]
            # stderr 비어있으면 stdout에서 에러 메시지 추출
            if not err.strip():
                err = stdout.decode("utf-8", errors="replace")[:500]
            logger.error("Claude Code 호출 실패 (exit {}): {}", proc.returncode, err)
            raise RuntimeError(f"Claude Code 실패 (exit {proc.returncode}): {err}")

        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            raise RuntimeError("Claude Code 빈 응답")

        try:
            resp = json.loads(raw)
            result_text = resp.get("result", "")
            self._track_usage(resp)
        except json.JSONDecodeError:
            result_text = raw

        if not result_text:
            raise RuntimeError("Claude Code 빈 응답")

        return result_text

    def _track_usage(self, resp: dict) -> None:
        """JSON 응답에서 토큰 사용량 누적"""
        cost = resp.get("total_cost_usd", 0)
        model_usage = resp.get("modelUsage", {})

        self.cumulative_usage["provider"] = self.provider.value
        self.cumulative_usage["total_calls"] += 1
        self.cumulative_usage["total_cost_usd"] += cost

        for model_name, usage in model_usage.items():
            inp = usage.get("inputTokens", 0)
            out = usage.get("outputTokens", 0)
            cache_r = usage.get("cacheReadInputTokens", 0)
            cache_c = usage.get("cacheCreationInputTokens", 0)
            model_cost = usage.get("costUSD", 0)

            self.cumulative_usage["total_input_tokens"] += inp
            self.cumulative_usage["total_output_tokens"] += out
            self.cumulative_usage["total_cache_read"] += cache_r
            self.cumulative_usage["total_cache_creation"] += cache_c
            self.cumulative_usage["total_cached_input_tokens"] += cache_r

            by_model = self.cumulative_usage["by_model"]
            m = by_model.setdefault(model_name, {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "cost_usd": 0.0,
            })
            m["calls"] += 1
            m["input_tokens"] += inp
            m["output_tokens"] += out
            m["cache_read"] += cache_r
            m["cache_creation"] += cache_c
            m["cost_usd"] += model_cost

            if not self._resolved_model or model_cost > 0.01:
                self._resolved_model = model_name

    @classmethod
    def get_usage_snapshot(cls) -> dict:
        """현재 누적 사용량 스냅샷 반환 (API용)"""
        u = cls.cumulative_usage
        return {
            "provider": LLMProvider.CLAUDE_CODE.value,
            "total_calls": u["total_calls"],
            "total_cost_usd": round(u["total_cost_usd"], 4),
            "total_input_tokens": u["total_input_tokens"],
            "total_output_tokens": u["total_output_tokens"],
            "total_cache_read": u["total_cache_read"],
            "total_cache_creation": u["total_cache_creation"],
            "total_cached_input_tokens": u["total_cached_input_tokens"],
            "by_model": {
                model: {**stats}
                for model, stats in u["by_model"].items()
            },
            "session_id": cls.get_session_id(),
            "provider_data": {},
        }

    @classmethod
    def get_usage_report(cls) -> dict[str, Any]:
        """Claude 사용량 리포트 반환"""
        stats_path = Path.home() / ".claude" / "stats-cache.json"
        if not stats_path.exists():
            return {
                "provider": LLMProvider.CLAUDE_CODE.value,
                "provider_name": "Claude Code (로컬)",
                "summary": {
                    "total_sessions": None,
                    "total_messages": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "cached_input_tokens": None,
                    "first_session_date": None,
                },
                "app_usage": cls.get_usage_snapshot(),
                "model_usage": {},
                "daily_model_tokens": [],
                "provider_data": {},
            }

        try:
            data = json.loads(stats_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("[LLM] Claude 사용량 파싱 실패")
            return {
                "provider": LLMProvider.CLAUDE_CODE.value,
                "provider_name": "Claude Code (로컬)",
                "summary": {
                    "total_sessions": None,
                    "total_messages": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "cached_input_tokens": None,
                    "first_session_date": None,
                },
                "app_usage": cls.get_usage_snapshot(),
                "model_usage": {},
                "daily_model_tokens": [],
                "provider_data": {},
            }

        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_input_tokens = 0
        model_usage = data.get("modelUsage", {})
        for usage in model_usage.values():
            total_input_tokens += int(usage.get("inputTokens") or 0)
            total_output_tokens += int(usage.get("outputTokens") or 0)
            total_cached_input_tokens += int(usage.get("cacheReadInputTokens") or 0)

        return {
            "provider": LLMProvider.CLAUDE_CODE.value,
            "provider_name": "Claude Code (로컬)",
            "summary": {
                "total_sessions": data.get("totalSessions"),
                "total_messages": data.get("totalMessages"),
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cached_input_tokens": total_cached_input_tokens,
                "first_session_date": data.get("firstSessionDate"),
            },
            "app_usage": cls.get_usage_snapshot(),
            "model_usage": model_usage,
            "daily_model_tokens": data.get("dailyModelTokens", []),
            "provider_data": {
                "daily_activity": data.get("dailyActivity", []),
            },
        }

    async def is_available(self) -> bool:
        """claude CLI 설치 여부 확인"""
        path = self._find_claude()
        if not path:
            logger.debug("Claude Code CLI를 찾을 수 없음 (PATH, /opt/homebrew/bin 등 확인)")
        return path is not None
