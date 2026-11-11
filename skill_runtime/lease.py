"""Short-lived skill leases and stale release decisions."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import SkillCatalog, SkillRecord


@dataclass(frozen=True)
class Lease:
    skill_name: str
    task_id: str
    acquired_turn: int
    expires_turn: int


@dataclass(frozen=True)
class ReleaseEvent:
    skill_name: str
    task_id: str
    reason: str
    turn: int


class LeaseManager:
    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog
        self._leases: dict[str, Lease] = {}
        self.events: list[ReleaseEvent] = []

    def acquire(self, skill_name: str, task_id: str, turn: int, ttl_turns: int = 1) -> SkillRecord:
        if turn < 0 or ttl_turns < 1:
            raise ValueError("turn must be non-negative and ttl_turns must be positive")
        self._leases[skill_name] = Lease(skill_name, task_id, turn, turn + ttl_turns)
        return self.catalog.get(skill_name)

    def reconcile(self, task_id: str, keep: set[str], turn: int) -> tuple[ReleaseEvent, ...]:
        released: list[ReleaseEvent] = []
        for name, lease in list(self._leases.items()):
            reason: str | None = None
            if lease.task_id != task_id:
                reason = "task_switched"
            elif name not in keep:
                reason = "route_changed"
            elif lease.expires_turn < turn:
                reason = "expired"
            if reason:
                event = ReleaseEvent(name, lease.task_id, reason, turn)
                released.append(event)
                self.events.append(event)
                del self._leases[name]
        return tuple(released)

    def active(self, task_id: str | None = None) -> tuple[Lease, ...]:
        leases = self._leases.values()
        if task_id is not None:
            leases = (lease for lease in leases if lease.task_id == task_id)
        return tuple(sorted(leases, key=lambda item: item.skill_name))
