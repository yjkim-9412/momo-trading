"""Codex CLI provider — 로컬 Codex CLI로 텍스트 생성."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from analysis.llm.base import empty_usage_snapshot
from core.config import settings
from trading.enums import LLMProvider, LLMTier


class CodexCLIProvider:
    """Codex CLI를 subprocess로 호출하는 LLM provider"""

    _active_session_id: str | None = None
    _session_enabled: bool = False
    _session_initialized: bool = False
    _session_lock: asyncio.Lock | None = None
    cumulative_usage: dict[str, Any] = empty_usage_snapshot(LLMProvider.CODEX_CLI)

    def __init__(self, tier: LLMTier = LLMTier.TIER1):
        self._tier = tier
        self._codex_path: str | None = None
        self._model = settings.get_llm_model(LLMProvider.CODEX_CLI, tier)
        self._reasoning_effort = settings.get_llm_reasoning_effort(
            LLMProvider.CODEX_CLI,
            tier,
        )
        self._resolved_model = f"codex:{self._model}" if self._model else "codex"

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """세션 재개 호출 직렬화"""
        if cls._session_lock is None:
            cls._session_lock = asyncio.Lock()
        return cls._session_lock

    @classmethod
    def start_session(cls) -> str | None:
        """새 세션 시작 요청"""
        cls._session_enabled = True
        cls._active_session_id = None
        cls._session_initialized = False
        logger.info("Codex 세션 시작")
        return None

    @classmethod
    def end_session(cls) -> str | None:
        """세션 종료"""
        session_id = cls._active_session_id
        if session_id:
            logger.info("Codex 세션 종료: {}", session_id[:8])
        cls._session_enabled = False
        cls._active_session_id = None
        cls._session_initialized = False
        return session_id

    @classmethod
    def pause_session(cls) -> str | None:
        """세션 일시 중지"""
        session_id = cls._active_session_id
        if session_id:
            logger.info("Codex 세션 일시 중지: {} (병렬 구간)", session_id[:8])
        cls._session_enabled = False
        return session_id

    @classmethod
    def resume_session(cls, session_id: str) -> None:
        """기존 세션 재개"""
        cls._session_enabled = True
        cls._active_session_id = session_id
        cls._session_initialized = True
        logger.info("Codex 세션 재개: {}", session_id[:8])

    @classmethod
    def get_session_id(cls) -> str | None:
        """현재 세션 ID 반환"""
        return cls._active_session_id

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.CODEX_CLI

    @property
    def display_name(self) -> str:
        return "Codex CLI (로컬)"

    @property
    def tier(self) -> LLMTier:
        return self._tier

    @property
    def configured_model(self) -> str:
        return self._model

    @property
    def configured_reasoning_effort(self) -> str | None:
        return self._reasoning_effort

    @property
    def model_id(self) -> str:
        return self._resolved_model

    def _find_codex(self) -> str | None:
        """codex CLI 경로 탐색"""
        if self._codex_path:
            return self._codex_path
        path = settings.get_llm_cli_path(LLMProvider.CODEX_CLI)
        if path:
            self._codex_path = path
            return path
        return None

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        """codex exec로 텍스트 생성"""
        codex = self._find_codex()
        if not codex:
            raise RuntimeError("codex CLI를 찾을 수 없습니다 (PATH 확인)")

        actual_prompt = self._build_prompt(prompt, system_prompt)
        cmd, output_path = self._build_command(codex)

        if self.__class__._session_enabled:
            async with self._get_lock():
                return await self._execute(cmd, actual_prompt, output_path)
        return await self._execute(cmd, actual_prompt, output_path)

    @staticmethod
    def _build_prompt(prompt: str, system_prompt: str) -> str:
        """system/user prompt를 하나의 Codex prompt로 합성"""
        if not system_prompt:
            return prompt
        return f"[역할]\n{system_prompt}\n\n[요청]\n{prompt}"

    def _append_reasoning_effort(self, cmd: list[str]) -> None:
        """Codex reasoning effort override 추가"""
        if self._reasoning_effort:
            cmd.extend(["-c", f"model_reasoning_effort={self._reasoning_effort}"])

    def _build_command(self, codex_path: str) -> tuple[list[str], str]:
        """세션 상태에 맞는 Codex CLI 명령 구성"""
        fd, output_path = tempfile.mkstemp(prefix="codex-llm-", suffix=".txt")
        os.close(fd)

        if self.__class__._session_enabled:
            if self.__class__._session_initialized and self.__class__._active_session_id:
                cmd = [
                    codex_path,
                    "exec",
                    "resume",
                    "--json",
                    "--skip-git-repo-check",
                    "-o",
                    output_path,
                ]
                if self._model:
                    cmd.extend(["--model", self._model])
                self._append_reasoning_effort(cmd)
                cmd.extend([self.__class__._active_session_id, "-"])
                return cmd, output_path

            cmd = [
                codex_path,
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "-o",
                output_path,
            ]
            if self._model:
                cmd.extend(["--model", self._model])
            self._append_reasoning_effort(cmd)
            cmd.append("-")
            return cmd, output_path

        cmd = [
            codex_path,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-o",
            output_path,
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        self._append_reasoning_effort(cmd)
        cmd.append("-")
        return cmd, output_path

    async def _execute(
        self,
        cmd: list[str],
        prompt: str,
        output_path: str,
    ) -> str:
        """subprocess 실행 + JSONL 파싱"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tempfile.gettempdir(),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")),
                timeout=300.0,
            )

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace")[:500]
                if not err.strip():
                    err = stdout.decode("utf-8", errors="replace")[:500]
                logger.error("Codex CLI 호출 실패 (exit {}): {}", proc.returncode, err)
                raise RuntimeError(f"Codex CLI 실패 (exit {proc.returncode}): {err}")

            raw_stdout = stdout.decode("utf-8", errors="replace").strip()
            raw_stderr = stderr.decode("utf-8", errors="replace").strip()
            if raw_stderr:
                logger.debug("Codex CLI stderr (무시): {}", raw_stderr[:300])
            parsed = self.parse_event_stream(raw_stdout)
            result_text = self._read_result_text(output_path, parsed.get("fallback_text", ""))

            if self.__class__._session_enabled and not self.__class__._session_initialized:
                session_id = parsed.get("session_id")
                if not session_id:
                    raise RuntimeError("Codex 세션 ID를 찾을 수 없습니다")
                self.__class__._active_session_id = session_id
                self.__class__._session_initialized = True

            self._track_usage(parsed.get("usage", {}))

            if not result_text:
                raise RuntimeError("Codex CLI 빈 응답")
            return result_text
        finally:
            try:
                os.remove(output_path)
            except OSError:
                pass

    @classmethod
    def parse_event_stream(cls, raw_stdout: str) -> dict[str, Any]:
        """Codex JSONL 이벤트에서 세션/usage/응답 추출"""
        session_id = None
        usage: dict[str, Any] = {}
        fallback_text = ""

        for raw_line in raw_stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("{"):
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            if event_type == "thread.started":
                session_id = event.get("thread_id") or session_id
                continue

            if event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message" and item.get("text"):
                    fallback_text = item["text"]
                continue

            if event_type == "turn.completed":
                usage = event.get("usage", {}) or usage

        return {
            "session_id": session_id,
            "usage": usage,
            "fallback_text": fallback_text,
        }

    @staticmethod
    def _read_result_text(output_path: str, fallback_text: str) -> str:
        """마지막 메시지 파일을 우선 사용하고 없으면 이벤트 텍스트로 대체"""
        try:
            with open(output_path, encoding="utf-8") as file:
                result_text = file.read().strip()
            if result_text:
                return result_text
        except OSError:
            pass
        return fallback_text.strip()

    def _track_usage(self, usage: dict[str, Any]) -> None:
        """Codex usage 누적"""
        if not usage:
            return

        input_tokens = usage.get("input_tokens", 0)
        cached_input_tokens = usage.get("cached_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        self.cumulative_usage["provider"] = self.provider.value
        self.cumulative_usage["total_calls"] += 1
        self.cumulative_usage["total_input_tokens"] += input_tokens
        self.cumulative_usage["total_output_tokens"] += output_tokens
        self.cumulative_usage["total_cached_input_tokens"] += cached_input_tokens

        model_usage = self.cumulative_usage["by_model"].setdefault(
            self.model_id,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
            },
        )
        model_usage["calls"] += 1
        model_usage["input_tokens"] += input_tokens
        model_usage["output_tokens"] += output_tokens
        model_usage["cached_input_tokens"] += cached_input_tokens

    @classmethod
    def get_usage_snapshot(cls) -> dict[str, Any]:
        """현재 누적 사용량 스냅샷 반환"""
        usage = cls.cumulative_usage
        return {
            "provider": LLMProvider.CODEX_CLI.value,
            "session_id": cls._active_session_id,
            "total_calls": usage["total_calls"],
            "total_cost_usd": usage["total_cost_usd"],
            "total_input_tokens": usage["total_input_tokens"],
            "total_output_tokens": usage["total_output_tokens"],
            "total_cache_read": usage["total_cache_read"],
            "total_cache_creation": usage["total_cache_creation"],
            "total_cached_input_tokens": usage["total_cached_input_tokens"],
            "by_model": {
                model: {**stats}
                for model, stats in usage["by_model"].items()
            },
            "provider_data": {},
        }

    @classmethod
    def get_usage_report(cls) -> dict[str, Any]:
        """Codex 사용량 리포트 반환"""
        sessions_dir = Path.home() / ".codex" / "sessions"
        total_sessions = len(list(sessions_dir.rglob("*.jsonl"))) if sessions_dir.exists() else 0
        return {
            "provider": LLMProvider.CODEX_CLI.value,
            "provider_name": "Codex CLI (로컬)",
            "summary": {
                "total_sessions": total_sessions,
                "total_messages": None,
                "input_tokens": None,
                "output_tokens": None,
                "cached_input_tokens": None,
                "first_session_date": None,
            },
            "app_usage": cls.get_usage_snapshot(),
            "model_usage": {},
            "daily_model_tokens": [],
            "provider_data": {
                "sessions_path": str(sessions_dir),
            },
        }

    async def is_available(self) -> bool:
        """codex CLI 설치 여부 확인"""
        path = self._find_codex()
        if not path:
            logger.debug("Codex CLI를 찾을 수 없음 (PATH, /opt/homebrew/bin 등 확인)")
        return path is not None
