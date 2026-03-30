from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
import os
from urllib.error import HTTPError
from urllib.request import urlopen


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
        env = os.environ.copy()
        env["PLAN_VIEWER_PLANS_DIR"] = str(self.plans_dir)
        env["PLAN_VIEWER_SOURCES"] = "local"
        env["PLAN_VIEWER_MIRROR_DIR"] = str(self.plans_dir)
        env["PLAN_VIEWER_LINEAGE_STORE"] = str(self.plans_dir / ".lineage.json")
        return subprocess.run(
            [sys.executable, str(self.cli), *args],
            text=True,
            capture_output=True,
            check=False,
            env=env,
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
        self.assertIn("Plan ID", dash_res.stdout)

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
        env["PLAN_VIEWER_PLANS_DIR"] = str(self.plans_dir)
        env["PLAN_VIEWER_SOURCES"] = "local"
        env["PLAN_VIEWER_LINEAGE_STORE"] = str(self.plans_dir / ".lineage.json")
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

    def test_merge_spinout_related_commands(self) -> None:
        self._write_plan("2026-03-01", "p1", "# One\n- [ ] a\n")
        self._write_plan("2026-03-02", "p2", "# Two\n- [x] b\n")

        list_res = self._run("list", "--plans-dir", str(self.plans_dir), "--json")
        self.assertEqual(list_res.returncode, 0)
        plans = json.loads(list_res.stdout)
        ids = [p["plan_id"] for p in plans[:2]]
        self.assertEqual(len(ids), 2)

        merge_res = self._run(
            "merge",
            ids[0],
            "--with",
            ids[1],
            "--plans-dir",
            str(self.plans_dir),
        )
        self.assertEqual(merge_res.returncode, 0)
        self.assertIn("\"ok\": true", merge_res.stdout.lower())

        spin_res = self._run(
            "spinout",
            ids[0],
            "--title",
            "Child Plan",
            "--plans-dir",
            str(self.plans_dir),
        )
        self.assertEqual(spin_res.returncode, 0)
        self.assertIn("\"child\"", spin_res.stdout)

        rel_res = self._run("related", ids[0], "--plans-dir", str(self.plans_dir))
        self.assertEqual(rel_res.returncode, 0)
        self.assertIn("\"group_ids\"", rel_res.stdout)

    def test_share_command_outputs_download_url_and_errors(self) -> None:
        self._write_plan("2026-03-02", "abc", "# Hello\n")
        list_res = self._run("list", "--plans-dir", str(self.plans_dir), "--json")
        self.assertEqual(list_res.returncode, 0)
        plan_id = json.loads(list_res.stdout)[0]["plan_id"]

        by_id = self._run("share", plan_id, "--plans-dir", str(self.plans_dir), "--port", "11444")
        self.assertEqual(by_id.returncode, 0)
        self.assertIn("/api/plan/", by_id.stdout)
        self.assertIn("/download", by_id.stdout)

        by_ref = self._run("share", "2026-03-02/abc", "--plans-dir", str(self.plans_dir), "--port", "11444")
        self.assertEqual(by_ref.returncode, 0)
        self.assertIn("/download", by_ref.stdout)

        missing = self._run("share", "missing", "--plans-dir", str(self.plans_dir))
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Plan not found", missing.stderr)

    def test_download_api_endpoints(self) -> None:
        self._write_plan("2026-03-02", "abc", "# Hello\n")
        list_res = self._run("list", "--plans-dir", str(self.plans_dir), "--json")
        self.assertEqual(list_res.returncode, 0)
        plan_id = json.loads(list_res.stdout)[0]["plan_id"]

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
        except PermissionError:
            sock.close()
            self.skipTest("Local socket binding not permitted in this sandbox")
        port = sock.getsockname()[1]
        sock.close()

        env = os.environ.copy()
        env["PLAN_VIEWER_PLANS_DIR"] = str(self.plans_dir)
        env["PLAN_VIEWER_SOURCES"] = "local"
        env["PLAN_VIEWER_MIRROR_DIR"] = str(self.plans_dir)
        env["PLAN_VIEWER_LINEAGE_STORE"] = str(self.plans_dir / ".lineage.json")
        proc = subprocess.Popen(
            [
                sys.executable,
                str(self.cli),
                "serve",
                "--plans-dir",
                str(self.plans_dir),
                "--port",
                str(port),
                "--no-open-browser",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            time.sleep(0.5)
            with urlopen(f"http://127.0.0.1:{port}/api/plan/{plan_id}/download?sources=local", timeout=3) as resp:
                body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertIn("# Hello", body)
                self.assertEqual(resp.headers.get_content_type(), "text/markdown")
                self.assertIn("attachment;", str(resp.headers.get("Content-Disposition", "")))

            with urlopen(f"http://127.0.0.1:{port}/api/plans/2026-03-02/abc/download", timeout=3) as resp:
                body = resp.read().decode("utf-8")
                self.assertEqual(resp.status, 200)
                self.assertIn("# Hello", body)

            with self.assertRaises(HTTPError) as ctx:
                urlopen(f"http://127.0.0.1:{port}/api/plan/pv_local_missing/download?sources=local", timeout=3)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    unittest.main()
