#!/usr/bin/env python3
"""Preflight checks before starting the Lark ↔ Claude Code bridge."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

OK = "✓"
FAIL = "✗"
WARN = "!"


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = OK if ok else FAIL
    line = f"  {mark} {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def main() -> int:
    print("Lark × Claude Code 启动前检查\n")

    env_path = BASE / ".env"
    all_ok = True

    all_ok &= check(".env 文件存在", env_path.exists(), str(env_path))

    app_id = os.getenv("LARK_APP_ID") or os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("LARK_APP_SECRET") or os.getenv("FEISHU_APP_SECRET", "")
    domain = os.getenv("LARK_DOMAIN", os.getenv("FEISHU_DOMAIN", "https://open.larksuite.com"))

    all_ok &= check(
        "LARK_APP_ID 已配置",
        bool(app_id) and not app_id.startswith("cli_xxx"),
        app_id[:12] + "…" if app_id else "请在 .env 填写",
    )
    all_ok &= check(
        "LARK_APP_SECRET 已配置",
        bool(app_secret) and app_secret != "xxxxxxxxxxxxxxxx",
        "已设置" if app_secret else "请在 .env 填写",
    )
    all_ok &= check("API 域名", domain == "https://open.larksuite.com", domain)

    claude_bin = os.getenv("CLAUDE_BIN") or shutil.which("claude") or "claude"
    claude_ok = bool(shutil.which(claude_bin) or Path(claude_bin).exists())
    all_ok &= check("Claude Code CLI", claude_ok, claude_bin)

    workdir = Path(os.getenv("CLAUDE_WORKDIR", str(Path.home() / "Desktop" / "WorkSpace")))
    all_ok &= check("工作目录存在", workdir.exists(), str(workdir))

    try:
        import lark_oapi  # noqa: F401

        check("Python 依赖 lark-oapi", True)
    except ImportError:
        all_ok &= check("Python 依赖 lark-oapi", False, "运行: pip install -r requirements.txt")

    print()
    if all_ok:
        print("全部通过。运行: python main.py")
        print("然后在 open.larksuite.com → Events → 选择 Long connection 并保存。")
        return 0

    print("请先修复上述问题，再运行 python main.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
