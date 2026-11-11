from pathlib import Path
from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest

from skill_runtime.catalog import CatalogError, SkillCatalog
from skill_runtime.context import ContextAdapter, ContextMode
from skill_runtime.cli import main
from skill_runtime.runtime import SkillRuntime


ROOT = Path(__file__).parent.parent
SKILLS = ROOT / "examples" / "skills"


class RuntimeTests(unittest.TestCase):
    def test_catalog_indexes_metadata_without_losing_hashes(self):
        catalog = SkillCatalog.from_directory(SKILLS)
        self.assertEqual(len(catalog), 3)
        record = catalog.get("web-research")
        self.assertTrue(record.description.startswith("Research current facts"))
        self.assertTrue(record.sha256)
        self.assertGreater(record.estimated_tokens, 0)

    def test_router_selects_only_relevant_skill_and_respects_budget(self):
        runtime = SkillRuntime(SkillCatalog.from_directory(SKILLS))
        plan = runtime.router.route("fact-check this web research and cite sources", budget_tokens=1_000)
        self.assertEqual([item.skill.name for item in plan.selected], ["web-research"])
        self.assertGreaterEqual(plan.selected[0].score, 0.20)
        self.assertIn("trigger:research", plan.selected[0].reasons)

    def test_router_matches_chinese_trigger_without_common_word_noise(self):
        runtime = SkillRuntime(SkillCatalog.from_directory(SKILLS))
        plan = runtime.router.route("请调研并核验来源", budget_tokens=1_000)
        self.assertEqual([item.skill.name for item in plan.selected], ["web-research"])
        self.assertIn("trigger:调研", plan.selected[0].reasons)

    def test_budget_can_reject_a_match_without_loading_everything(self):
        runtime = SkillRuntime(SkillCatalog.from_directory(SKILLS))
        plan = runtime.router.route("review the code for regressions and missing tests", budget_tokens=1)
        self.assertEqual(plan.selected, ())
        self.assertTrue(plan.rejected)
        self.assertTrue(plan.reason.startswith("no candidate"))

    def test_task_switch_releases_old_lease_and_removes_old_body_from_next_request(self):
        runtime = SkillRuntime(
            SkillCatalog.from_directory(SKILLS),
            ContextAdapter(base_messages=({"role": "system", "content": "You are a careful assistant."},)),
        )
        first = runtime.run_turn("research current facts and cite sources", task_id="task-a", turn=0)
        second = runtime.run_turn("clean this csv and inspect missing values", task_id="task-b", turn=0)
        self.assertEqual(first.active_skills, ("web-research",))
        self.assertEqual(second.active_skills, ("data-cleaning",))
        self.assertTrue(any(event.skill_name == "web-research" and event.reason == "task_switched" for event in second.released))
        self.assertIn("web-research", second.context.removed_skills)
        self.assertNotIn("<skill name=\"web-research\">", second.context.request.text)
        self.assertIn("<skill name=\"data-cleaning\">", second.context.request.text)
        self.assertIn("clean this csv", second.context.request.text)
        self.assertEqual(second.status, "physically_removed")
        self.assertEqual(second.context.physically_removed_skills, ("web-research",))

    def test_same_task_route_change_releases_stale_skill(self):
        runtime = SkillRuntime(SkillCatalog.from_directory(SKILLS))
        runtime.run_turn("research current facts and cite sources", task_id="same", turn=0)
        result = runtime.run_turn("review code for regressions", task_id="same", turn=1)
        self.assertEqual(result.active_skills, ("code-review",))
        self.assertTrue(any(event.reason == "route_changed" for event in result.released))

    def test_history_append_reports_logical_only_and_keeps_old_body(self):
        runtime = SkillRuntime(
            SkillCatalog.from_directory(SKILLS),
            ContextAdapter(mode=ContextMode.HISTORY_APPEND),
        )
        runtime.run_turn("research current facts and cite sources", task_id="a", turn=0)
        result = runtime.run_turn("clean this csv and inspect missing values", task_id="b", turn=0)
        self.assertEqual(result.status, "logical_unload_only")
        self.assertFalse(result.context.physically_removed)
        self.assertIn("<skill name=\"web-research\">", result.context.request.text)
        later = runtime.run_turn("zzxy-another-task", task_id="c", turn=0)
        self.assertEqual(later.status, "logical_unload_only")
        self.assertEqual(later.context.residual_skills, ("data-cleaning", "web-research"))
        self.assertIn("<skill name=\"data-cleaning\">", result.context.request.text)

    def test_history_append_no_match_does_not_hide_old_context(self):
        runtime = SkillRuntime(
            SkillCatalog.from_directory(SKILLS),
            ContextAdapter(mode=ContextMode.HISTORY_APPEND),
        )
        runtime.run_turn("research current facts and cite sources", task_id="a", turn=0)
        result = runtime.run_turn("zzxy-unmatched-task", task_id="b", turn=0)
        self.assertEqual(result.route_status, "no_match")
        self.assertEqual(result.status, "logical_unload_only")
        self.assertFalse(result.context.physically_removed)
        self.assertIn("<skill name=\"web-research\">", result.context.request.text)

    def test_malformed_skill_frontmatter_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "broken" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text("---\nname: Broken Name\n---\nbody", encoding="utf-8")
            with self.assertRaises(CatalogError):
                SkillCatalog.from_directory(directory)

    def test_skill_file_change_after_indexing_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "verified" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text("---\nname: verified\ndescription: A verified sample.\n---\n\nOriginal body.\n", encoding="utf-8")
            record = SkillCatalog.from_directory(directory).get("verified")
            skill.write_text("---\nname: verified\ndescription: A verified sample.\n---\n\nChanged body.\n", encoding="utf-8")
            with self.assertRaisesRegex(CatalogError, "changed after indexing"):
                record.read_body()

    def test_duplicate_names_are_retained_with_stable_catalog_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder, trigger in (("one", "first"), ("two", "second")):
                skill = root / folder / "SKILL.md"
                skill.parent.mkdir()
                skill.write_text(
                    "---\nname: shared\ndescription: A duplicated sample.\ntriggers: [{0}]\n---\n\n{0} body.\n".format(trigger),
                    encoding="utf-8",
                )
            catalog = SkillCatalog.from_directory(root)
            self.assertEqual(len(catalog), 2)
            self.assertEqual([record.key for record in catalog], ["shared@one", "shared@two"])
            with self.assertRaisesRegex(CatalogError, "ambiguous"):
                catalog.get("shared")
            runtime = SkillRuntime(catalog)
            result = runtime.run_turn("use second", task_id="duplicate", turn=0, budget_tokens=100)
            self.assertEqual(result.active_skills, ("shared@two",))
            self.assertIn('<skill name="shared" id="shared@two">', result.context.request.text)

    def test_duplicate_names_do_not_consume_multiple_route_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ("one", "two"):
                skill = root / folder / "SKILL.md"
                skill.parent.mkdir()
                skill.write_text(
                    "---\nname: shared\ndescription: A duplicated sample.\ntriggers: [common]\n---\n\nShared body.\n",
                    encoding="utf-8",
                )
            unique = root / "unique" / "SKILL.md"
            unique.parent.mkdir()
            unique.write_text(
                "---\nname: unique\ndescription: A unique sample.\ntriggers: [other]\n---\n\nUnique body.\n",
                encoding="utf-8",
            )
            runtime = SkillRuntime(SkillCatalog.from_directory(root))
            result = runtime.run_turn("common other", task_id="dedupe", turn=0, budget_tokens=100)
            self.assertEqual(result.active_skills, ("shared@one", "unique"))
            self.assertEqual(len({name.split("@", 1)[0] for name in result.active_skills}), 2)

    def test_budget_rejected_duplicate_does_not_block_cheaper_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder, body in (("aaa-heavy", "x" * 400), ("bbb-light", "Tiny.")):
                skill = root / folder / "SKILL.md"
                skill.parent.mkdir()
                skill.write_text(
                    "---\nname: shared\ndescription: A duplicated sample.\ntriggers: [common]\n---\n\n"
                    + body
                    + "\n",
                    encoding="utf-8",
                )
            catalog = SkillCatalog.from_directory(root)
            tokens = {record.key: record.estimated_tokens for record in catalog}
            self.assertEqual(tokens, {"shared@aaa-heavy": 108, "shared@bbb-light": 10})
            plan = SkillRuntime(catalog).router.route("common", skill_budget_tokens=20)
            self.assertEqual([item.skill.key for item in plan.selected], ["shared@bbb-light"])
            self.assertEqual(len(plan.selected), 1)
            self.assertLessEqual(plan.estimated_tokens, 20)

    def test_block_list_frontmatter_preserves_triggers(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "listed" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text(
                "---\nname: listed\ndescription: A listed sample.\ntriggers:\n  - alpha\n  - beta # inline comment\ntags:\n  - one\n---\n\nBody.\n",
                encoding="utf-8",
            )
            record = SkillCatalog.from_directory(directory).get("listed")
            self.assertEqual(record.triggers, ("alpha", "beta"))
            self.assertEqual(record.tags, ("one",))

    def test_nested_non_routing_metadata_does_not_block_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "nested" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text(
                "---\nname: nested\ndescription: Nested metadata sample.\ncompatibility:\n  platforms:\n    - linux\nmetadata:\n  owner: team-a\n---\n\nBody.\n",
                encoding="utf-8",
            )
            record = SkillCatalog.from_directory(directory).get("nested")
            self.assertEqual(record.compatibility, ("platforms:\n  - linux",))

    def test_chinese_bigrams_do_not_cross_punctuation(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "false-positive" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text(
                "---\nname: false-positive\ndescription: Unrelated sample.\ntriggers: [研来]\n---\n\nBody.\n",
                encoding="utf-8",
            )
            plan = SkillRuntime(SkillCatalog.from_directory(directory)).router.route(
                "调研，来源", skill_budget_tokens=100
            )
            self.assertEqual(plan.selected, ())

    def test_common_explicit_trigger_still_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(5):
                skill = root / "audit-{0}".format(index) / "SKILL.md"
                skill.parent.mkdir()
                skill.write_text(
                    "---\nname: audit-{0}\ndescription: A task-specific capability.\ntriggers: [audit]\n---\n\nBody.\n".format(index),
                    encoding="utf-8",
                )
            plan = SkillRuntime(SkillCatalog.from_directory(root)).router.route(
                "audit", skill_budget_tokens=100
            )
            self.assertTrue(plan.selected)
            self.assertIn("trigger:audit", plan.selected[0].reasons)

    def test_common_terms_do_not_hide_a_specific_skill_or_join_trigger_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(20):
                name = "generic-{0:02}".format(index)
                skill = root / name / "SKILL.md"
                skill.parent.mkdir()
                skill.write_text(
                    "---\nname: {0}\ndescription: 批量核对通用任务。\ntriggers: [批量, 核对]\n---\n\nGeneric body.\n".format(name),
                    encoding="utf-8",
                )
            specific = root / "pdf-merger" / "SKILL.md"
            specific.parent.mkdir()
            specific.write_text(
                "---\nname: pdf-merger\ndescription: 合并 PDF 文档。\ntriggers: [合并, pdf]\n---\n\nPDF body.\n",
                encoding="utf-8",
            )
            runtime = SkillRuntime(SkillCatalog.from_directory(root))
            plan = runtime.router.route("请批量核对并合并 PDF 文件", budget_tokens=1_000)
            self.assertEqual(plan.selected[0].skill.name, "pdf-merger")
            self.assertNotIn("generic-00", [item.skill.name for item in plan.selected])
            candidates = plan.selected + plan.rejected
            self.assertFalse(any(
                "trigger:量核" in item.reasons
                for item in candidates
            ))

    def test_large_catalog_does_not_enter_model_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(100):
                name = "target-skill" if index == 42 else "bulk-skill-{0:03}".format(index)
                trigger = "needle-42" if index == 42 else "unrelated-{0}".format(index)
                skill = root / name / "SKILL.md"
                skill.parent.mkdir()
                skill.write_text(
                    "---\nname: {0}\ndescription: A sample capability for testing catalog scale.\ntriggers: [{1}]\n---\n\nSmall body.\n".format(name, trigger),
                    encoding="utf-8",
                )
            runtime = SkillRuntime(SkillCatalog.from_directory(root))
            result = runtime.run_turn("use needle-42", task_id="large", turn=0, budget_tokens=100)
            self.assertEqual(len(runtime.catalog), 100)
            self.assertEqual(result.active_skills, ("target-skill",))
            self.assertIn("<skill name=\"target-skill\">", result.context.request.text)
            self.assertNotIn("bulk-skill", result.context.request.text)

    def test_demo_cli_reports_actual_release_and_context_sizes(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--skills", str(SKILLS), "demo"])
        data = json.loads(output.getvalue())
        second = data["ephemeral_system"]["next_turn"]
        self.assertEqual(code, 0)
        self.assertEqual(second["physically_removed_skills"], ["web-research"])
        self.assertTrue(any(item["reason"] == "task_switched" for item in second["released"]))
        self.assertGreater(data["comparison"]["next_turn_chars_saved"], 0)
        self.assertFalse(data["comparison"]["old_skill_present_in_ephemeral_request"])
        self.assertTrue(data["comparison"]["old_skill_present_in_append_request"])


if __name__ == "__main__":
    unittest.main()
