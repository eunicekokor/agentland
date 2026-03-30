from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
import sys

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import plan_core


class PlanCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.plans_dir = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_plan(self, date: str, slug: str, content: str) -> Path:
        plan_path = self.plans_dir / date / slug / "plan.md"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(content, encoding="utf-8")
        return plan_path

    def _with_source_roots(self, roots: dict[str, Path]):
        original = plan_core.source_roots
        plan_core.source_roots = lambda _local: roots  # type: ignore[assignment]
        self.addCleanup(lambda: setattr(plan_core, "source_roots", original))

    def test_scan_plans_sorted_newest_first(self) -> None:
        self._write_plan("2026-03-01", "2026-03-01_09-00_old", "# Old\n")
        self._write_plan("2026-03-02", "2026-03-02_10-00_new", "# New\n")

        plans = plan_core.scan_plans(self.plans_dir)

        self.assertEqual(plans[0]["slug"], "2026-03-02_10-00_new")
        self.assertEqual(plans[1]["slug"], "2026-03-01_09-00_old")

    def test_title_extraction_falls_back_to_slug(self) -> None:
        self._write_plan("2026-03-02", "2026-03-02_10-00_no-title", "No heading\n")

        plans = plan_core.scan_plans(self.plans_dir)

        self.assertEqual(plans[0]["title"], "2026-03-02_10-00_no-title")

    def test_checkbox_status_heuristics(self) -> None:
        self.assertEqual(plan_core.parse_task_stats("no tasks"), (0, 0))
        self.assertEqual(plan_core.derive_status(0, 0), "unscored")

        total, done = plan_core.parse_task_stats("- [ ] a\n- [ ] b\n")
        self.assertEqual((total, done), (2, 0))
        self.assertEqual(plan_core.derive_status(total, done), "planned")

        total, done = plan_core.parse_task_stats("- [x] a\n- [ ] b\n")
        self.assertEqual((total, done), (2, 1))
        self.assertEqual(plan_core.derive_status(total, done), "active")

        total, done = plan_core.parse_task_stats("- [x] a\n- [X] b\n")
        self.assertEqual((total, done), (2, 2))
        self.assertEqual(plan_core.derive_status(total, done), "complete")

    def test_resolve_plan_ref_unique_missing_ambiguous(self) -> None:
        self._write_plan("2026-03-01", "dup", "# One\n")
        self._write_plan("2026-03-02", "dup", "# Two\n")
        self._write_plan("2026-03-03", "solo", "# Solo\n")

        date, slug, _ = plan_core.resolve_plan_ref(self.plans_dir, "solo")
        self.assertEqual((date, slug), ("2026-03-03", "solo"))

        with self.assertRaisesRegex(ValueError, "Ambiguous slug"):
            plan_core.resolve_plan_ref(self.plans_dir, "dup")

        with self.assertRaisesRegex(ValueError, "Plan not found"):
            plan_core.resolve_plan_ref(self.plans_dir, "missing")

    def test_resolve_plans_dir_precedence(self) -> None:
        prev = os.environ.get("PLAN_VIEWER_PLANS_DIR")
        try:
            os.environ["PLAN_VIEWER_PLANS_DIR"] = "/tmp/from-env"
            self.assertEqual(plan_core.resolve_plans_dir(None), Path("/tmp/from-env"))
            self.assertEqual(plan_core.resolve_plans_dir("/tmp/from-cli"), Path("/tmp/from-cli"))
        finally:
            if prev is None:
                os.environ.pop("PLAN_VIEWER_PLANS_DIR", None)
            else:
                os.environ["PLAN_VIEWER_PLANS_DIR"] = prev

    def test_resolve_plans_dir_defaults_to_repo_local(self) -> None:
        prev = os.environ.get("PLAN_VIEWER_PLANS_DIR")
        try:
            os.environ.pop("PLAN_VIEWER_PLANS_DIR", None)
            self.assertEqual(plan_core.resolve_plans_dir(None), plan_core.DEFAULT_PLANS_DIR)
        finally:
            if prev is not None:
                os.environ["PLAN_VIEWER_PLANS_DIR"] = prev

    def test_create_plan_and_auto_sync_on_write(self) -> None:
        mirror = Path(self.tempdir.name) / "mirror"
        prev_mirror = os.environ.get("PLAN_VIEWER_MIRROR_DIR")
        try:
            os.environ["PLAN_VIEWER_MIRROR_DIR"] = str(mirror)
            date, slug, plan_file = plan_core.create_plan(self.plans_dir, "My Plan")
            self.assertTrue(plan_file.exists())

            result = plan_core.write_plan(self.plans_dir, date, slug, "# Updated\n")
            self.assertTrue(result["ok"])

            mirrored = mirror / date / slug / "plan.md"
            self.assertTrue(mirrored.exists())
            self.assertIn("Updated", mirrored.read_text(encoding="utf-8"))
        finally:
            if prev_mirror is None:
                os.environ.pop("PLAN_VIEWER_MIRROR_DIR", None)
            else:
                os.environ["PLAN_VIEWER_MIRROR_DIR"] = prev_mirror

    def test_scan_plans_multi_source_and_plan_id(self) -> None:
        local = self.plans_dir / "local"
        agent = self.plans_dir / "agent"
        cursor = self.plans_dir / "cursor"
        claude = self.plans_dir / "claude"
        self._with_source_roots(
            {
                plan_core.SOURCE_LOCAL: local,
                plan_core.SOURCE_AGENT: agent,
                plan_core.SOURCE_CURSOR: cursor,
                plan_core.SOURCE_CLAUDE: claude,
            }
        )

        (local / "2026-03-01" / "a").mkdir(parents=True, exist_ok=True)
        (local / "2026-03-01" / "a" / "plan.md").write_text("# Local\n- [ ] x\n", encoding="utf-8")
        (agent / "2026-03-02" / "b").mkdir(parents=True, exist_ok=True)
        (agent / "2026-03-02" / "b" / "plan.md").write_text("# Agent\n- [x] y\n", encoding="utf-8")
        cursor.mkdir(parents=True, exist_ok=True)
        (cursor / "c.plan.md").write_text("# Cursor\n", encoding="utf-8")
        claude.mkdir(parents=True, exist_ok=True)
        (claude / "d.md").write_text("# Claude\n", encoding="utf-8")

        plans = plan_core.scan_plans(local, sources=list(plan_core.ALL_SOURCES), include_lineage=False)
        sources = {p["source"] for p in plans}
        self.assertTrue({"local", "agent", "cursor", "claude"}.issubset(sources))
        self.assertTrue(all(str(p.get("plan_id", "")).startswith("pv_") for p in plans))

    def test_lineage_merge_and_group(self) -> None:
        store = {"plans": {}}
        plan_core.add_merge_relation(store, "a", "b")
        plan_core.add_parent_child_relation(store, "b", "c")

        rel_a = plan_core.lineage_related(store, "a")
        self.assertIn("b", rel_a["merged_with"])
        group = plan_core.lineage_group_ids(store, "a")
        self.assertEqual(group, ["a", "b", "c"])

    def test_write_plan_by_id_imports_external(self) -> None:
        local = self.plans_dir / "local"
        cursor = self.plans_dir / "cursor"
        self._with_source_roots(
            {
                plan_core.SOURCE_LOCAL: local,
                plan_core.SOURCE_AGENT: self.plans_dir / "agent-missing",
                plan_core.SOURCE_CURSOR: cursor,
                plan_core.SOURCE_CLAUDE: self.plans_dir / "claude-missing",
            }
        )

        prev_mirror = os.environ.get("PLAN_VIEWER_MIRROR_DIR")
        try:
            os.environ["PLAN_VIEWER_MIRROR_DIR"] = str(local)
            cursor.mkdir(parents=True, exist_ok=True)
            source_file = cursor / "external.plan.md"
            source_file.write_text("# External\n", encoding="utf-8")

            plans = plan_core.scan_plans(local, sources=[plan_core.SOURCE_CURSOR], include_lineage=False)
            ext_id = plans[0]["plan_id"]
            res = plan_core.write_plan_by_id(local, ext_id, "# Updated Imported\n", sources=[plan_core.SOURCE_CURSOR])
            self.assertTrue(res["ok"])
            self.assertTrue(res["imported"])

            imported_plans = plan_core.scan_plans(local, sources=[plan_core.SOURCE_LOCAL], include_lineage=False)
            self.assertTrue(imported_plans)
        finally:
            if prev_mirror is None:
                os.environ.pop("PLAN_VIEWER_MIRROR_DIR", None)
            else:
                os.environ["PLAN_VIEWER_MIRROR_DIR"] = prev_mirror


if __name__ == "__main__":
    unittest.main()
