#!/usr/bin/env python3
"""Feishu ↔ Claude Code bridge via long-connection WebSocket."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import lark_oapi as lark
from dotenv import load_dotenv
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from claude_runner import ClaudeRunner
from session_store import SessionStore

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("feishu-claude")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STORE = SessionStore(DATA_DIR / "sessions.json")

def _env(*keys: str, default: str = "") -> str:
    for key in keys:
        val = os.getenv(key)
        if val:
            return val
    if default:
        return default
    raise KeyError(f"Missing env: one of {keys}")


APP_ID = _env("LARK_APP_ID", "FEISHU_APP_ID")
APP_SECRET = _env("LARK_APP_SECRET", "FEISHU_APP_SECRET")
# 国际版 Lark 默认；国内飞书请设 FEISHU_DOMAIN=https://open.feishu.cn
FEISHU_DOMAIN = os.getenv("LARK_DOMAIN", os.getenv("FEISHU_DOMAIN", "https://open.larksuite.com"))
CLAUDE_BIN = os.getenv("CLAUDE_BIN") or shutil.which("claude") or "claude"
CLAUDE_WORKDIR = os.getenv("CLAUDE_WORKDIR", str(Path.home()))
CLAUDE_PERMISSION_MODE = os.getenv("CLAUDE_PERMISSION_MODE", "acceptEdits")
CLAUDE_TIMEOUT_SEC = int(os.getenv("CLAUDE_TIMEOUT_SEC", "600"))
ALLOWED_OPEN_IDS = {
    x.strip()
    for x in os.getenv("ALLOWED_OPEN_IDS", "").split(",")
    if x.strip()
}

# 快捷任务：命令 → 发给 Claude 的完整 prompt（{args} 为后续文字）
TASK_TEMPLATES: dict[str, str] = {
    "/检查": (
        "请对当前工作目录下的项目做完整性检查，输出结构化报告，包含：\n"
        "1. 项目结构与关键文件是否齐全\n"
        "2. 依赖与配置（package/requirements、.env.example、README）\n"
        "3. 明显缺失的测试、文档或 CI\n"
        "4. 安全与可维护性风险\n"
        "5. 优先修复建议（按优先级排序）\n"
        "请实际读取文件后再下结论，不要臆测。\n"
        "补充说明：{args}"
    ),
    "/audit": (
        "请对当前工作目录下的项目做完整性检查，输出结构化报告，包含：\n"
        "1. 项目结构与关键文件是否齐全\n"
        "2. 依赖与配置（package/requirements、.env.example、README）\n"
        "3. 明显缺失的测试、文档或 CI\n"
        "4. 安全与可维护性风险\n"
        "5. 优先修复建议（按优先级排序）\n"
        "请实际读取文件后再下结论，不要臆测。\n"
        "补充说明：{args}"
    ),
    "/汇报": (
        "请根据当前工作目录的项目情况，撰写一份工作汇报（中文），结构包含：\n"
        "• 本周/本阶段完成事项\n"
        "• 进行中任务与进度\n"
        "• 风险与阻塞\n"
        "• 下一步计划\n"
        "语气专业、简洁，适合发给团队或上级。如需具体数据请先查看代码与文档。\n"
        "主题或补充要求：{args}"
    ),
    "/report": (
        "请根据当前工作目录的项目情况，撰写一份工作汇报（中文），结构包含：\n"
        "• 本周/本阶段完成事项\n"
        "• 进行中任务与进度\n"
        "• 风险与阻塞\n"
        "• 下一步计划\n"
        "语气专业、简洁，适合发给团队或上级。如需具体数据请先查看代码与文档。\n"
        "主题或补充要求：{args}"
    ),
    "/设计": (
        "请针对以下需求输出设计草案（中文），包含：背景与目标、方案对比、推荐方案、"
        "模块/接口设计、数据流、风险与待决问题。可配合 mermaid 图示。\n"
        "需求：{args}"
    ),
    "/design": (
        "请针对以下需求输出设计草案（中文），包含：背景与目标、方案对比、推荐方案、"
        "模块/接口设计、数据流、风险与待决问题。可配合 mermaid 图示。\n"
        "需求：{args}"
    ),
    "/文档": (
        "请为当前项目编写或完善文档（中文 Markdown），先了解代码结构再动笔。"
        "要求清晰、可维护，含必要的安装、配置与使用说明。\n"
        "具体要求：{args}"
    ),
    "/doc": (
        "请为当前项目编写或完善文档（中文 Markdown），先了解代码结构再动笔。"
        "要求清晰、可维护，含必要的安装、配置与使用说明。\n"
        "具体要求：{args}"
    ),
    "/开发": (
        "请在当前工作目录执行以下开发任务。先阅读相关代码再修改，保持与现有风格一致，"
        "改完后简要说明改了什么。\n"
        "任务：{args}"
    ),
    "/dev": (
        "请在当前工作目录执行以下开发任务。先阅读相关代码再修改，保持与现有风格一致，"
        "改完后简要说明改了什么。\n"
        "任务：{args}"
    ),
}

EXECUTOR = ThreadPoolExecutor(max_workers=4)
RUNNER = ClaudeRunner(
    claude_bin=CLAUDE_BIN,
    workdir=CLAUDE_WORKDIR,
    permission_mode=CLAUDE_PERMISSION_MODE,
    timeout_sec=CLAUDE_TIMEOUT_SEC,
)

# 飞书 API Client（发消息）
api_client = (
    lark.Client.builder()
    .app_id(APP_ID)
    .app_secret(APP_SECRET)
    .domain(FEISHU_DOMAIN)
    .log_level(lark.LogLevel.INFO)
    .build()
)


def _parse_text_content(content: str) -> str:
    try:
        obj = json.loads(content)
        return (obj.get("text") or "").strip()
    except json.JSONDecodeError:
        return content.strip()


def _split_message(text: str, max_len: int = 3500) -> list[str]:
    """Feishu text messages have size limits; chunk long replies."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks


def send_text(chat_id: str, text: str, reply_to_message_id: str | None = None) -> None:
    body = CreateMessageRequestBody.builder().msg_type("text").content(
        json.dumps({"text": text}, ensure_ascii=False)
    )
    if reply_to_message_id:
        req = (
            ReplyMessageRequest.builder()
            .message_id(reply_to_message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        resp = api_client.im.v1.message.reply(req)
    else:
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                body.receive_id(chat_id).build()
            )
            .build()
        )
        resp = api_client.im.v1.message.create(req)

    if not resp.success():
        log.error("send failed: %s %s", resp.code, resp.msg)


def _handle_command(chat_id: str, text: str, open_id: str) -> str | None:
    """Built-in commands. Return reply text if handled."""
    lower = text.lower().strip()

    if lower in ("/help", "/帮助", "帮助"):
        return (
            "**飞书 × Claude Code 助手**\n\n"
            "直接发送消息即可让 Claude Code 执行任务（读写文件、运行命令等）。\n\n"
            "**快捷任务：**\n"
            "• `/开发 <描述>` — 开发/改代码\n"
            "• `/设计 <需求>` — 设计草案\n"
            "• `/文档 <要求>` — 编写或完善文档\n"
            "• `/汇报 <主题>` — 工作汇报\n"
            "• `/检查 [说明]` — 项目完整性检查\n\n"
            "**会话命令：**\n"
            "• `/new` — 开启新会话（清空上下文绑定）\n"
            "• `/history` — 查看最近聊天记录\n"
            "• `/cwd <路径>` — 设置本会话工作目录\n"
            "• `/pwd` — 查看当前工作目录\n"
            "• `/help` — 显示此帮助\n\n"
            f"默认工作目录：`{CLAUDE_WORKDIR}`"
        )

    if lower in ("/new", "/reset", "/新会话"):
        STORE.reset(chat_id)
        return "已开启新会话。下一条消息将开始新的 Claude Code 对话。"

    if lower in ("/history", "/记录"):
        return STORE.format_history(chat_id)

    if lower.startswith("/cwd"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return "用法：`/cwd /path/to/your/project`"
        path = parts[1].strip()
        p = Path(path).expanduser()
        if not p.exists():
            return f"路径不存在：`{path}`"
        session = STORE.get(chat_id)
        session.workdir = str(p.resolve())
        STORE.update(session)
        return f"已设置工作目录：`{session.workdir}`"

    if lower in ("/pwd",):
        session = STORE.get(chat_id)
        wd = session.workdir or CLAUDE_WORKDIR
        return f"当前工作目录：`{wd}`"

    return None


def _expand_task_prompt(text: str) -> str:
    """Expand /开发、/检查 等快捷命令为完整 prompt。"""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return text
    parts = stripped.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else "（无额外说明，请按命令默认要求执行）"
    template = TASK_TEMPLATES.get(cmd)
    if template is None:
        return text
    return template.format(args=args)


def _process_claude_task(
    chat_id: str,
    message_id: str,
    user_text: str,
    open_id: str,
) -> None:
    session = STORE.get(chat_id)
    workdir = session.workdir or CLAUDE_WORKDIR

    prompt = _expand_task_prompt(user_text)
    session.append("user", user_text)
    STORE.update(session)

    result = RUNNER.run(
        prompt,
        workdir=workdir,
        session_id=session.claude_session_id,
        continue_session=bool(session.claude_session_id),
    )

    if result.error:
        reply = f"❌ 执行失败\n\n{result.error}"
        session.append("system", result.error)
    else:
        footer = ""
        if result.cost_usd is not None:
            footer = f"\n\n—\n💰 本次约 ${result.cost_usd:.4f}"
        reply = result.text + footer
        session.append("assistant", result.text)
        if result.session_id:
            session.claude_session_id = result.session_id

    STORE.update(session)

    chunks = _split_message(reply)
    for i, chunk in enumerate(chunks):
        prefix = f"（{i + 1}/{len(chunks)}）\n" if len(chunks) > 1 else ""
        send_text(
            chat_id,
            prefix + chunk,
            reply_to_message_id=message_id if i == 0 else None,
        )


def handle_message(data: P2ImMessageReceiveV1) -> None:
    """Must return within ~3s — offload Claude to background thread."""
    event = data.event
    if not event or not event.message:
        return

    msg = event.message
    if msg.message_type != "text":
        return

    sender = event.sender
    open_id = ""
    if sender and sender.sender_id:
        open_id = sender.sender_id.open_id or ""

    if ALLOWED_OPEN_IDS and open_id not in ALLOWED_OPEN_IDS:
        log.warning("ignored message from %s", open_id)
        return

    text = _parse_text_content(msg.content or "")
    if not text:
        return

    # 忽略 @机器人 时可能带的前缀
    text = re.sub(r"^@\S+\s*", "", text).strip()
    if not text:
        return

    chat_id = msg.chat_id or ""
    message_id = msg.message_id or ""

    cmd_reply = _handle_command(chat_id, text, open_id)
    if cmd_reply is not None:
        send_text(chat_id, cmd_reply, reply_to_message_id=message_id)
        session = STORE.get(chat_id)
        session.append("user", text)
        session.append("assistant", cmd_reply)
        STORE.update(session)
        return

    # 3 秒内先回复，Claude 在后台跑
    send_text(chat_id, "⏳ 已收到，Claude Code 正在处理…", reply_to_message_id=message_id)

    EXECUTOR.submit(_process_claude_task, chat_id, message_id, text, open_id)


def main() -> None:
    if not Path(CLAUDE_BIN).exists() and not shutil.which(CLAUDE_BIN):
        log.warning("Claude binary may not exist: %s", CLAUDE_BIN)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(handle_message)
        .build()
    )

    ws_client = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
        domain=FEISHU_DOMAIN,
    )

    log.info("Starting Lark long connection → Claude Code (domain=%s)", FEISHU_DOMAIN)
    log.info("Claude: %s | workdir: %s", CLAUDE_BIN, CLAUDE_WORKDIR)
    log.info("Sessions: %s", DATA_DIR / "sessions.json")

    try:
        ws_client.start()
    except KeyboardInterrupt:
        log.info("Shutting down…")
    finally:
        EXECUTOR.shutdown(wait=False)


if __name__ == "__main__":
    main()
