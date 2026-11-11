"""Index local Agent Skills without loading their full bodies into memory."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from .frontmatter import FrontmatterError, as_list, split_document


class CatalogError(ValueError):
    """Raised when a skill directory cannot be indexed safely."""


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    path: Path
    triggers: tuple[str, ...]
    tags: tuple[str, ...]
    compatibility: tuple[str, ...]
    risk: str
    sha256: str
    body_chars: int
    estimated_tokens: int
    catalog_key: str = ""

    @property
    def key(self) -> str:
        """Stable identifier within one catalog, distinct from metadata name."""
        return self.catalog_key or self.name

    def read_body(self) -> str:
        raw = self.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise CatalogError(f"skill changed after indexing: {self.key}")
        text = raw.decode("utf-8")
        _, body = split_document(text)
        return body.strip()


class SkillCatalog:
    """Metadata index. Full skill text is read only after a lease is acquired."""

    def __init__(self, records: Iterable[SkillRecord] = ()) -> None:
        self._records = {record.key: record for record in records}

    @classmethod
    def from_directory(cls, root: str | Path) -> "SkillCatalog":
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            raise CatalogError(f"skill directory does not exist: {root_path}")
        paths = sorted(root_path.rglob("SKILL.md"))
        parsed = []
        name_counts: dict[str, int] = {}
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(root_path):
                raise CatalogError(f"skill path escapes the catalog root: {path}")
            record = _record_from_path(path)
            parsed.append((path, record))
            name_counts[record.name] = name_counts.get(record.name, 0) + 1

        records: list[SkillRecord] = []
        keys: set[str] = set()
        for path, record in parsed:
            key = record.name
            if name_counts[record.name] > 1:
                relative = path.relative_to(root_path).parent.as_posix()
                key = f"{record.name}@{relative}" if relative else record.name
            if key in keys:
                raise CatalogError(f"duplicate catalog key: {key}")
            keys.add(key)
            records.append(replace(record, catalog_key=key))
        return cls(records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self):
        return iter(sorted(self._records.values(), key=lambda item: (item.name, item.key)))

    def get(self, name: str) -> SkillRecord:
        matches = [record for record in self._records.values() if record.name == name]
        if len(matches) > 1 and name not in self._records:
            raise CatalogError(f"ambiguous skill name {name!r}; use its catalog key")
        try:
            return self._records[name]
        except KeyError as exc:
            if len(matches) == 1:
                return matches[0]
            raise CatalogError(f"unknown skill: {name}") from exc

    def get_by_key(self, key: str) -> SkillRecord:
        try:
            return self._records[key]
        except KeyError as exc:
            raise CatalogError(f"unknown catalog key: {key}") from exc

    def records(self) -> tuple[SkillRecord, ...]:
        return tuple(self)


def _record_from_path(path: Path) -> SkillRecord:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    try:
        metadata, body = split_document(text)
    except FrontmatterError as exc:
        raise CatalogError(f"{path}: {exc}") from exc
    name = str(metadata.get("name", "")).strip()
    description = str(metadata.get("description", "")).strip()
    if not name or not description:
        raise CatalogError(f"{path}: frontmatter requires name and description")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
        raise CatalogError(f"{path}: invalid skill name {name!r}")
    body = body.strip()
    return SkillRecord(
        name=name,
        description=description,
        path=path,
        triggers=as_list(metadata.get("triggers")),
        tags=as_list(metadata.get("tags")),
        compatibility=as_list(metadata.get("compatibility")),
        risk=str(metadata.get("risk", "normal")).strip() or "normal",
        sha256=hashlib.sha256(raw).hexdigest(),
        body_chars=len(body),
        estimated_tokens=max(1, (len(body) + 3) // 4) + 8,
    )
