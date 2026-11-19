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
        # Preserve router ranking: top-k order controls prompt priority and
        # telemetry, so a set or an alphabetical sort would lose information.
        selected_keys = tuple(dict.fromkeys(candidate.skill.key for candidate in plan.selected))
        released = self.leases.reconcile(task_id, set(selected_keys), turn)
        for candidate in plan.selected:
            self.leases.acquire(candidate.skill.key, task_id, turn, ttl_turns)
        loaded = []
        for key in selected_keys:
            record = self.catalog.get_by_key(key)
            loaded.append((record, record.read_body()))
        context = self.context.build(tuple(loaded), turn, task)
        active_names = {lease.skill_name for lease in self.leases.active(task_id)}
        active = tuple(name for name in selected_keys if name in active_names)
        return TurnResult(task_id, turn, plan, context, released, active)
