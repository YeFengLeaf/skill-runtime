"""Context adapters that make physical versus logical release explicit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from html import escape
from typing import Iterable, Mapping

from .catalog import SkillRecord


class ContextMode(str, Enum):
    EPHEMERAL_SYSTEM = "ephemeral_system"
    TOOL_PROMPT = "tool_prompt"
    HISTORY_APPEND = "history_append"


@dataclass(frozen=True)
class RequestEnvelope:
    messages: tuple[dict[str, str], ...]

    @property
    def text(self) -> str:
        return "\n".join(message.get("content", "") for message in self.messages)

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class ContextSnapshot:
    request: RequestEnvelope
    mode: ContextMode
    injected_skills: tuple[str, ...]
    removed_skills: tuple[str, ...]
    physically_removed_skills: tuple[str, ...]
    residual_skills: tuple[str, ...]
    physically_removed: bool
    injected_chars: int
    estimated_tokens: int


class ContextAdapter:
    """Build each request from a baseline, or intentionally model append-only history."""

    def __init__(self, base_messages: Iterable[Mapping[str, str]] = (), mode: ContextMode = ContextMode.EPHEMERAL_SYSTEM) -> None:
        self.base_messages = tuple({"role": str(item["role"]), "content": str(item["content"])} for item in base_messages)
        self.mode = ContextMode(mode)
        self._history = list(self.base_messages)
        self._last_injected: tuple[str, ...] = ()
        self._last_bodies: dict[str, str] = {}
        self._history_bodies: dict[str, set[str]] = {}

    def build(self, skills: Iterable[tuple[SkillRecord, str]], turn: int, user_task: str = "") -> ContextSnapshot:
        loaded = tuple(skills)
        names = tuple(record.key for record, _ in loaded)
        current_bodies = {record.key: body for record, body in loaded}
        skill_text = _render_skills(loaded)
        removed = tuple(name for name in self._last_injected if name not in names)
        if self.mode is ContextMode.EPHEMERAL_SYSTEM:
            messages = _with_user_task(_with_system_block(self.base_messages, skill_text), user_task)
        elif self.mode is ContextMode.TOOL_PROMPT:
            messages = self.base_messages + (({"role": "developer", "content": skill_text},) if skill_text else ())
            messages = _with_user_task(messages, user_task)
            # Tool prompts are part of this request only; they are not stored in history.
        else:
            if skill_text:
                self._history.append({"role": "developer", "content": skill_text})
                for name, body in current_bodies.items():
                    if body:
                        self._history_bodies.setdefault(name, set()).add(body)
            if user_task:
                self._history.append({"role": "user", "content": user_task})
            messages = tuple(self._history)
        request = RequestEnvelope(tuple(messages))
        physically_removed = tuple(
            name for name in removed
            if self._last_bodies.get(name) and self._last_bodies[name] not in request.text
        )
        residual = tuple(sorted(
            name for name, bodies in self._history_bodies.items()
            if name not in names and any(body in request.text for body in bodies)
        )) if self.mode is ContextMode.HISTORY_APPEND else ()
        self._last_injected = names
        self._last_bodies = current_bodies
        return ContextSnapshot(
            request=request,
            mode=self.mode,
            injected_skills=names,
            removed_skills=removed,
            physically_removed_skills=physically_removed,
            residual_skills=residual,
            physically_removed=bool(physically_removed),
            injected_chars=len(skill_text),
            estimated_tokens=max(1, (request.chars + 3) // 4) if request.chars else 0,
        )


def _render_skills(skills: tuple[tuple[SkillRecord, str], ...]) -> str:
    if not skills:
        return ""
    blocks = ["<active_skills>"]
    for record, body in skills:
        name = escape(record.name, quote=True)
        if record.key == record.name:
            opening = f'<skill name="{name}">'
        else:
            opening = f'<skill name="{name}" id="{escape(record.key, quote=True)}">'
        blocks.extend((opening, body, "</skill>"))
    blocks.append("</active_skills>")
    return "\n".join(blocks)


def _with_system_block(base: tuple[dict[str, str], ...], skill_text: str) -> tuple[dict[str, str], ...]:
    if not skill_text:
        return base
    messages = list(base)
    for index, message in enumerate(messages):
        if message.get("role") == "system":
            messages[index] = {**message, "content": message.get("content", "") + "\n" + skill_text}
            return tuple(messages)
    return ({"role": "system", "content": skill_text}, *base)


def _with_user_task(messages: tuple[dict[str, str], ...], user_task: str) -> tuple[dict[str, str], ...]:
    if not user_task:
        return messages
    return (*messages, {"role": "user", "content": user_task})
