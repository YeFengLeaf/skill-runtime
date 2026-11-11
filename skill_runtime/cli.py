"""Command line entry point for inspecting and exercising a local catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import SkillCatalog
from .context import ContextAdapter, ContextMode
from .runtime import SkillRuntime


def _default_skills_path() -> Path:
    """Use checkout examples first, then the copy bundled in an install."""
    checkout_path = Path("examples/skills")
    if checkout_path.is_dir():
        return checkout_path
    packaged_path = Path(__file__).resolve().parent.parent / "examples" / "skills"
    return packaged_path if packaged_path.is_dir() else checkout_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skill-runtime")
    parser.add_argument("--skills", default=_default_skills_path(), type=Path)
    parser.add_argument("--mode", choices=[item.value for item in ContextMode], default=ContextMode.EPHEMERAL_SYSTEM.value)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("index")
    route = subparsers.add_parser("route")
    route.add_argument("task")
    route.add_argument("--skill-budget-tokens", "--budget", dest="skill_budget_tokens", type=int, default=2_000)
    run = subparsers.add_parser("run")
    run.add_argument("task")
    run.add_argument("--task-id", default="default")
    run.add_argument("--turn", type=int, default=0)
    run.add_argument("--skill-budget-tokens", "--budget", dest="skill_budget_tokens", type=int, default=2_000)
    subparsers.add_parser("demo")
    args = parser.parse_args(argv)

    catalog = SkillCatalog.from_directory(args.skills)
    if args.command == "index":
        print(json.dumps([_record_json(record) for record in catalog], ensure_ascii=False, indent=2))
        return 0
    if args.command == "route":
        result = SkillRuntime(catalog).router.route(args.task, args.skill_budget_tokens)
        print(json.dumps(_route_json(result), ensure_ascii=False, indent=2))
        return 0
    runtime = SkillRuntime(catalog, ContextAdapter(mode=ContextMode(args.mode)))
    if args.command == "demo":
        old_task = "research current facts and cite sources"
        next_task = "clean this csv and inspect missing values"
        ephemeral = SkillRuntime(catalog, ContextAdapter(mode=ContextMode.EPHEMERAL_SYSTEM))
        append_only = SkillRuntime(catalog, ContextAdapter(mode=ContextMode.HISTORY_APPEND))
        ephemeral_first = ephemeral.run_turn(old_task, task_id="research", turn=0)
        ephemeral_next = ephemeral.run_turn(next_task, task_id="data-cleaning", turn=0)
        append_first = append_only.run_turn(old_task, task_id="research", turn=0)
        append_next = append_only.run_turn(next_task, task_id="data-cleaning", turn=0)
        print(json.dumps({
            "ephemeral_system": {"first_turn": _turn_json(ephemeral_first), "next_turn": _turn_json(ephemeral_next)},
            "history_append": {"first_turn": _turn_json(append_first), "next_turn": _turn_json(append_next)},
            "comparison": {
                "next_turn_chars_saved": append_next.context.request.chars - ephemeral_next.context.request.chars,
                "old_skill_present_in_ephemeral_request": "<skill name=\"web-research\">" in ephemeral_next.context.request.text,
                "old_skill_present_in_append_request": "<skill name=\"web-research\">" in append_next.context.request.text,
            },
        }, ensure_ascii=False, indent=2))
        return 0
    result = runtime.run_turn(args.task, task_id=args.task_id, turn=args.turn, skill_budget_tokens=args.skill_budget_tokens)
    print(json.dumps(_turn_json(result), ensure_ascii=False, indent=2))
    return 0


def _record_json(record):
    return {"name": record.name, "key": record.key, "description": record.description, "path": str(record.path), "estimated_tokens": record.estimated_tokens, "sha256": record.sha256}


def _route_json(route):
    return {"reason": route.reason, "skill_budget_tokens": route.skill_budget_tokens, "estimated_tokens": route.estimated_tokens, "selected": [{"name": item.skill.name, "key": item.skill.key, "score": round(item.score, 3), "reasons": item.reasons} for item in route.selected], "rejected": [{"name": item.skill.name, "key": item.skill.key, "score": round(item.score, 3), "reasons": item.reasons} for item in route.rejected]}


def _turn_json(result):
    return {
        "task_id": result.task_id,
        "turn": result.turn,
        "status": result.status,
        "route_status": result.route_status,
        "route": _route_json(result.route),
        "active_skills": result.active_skills,
        "released": [{"skill_name": event.skill_name, "task_id": event.task_id, "reason": event.reason} for event in result.released],
        "injected_skills": result.context.injected_skills,
        "removed_skills": result.context.removed_skills,
        "physically_removed_skills": result.context.physically_removed_skills,
        "residual_skills": result.context.residual_skills,
        "injected_chars": result.context.injected_chars,
        "request_chars": result.context.request.chars,
        "estimated_request_tokens": result.context.estimated_tokens,
    }
