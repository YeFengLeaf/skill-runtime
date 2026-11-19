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
        self._last_blocks: dict[str, str] = {}
        self._history_bodies: dict[str, set[str]] = {}

    def build(self, skills: Iterable[tuple[SkillRecord, str]], turn: int, user_task: str = "") -> ContextSnapshot:
        loaded = tuple(skills)
        names = tuple(record.key for record, _ in loaded)
        current_bodies = {record.key: body for record, body in loaded}
        current_blocks = {record.key: _render_skill_block(record, body) for record, body in loaded}
        skill_text = _render_skills(loaded)
        removed = tuple(name for name in self._last_injected if name not in names)
        if self.mode is ContextMode.EPHEMERAL_SYSTEM:
            messages_before_task = _with_system_block(self.base_messages, skill_text)
        elif self.mode is ContextMode.TOOL_PROMPT:
            messages_before_task = self.base_messages + (
                ({"role": "developer", "content": skill_text},) if skill_text else ()
            )
            # Tool prompts are part of this request only; they are not stored in history.
        else:
            # An append-only host keeps every block forever. Appending the same
            # rendered block again would duplicate context without adding information.
            new_blocks = [
                current_blocks[record.key]
                for record, _ in loaded
                if current_blocks[record.key]
                and not _contains_skill_block(self._history, current_blocks[record.key])
            ]
            if new_blocks:
                self._history.append({
                    "role": "developer",
                    "content": "<active_skills>\n" + "\n".join(new_blocks) + "\n</active_skills>",
                })
                for record, body in loaded:
                    if body and current_blocks[record.key] in new_blocks:
                        self._history_bodies.setdefault(record.key, set()).add(body)
            messages_before_task = tuple(self._history)
        if user_task:
            messages = (*messages_before_task, {"role": "user", "content": user_task})
            if self.mode is ContextMode.HISTORY_APPEND:
                self._history.append({"role": "user", "content": user_task})
        else:
            messages = messages_before_task
        request = RequestEnvelope(tuple(messages))
        # Compare exact rendered blocks without the current task text: a task
        # may legitimately quote a skill body, but that does not keep the old
        # injected block in the request.
        request_before_task = "\n".join(
            message.get("content", "") for message in messages_before_task
        )
        physically_removed = tuple(
            name for name in removed
            if self._last_blocks.get(name) and self._last_blocks[name] not in request_before_task
        )
        # Residual counts older copies that are still in append-only history and
        # are not the body currently injected for that skill.
        residual = tuple(sorted(
            name for name, bodies in self._history_bodies.items()
            if any(body != current_bodies.get(name) for body in bodies)
        )) if self.mode is ContextMode.HISTORY_APPEND else ()
        self._last_injected = names
        self._last_blocks = current_blocks
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


def _render_skill_block(record: SkillRecord, body: str) -> str:
    name = escape(record.name, quote=True)
    if record.key == record.name:
        opening = f'<skill name="{name}">'
    else:
        opening = f'<skill name="{name}" id="{escape(record.key, quote=True)}">'
    return "\n".join((opening, body, "</skill>"))


def _render_skills(skills: tuple[tuple[SkillRecord, str], ...]) -> str:
    if not skills:
        return ""
    blocks = ["<active_skills>"]
    blocks.extend(_render_skill_block(record, body) for record, body in skills)
    blocks.append("</active_skills>")
    return "\n".join(blocks)


def _contains_skill_block(messages: Iterable[Mapping[str, str]], block: str) -> bool:
    return any(
        block in message.get("content", "")
        for message in messages
        if message.get("role") in {"system", "developer"}
    )


def _with_system_block(base: tuple[dict[str, str], ...], skill_text: str) -> tuple[dict[str, str], ...]:
    if not skill_text:
        return base
    messages = list(base)
    for index, message in enumerate(messages):
        if message.get("role") == "system":
            messages[index] = {**message, "content": message.get("content", "") + "\n" + skill_text}
            return tuple(messages)
    return ({"role": "system", "content": skill_text}, *base)
