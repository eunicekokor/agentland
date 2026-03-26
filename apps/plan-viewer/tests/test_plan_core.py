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


if __name__ == "__main__":
    unittest.main()
