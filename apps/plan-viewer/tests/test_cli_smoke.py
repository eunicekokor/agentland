from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import os


class CliSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.plans_dir = Path(self.tempdir.name)
        self.cli = Path(__file__).resolve().parents[1] / "plan_viewer.py"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_plan(self, date: str, slug: str, content: str) -> None:
        plan_path = self.plans_dir / date / slug / "plan.md"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(content, encoding="utf-8")

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.cli), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_list_show_dashboard_smoke(self) -> None:
        self._write_plan("2026-03-02", "abc", "# Hello\n- [ ] task\n")

        list_res = self._run("list", "--plans-dir", str(self.plans_dir))
        self.assertEqual(list_res.returncode, 0)
        self.assertIn("abc", list_res.stdout)

        show_res = self._run("show", "2026-03-02/abc", "--plans-dir", str(self.plans_dir))
        self.assertEqual(show_res.returncode, 0)
        self.assertIn("# Hello", show_res.stdout)

        dash_res = self._run("dashboard", "--plans-dir", str(self.plans_dir), "--watch", "0")
        self.assertEqual(dash_res.returncode, 0)
        self.assertIn("Slug", dash_res.stdout)

    def test_open_error_path(self) -> None:
        res = self._run("open", "missing", "--plans-dir", str(self.plans_dir), "--agent", "codex")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Plan not found", res.stderr)

    def test_ambiguous_ref_error_path(self) -> None:
        self._write_plan("2026-03-01", "dup", "# One\n")
        self._write_plan("2026-03-02", "dup", "# Two\n")

        res = self._run("show", "dup", "--plans-dir", str(self.plans_dir))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Ambiguous slug", res.stderr)

    def test_init_command_creates_directory(self) -> None:
        target = self.plans_dir / "new-plans-root"
        self.assertFalse(target.exists())
        res = self._run("init", "--plans-dir", str(target))
        self.assertEqual(res.returncode, 0)
        self.assertTrue(target.exists())
        self.assertIn("Initialized plans directory", res.stdout)

    def test_no_args_defaults_to_dashboard(self) -> None:
        res = self._run()
        self.assertEqual(res.returncode, 0)
        self.assertIn("No plans found", res.stdout)

    def test_new_creates_plan_and_mirror_without_cursor(self) -> None:
        mirror = self.plans_dir / "mirror"
        env = os.environ.copy()
        env["PLAN_VIEWER_MIRROR_DIR"] = str(mirror)
        res = subprocess.run(
            [
                sys.executable,
                str(self.cli),
                "new",
                "My CLI Plan",
                "--plans-dir",
                str(self.plans_dir),
                "--no-cursor",
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Created plan:", res.stdout)

        plans = list((self.plans_dir).glob("*/**/plan.md"))
        self.assertTrue(plans)

        mirrored = list(mirror.glob("*/**/plan.md"))
        self.assertTrue(mirrored)


if __name__ == "__main__":
    unittest.main()
