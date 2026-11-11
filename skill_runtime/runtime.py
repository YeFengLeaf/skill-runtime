"""End-to-end runtime orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import SkillCatalog
from .context import ContextAdapter, ContextSnapshot
from .lease import LeaseManager, ReleaseEvent
from .router import RoutePlan, Router


@dataclass(frozen=True)
class TurnResult:
    task_id: str
    turn: int
    route: RoutePlan
    context: ContextSnapshot
    released: tuple[ReleaseEvent, ...]
    active_skills: tuple[str, ...]

    @property
    def status(self) -> str:
        if self.context.residual_skills:
            return "logical_unload_only"
        if self.context.physically_removed:
            return "physically_removed"
        if not self.route.selected:
            return "no_match"
        return "active"

    @property
    def route_status(self) -> str:
        return "matched" if self.route.selected else "no_match"


class SkillRuntime:
    def __init__(self, catalog: SkillCatalog, context: ContextAdapter | None = None) -> None:
        self.catalog = catalog
        self.router = Router(catalog)
        self.leases = LeaseManager(catalog)
        self.context = context or ContextAdapter()

    def run_turn(
        self,
        task: str,
        *,
        task_id: str = "default",
        turn: int = 0,
        skill_budget_tokens: int | None = None,
        max_skills: int = 3,
        ttl_turns: int = 1,
        budget_tokens: int | None = None,
    ) -> TurnResult:
        plan = self.router.route(
            task,
            skill_budget_tokens,
            max_skills,
            budget_tokens=budget_tokens,
        )
        selected_keys = {candidate.skill.key for candidate in plan.selected}
        released = self.leases.reconcile(task_id, selected_keys, turn)
        for candidate in plan.selected:
            self.leases.acquire(candidate.skill.key, task_id, turn, ttl_turns)
        loaded = tuple(
            (self.catalog.get_by_key(key), self.catalog.get_by_key(key).read_body())
            for key in sorted(selected_keys)
        )
        context = self.context.build(loaded, turn, task)
        active = tuple(lease.skill_name for lease in self.leases.active(task_id))
        return TurnResult(task_id, turn, plan, context, released, active)
