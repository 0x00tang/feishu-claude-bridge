"""Invoke local Claude Code in non-interactive mode."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ClaudeResult:
    text: str
    session_id: str | None
    cost_usd: float | None = None
    error: str | None = None


class ClaudeRunner:
    def __init__(
        self,
        claude_bin: str,
        workdir: str,
        permission_mode: str = "acceptEdits",
        timeout_sec: int = 600,
    ) -> None:
        self.claude_bin = claude_bin
        self.default_workdir = Path(workdir).expanduser().resolve()
        self.permission_mode = permission_mode
        self.timeout_sec = timeout_sec

    def run(
        self,
        prompt: str,
        *,
        workdir: str | None = None,
        session_id: str | None = None,
        continue_session: bool = False,
    ) -> ClaudeResult:
        cwd = Path(workdir or self.default_workdir).expanduser().resolve()
        cwd.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            self.permission_mode,
        ]

        if session_id:
            cmd.extend(["--resume", session_id])
        elif continue_session:
            cmd.append("--continue")

        env = os.environ.copy()
        # 使用已登录的 Claude Code OAuth（非 bare 模式会读 keychain）
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return ClaudeResult(
                text="",
                session_id=session_id,
                error=f"Claude Code 执行超时（>{self.timeout_sec}s）",
            )
        except FileNotFoundError:
            return ClaudeResult(
                text="",
                session_id=session_id,
                error=f"找不到 Claude 可执行文件: {self.claude_bin}",
            )

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            if len(err) > 2000:
                err = err[:2000] + "…"
            return ClaudeResult(
                text="",
                session_id=session_id,
                error=err or f"Claude Code 退出码 {proc.returncode}",
            )

        stdout = proc.stdout.strip()
        try:
            data = json.loads(stdout)
            if data.get("is_error"):
                err_text = str(data.get("result") or data.get("error") or "未知错误")
                return ClaudeResult(
                    text="",
                    session_id=data.get("session_id") or session_id,
                    error=err_text,
                )
            result = data.get("result") or data.get("structured_output")
            if isinstance(result, dict):
                result = json.dumps(result, ensure_ascii=False, indent=2)
            text = str(result or "").strip()
            new_sid = data.get("session_id") or session_id
            usage = data.get("usage") or {}
            cost = usage.get("total_cost_usd")
            if cost is None:
                cost = data.get("total_cost_usd")
            return ClaudeResult(
                text=text or "（无文本回复）",
                session_id=new_sid,
                cost_usd=float(cost) if cost is not None else None,
            )
        except json.JSONDecodeError:
            # 部分版本可能直接输出纯文本
            return ClaudeResult(
                text=stdout or "（无文本回复）",
                session_id=session_id,
            )
