"""Per-chat session mapping and lightweight chat history."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HistoryEntry:
    role: str  # user | assistant | system
    content: str
    at: str = field(default_factory=_utc_now)


@dataclass
class ChatSession:
    chat_id: str
    claude_session_id: str | None = None
    workdir: str | None = None
    history: list[HistoryEntry] = field(default_factory=list)
    updated_at: str = field(default_factory=_utc_now)

    def append(self, role: str, content: str, max_entries: int = 40) -> None:
        self.history.append(HistoryEntry(role=role, content=content))
        if len(self.history) > max_entries:
            self.history = self.history[-max_entries:]
        self.updated_at = _utc_now()


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._sessions: dict[str, ChatSession] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for chat_id, data in raw.items():
            history = [HistoryEntry(**h) for h in data.get("history", [])]
            self._sessions[chat_id] = ChatSession(
                chat_id=chat_id,
                claude_session_id=data.get("claude_session_id"),
                workdir=data.get("workdir"),
                history=history,
                updated_at=data.get("updated_at", _utc_now()),
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        for chat_id, s in self._sessions.items():
            payload[chat_id] = {
                "claude_session_id": s.claude_session_id,
                "workdir": s.workdir,
                "history": [asdict(h) for h in s.history],
                "updated_at": s.updated_at,
            }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, chat_id: str) -> ChatSession:
        with self._lock:
            if chat_id not in self._sessions:
                self._sessions[chat_id] = ChatSession(chat_id=chat_id)
            return self._sessions[chat_id]

    def update(self, session: ChatSession) -> None:
        with self._lock:
            session.updated_at = _utc_now()
            self._sessions[session.chat_id] = session
            self._save()

    def reset(self, chat_id: str) -> ChatSession:
        with self._lock:
            session = ChatSession(chat_id=chat_id)
            self._sessions[chat_id] = session
            self._save()
            return session

    def new_claude_session_id(self) -> str:
        return str(uuid.uuid4())

    def format_history(self, chat_id: str, limit: int = 10) -> str:
        session = self.get(chat_id)
        lines: list[str] = []
        for entry in session.history[-limit:]:
            label = {"user": "你", "assistant": "Claude", "system": "系统"}.get(
                entry.role, entry.role
            )
            text = entry.content.strip()
            if len(text) > 500:
                text = text[:500] + "…"
            lines.append(f"**{label}** ({entry.at[:16]})\n{text}")
        if not lines:
            return "（暂无聊天记录）"
        sid = session.claude_session_id or "未建立"
        workdir = session.workdir or "（使用默认工作目录）"
        header = f"会话 ID: `{sid[:8]}…`\n工作目录: `{workdir}`\n\n"
        return header + "\n\n---\n\n".join(lines)
