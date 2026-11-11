"""Cheap, deterministic task-to-skill routing with explicit budget decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import ceil

from .catalog import SkillCatalog, SkillRecord

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
}


@dataclass(frozen=True)
class RouteCandidate:
    skill: SkillRecord
    score: float
    reasons: tuple[str, ...]
    estimated_tokens: int


@dataclass(frozen=True)
class RoutePlan:
    task: str
    skill_budget_tokens: int
    selected: tuple[RouteCandidate, ...]
    rejected: tuple[RouteCandidate, ...]
    reason: str

    @property
    def estimated_tokens(self) -> int:
        return sum(item.estimated_tokens for item in self.selected)

    @property
    def budget_tokens(self) -> int:
        """Backward-compatible alias for the skill-only budget."""
        return self.skill_budget_tokens


class Router:
    def __init__(self, catalog: SkillCatalog, threshold: float = 0.20) -> None:
        self.catalog = catalog
        self.threshold = threshold
        term_frequency: dict[str, int] = {}
        for record in catalog:
            record_terms = (
                _terms(record.name.replace("-", " "))
                | _terms_from_values(record.triggers)
                | _terms_from_values(record.tags)
                | _terms(record.description)
            )
            for term in record_terms:
                term_frequency[term] = term_frequency.get(term, 0) + 1
        common_cutoff = max(5, ceil(len(catalog) * 0.25))
        self._common_terms = {
            term for term, frequency in term_frequency.items() if frequency >= common_cutoff
        }

    def route(
        self,
        task: str,
        skill_budget_tokens: int | None = None,
        max_skills: int = 3,
        *,
        budget_tokens: int | None = None,
    ) -> RoutePlan:
        if skill_budget_tokens is None:
            skill_budget_tokens = budget_tokens if budget_tokens is not None else 2_000
        elif budget_tokens is not None:
            raise TypeError("pass only skill_budget_tokens (budget_tokens is a compatibility alias)")
        if skill_budget_tokens < 0:
            raise ValueError("skill_budget_tokens must be non-negative")
        if max_skills < 1:
            raise ValueError("max_skills must be positive")
        task_terms = _terms(task)
        ranked_candidates = sorted(
            (self._score(record, task_terms) for record in self.catalog),
            key=lambda item: (-item.score, item.skill.key),
        )
        selected: list[RouteCandidate] = []
        rejected: list[RouteCandidate] = []
        used_tokens = 0
        seen_names: set[str] = set()
        for candidate in ranked_candidates:
            # Duplicate catalog keys remain addressable, but one metadata name
            # should consume at most one routing slot in a request.
            if candidate.skill.name in seen_names:
                rejected.append(candidate)
                continue
            if candidate.score < self.threshold:
                rejected.append(candidate)
                continue
            if len(selected) >= max_skills or used_tokens + candidate.estimated_tokens > skill_budget_tokens:
                rejected.append(candidate)
                continue
            selected.append(candidate)
            # Reserve the name only once the skill is actually selected: a
            # duplicate rejected for budget must not block a cheaper copy.
            seen_names.add(candidate.skill.name)
            used_tokens += candidate.estimated_tokens
        reason = "matched" if selected else "no candidate met threshold and budget"
        return RoutePlan(task, skill_budget_tokens, tuple(selected), tuple(rejected), reason)

    def _score(self, record: SkillRecord, task_terms: set[str]) -> RouteCandidate:
        name_terms = _terms(record.name.replace("-", " "))
        description_terms = _terms(record.description)
        trigger_terms = _terms_from_values(record.triggers)
        tag_terms = _terms(" ".join(record.tags))
        reasons: list[str] = []
        score = 0.0
        for term in sorted(task_terms & name_terms):
            score += self._weight(term, 0.52)
            reasons.append(f"name:{term}")
        for term in sorted(task_terms & trigger_terms):
            # A task containing only common terms still has usable triggers.
            score += 0.30 if task_terms <= self._common_terms else self._weight(term, 0.30)
            reasons.append(f"trigger:{term}")
        for term in sorted(task_terms & tag_terms):
            score += self._weight(term, 0.20)
            reasons.append(f"tag:{term}")
        overlap = task_terms & description_terms
        if overlap:
            score += min(0.38, sum(self._weight(term, 0.12) for term in overlap))
            reasons.append("description:" + ",".join(sorted(overlap)))
        return RouteCandidate(record, min(score, 1.0), tuple(reasons), record.estimated_tokens)

    def _weight(self, term: str, base: float) -> float:
        return base * (0.2 if term in self._common_terms else 1.0)


def _terms_from_values(values: tuple[str, ...]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        terms.update(_terms(value))
    return terms


def _terms(value: str) -> set[str]:
    # Keep words and CJK characters so the router remains useful for Chinese tasks.
    lowered = value.lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9._-]*", lowered)) - _STOP_WORDS
    cjk = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+", lowered):
        cjk.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
        if len(chunk) == 1:
            cjk.add(chunk)
    return words | cjk
