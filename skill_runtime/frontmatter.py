"""Small dependency-free parser for the metadata subset used by SKILL.md."""

from __future__ import annotations

import ast
import re
from typing import Any


class FrontmatterError(ValueError):
    """Raised when frontmatter uses YAML structures this parser cannot preserve."""


def split_document(text: str) -> tuple[dict[str, Any], str]:
    """Return frontmatter and markdown body.

    Agent Skills use YAML frontmatter. This parser intentionally accepts the
    common scalar/list subset so the runtime has no mandatory third-party
    dependency. Unsupported YAML constructs are kept as strings rather than
    silently changing their meaning.
    """

    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized
    end = normalized.find("\n---", 4)
    if end < 0:
        return {}, normalized
    header = normalized[4:end]
    body_start = end + len("\n---")
    if normalized[body_start:body_start + 1] == "\n":
        body_start += 1
    metadata: dict[str, Any] = {}
    lines = header.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1].isspace() or ":" not in line:
            raise FrontmatterError(f"unsupported frontmatter line {index + 1}: {line.strip()}")
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise FrontmatterError(f"empty frontmatter key on line {index + 1}")
        value = _strip_inline_comment(value.strip())
        index += 1
        if not value:
            next_index = index
            while next_index < len(lines) and (
                not lines[next_index].strip() or lines[next_index].lstrip().startswith("#")
            ):
                next_index += 1
            if next_index < len(lines) and lines[next_index][:1].isspace():
                if lines[next_index].lstrip().startswith("-"):
                    try:
                        metadata[key], index = _block_list(
                            lines, next_index, len(line) - len(line.lstrip())
                        )
                    except FrontmatterError:
                        if key in {"triggers", "tags"}:
                            raise
                        metadata[key], index = _block_text(
                            lines, next_index, len(line) - len(line.lstrip())
                        )
                    continue
                if key in {"triggers", "tags"}:
                    raise FrontmatterError(
                        f"unsupported nested YAML value for {key!r} on line {next_index + 1}"
                    )
                metadata[key], index = _block_text(lines, next_index, len(line) - len(line.lstrip()))
                continue
            metadata[key] = ""
            continue
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            next_index = index
            while next_index < len(lines) and (
                not lines[next_index].strip() or lines[next_index].lstrip().startswith("#")
            ):
                next_index += 1
            if next_index < len(lines) and lines[next_index][:1].isspace():
                metadata[key], index = _block_text(lines, next_index, len(line) - len(line.lstrip()))
            else:
                metadata[key] = ""
            continue
        metadata[key] = _value(value)
    return metadata, normalized[body_start:]


def _block_list(lines: list[str], start: int, parent_indent: int) -> tuple[list[str], int]:
    values: list[str] = []
    list_indent: int | None = None
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            break
        content = line[indent:]
        if content != "-" and not content.startswith("- "):
            raise FrontmatterError(f"unsupported nested YAML list item on line {index + 1}")
        if list_indent is None:
            list_indent = indent
        elif indent != list_indent:
            raise FrontmatterError(f"inconsistent YAML list indentation on line {index + 1}")
        item = content[1:].strip()
        if not item:
            raise FrontmatterError(f"nested YAML list values are unsupported on line {index + 1}")
        if re.match(r"[^'\"]+:\s", item):
            raise FrontmatterError(f"nested YAML mappings are unsupported on line {index + 1}")
        values.append(str(_value(_strip_inline_comment(item))))
        index += 1
    return values, index


def _block_text(lines: list[str], start: int, parent_indent: int) -> tuple[str, int]:
    index = start
    base_indent: int | None = None
    values: list[str] = []
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            values.append("")
            index += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            break
        if base_indent is None:
            base_indent = indent
        values.append(line[base_indent:])
        index += 1
    while values and not values[-1]:
        values.pop()
    return "\n".join(values), index


def _value(value: str) -> Any:
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed]
        except (ValueError, SyntaxError):
            return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value.strip("'\"")


def _strip_inline_comment(value: str) -> str:
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\" and quote == '"':
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value


def as_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(item for item in re.split(r"[,;]", str(value)) if item.strip())
