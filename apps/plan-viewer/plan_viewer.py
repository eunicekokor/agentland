#!/usr/bin/env python3
"""CLI + web server for plan-viewer."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from plan_core import (
    ALL_SOURCES,
    SOURCE_LOCAL,
    create_plan,
    get_plan_by_id,
    import_plan_to_local,
    lineage_for_plan,
    list_docs,
    list_docs_by_plan_id,
    list_plan_files,
    list_plan_files_by_plan_id,
    merge_plans,
    open_with_agent,
    read_plan,
    read_plan_by_id,
    plan_download_by_id,
    plan_download_legacy,
    resolve_mirror_dir,
    resolve_plan_ref,
    resolve_plans_dir,
    resolve_sources,
    scan_plans,
    serve_doc_file,
    serve_doc_file_by_plan_id,
    serve_plan_file,
    serve_plan_file_by_plan_id,
    spinout_plan,
    sync_plan_dir,
    write_plan,
    write_plan_by_id,
)

PORT = 8787
VIEWER_DIR = Path(__file__).resolve().parent
STATUS_ORDER = {"active": 0, "planned": 1, "unscored": 2, "complete": 3}


def _error(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def _parse_updated(updated_iso: str) -> datetime:
    try:
        return datetime.fromisoformat(updated_iso)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _format_relative_time(updated_iso: str) -> str:
    if not updated_iso:
        return "-"
    try:
        ts = datetime.fromisoformat(updated_iso)
    except ValueError:
        return "-"

    now = datetime.now(timezone.utc)
    diff = now - ts
    mins = int(diff.total_seconds() // 60)
    if mins < 1:
        return "now"
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    if days < 7:
        return f"{days}d"
    return ts.strftime("%Y-%m-%d")


def _format_progress(plan: dict) -> str:
    total = int(plan.get("task_total") or 0)
    done = int(plan.get("task_done") or 0)
    if total <= 0:
        return "-"
    return f"{done}/{total}"


def _plans_for_display(plans: list[dict]) -> list[dict]:
    return sorted(
        plans,
        key=lambda p: (
            STATUS_ORDER.get(str(p.get("status", "unscored")), 99),
            -_parse_updated(str(p.get("updated", "")).strip()).timestamp(),
        ),
    )


def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    if not rows:
        print("No plans found")
        return

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row))


def _filter_plans(plans: list[dict], date: str | None, limit: int | None) -> list[dict]:
    filtered = plans
    if date:
        filtered = [p for p in filtered if p.get("date") == date]
    if limit is not None and limit >= 0:
        filtered = filtered[:limit]
    return filtered


def _resolve_sources_arg(value: str | None) -> list[str]:
    return resolve_sources(value)


def _resolve_plan_for_cli(plans_dir: Path, plan_ref: str, sources: list[str]) -> dict:
    if plan_ref.startswith("pv_"):
        p = get_plan_by_id(plans_dir, plan_ref, sources=sources)
        if not p:
            raise ValueError(f"Plan not found: {plan_ref}")
        return p

    date, slug, plan_file = resolve_plan_ref(plans_dir, plan_ref, sources=sources)
    return {
        "plan_id": f"legacy:{date}/{slug}",
        "source": SOURCE_LOCAL,
        "date": date,
        "slug": slug,
        "path": str(plan_file),
    }


def cmd_list(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)
    plans = scan_plans(plans_dir, sources=sources)
    plans = _filter_plans(plans, args.date, args.limit)

    if args.json:
        print(json.dumps(plans, indent=2))
        return 0

    rows = []
    for p in plans:
        rows.append(
            [
                str(p.get("plan_id", ""))[:22],
                str(p.get("source", "")),
                f"{p.get('date','')}/{p.get('slug','')}",
                str(p.get("status", "unscored")),
                _format_progress(p),
                _format_relative_time(str(p.get("updated", ""))),
                str(p.get("title", "")),
            ]
        )
    _print_table(rows, ["Plan ID", "Source", "Ref", "Status", "Progress", "Updated", "Title"])
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)

    if args.plan_ref.startswith("pv_"):
        content = read_plan_by_id(plans_dir, args.plan_ref, sources=sources)
        if content is None:
            return _error(f"Plan not found: {args.plan_ref}")
        print(content)
        return 0

    try:
        date, slug, _ = resolve_plan_ref(plans_dir, args.plan_ref, sources=sources)
    except ValueError as e:
        return _error(str(e))

    content = read_plan(plans_dir, date, slug)
    if content is None:
        return _error(f"Plan not found: {args.plan_ref}")

    print(content)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)
    try:
        plan = _resolve_plan_for_cli(plans_dir, args.plan_ref, sources)
    except ValueError as e:
        return _error(str(e))

    result = open_with_agent(str(plan["path"]), args.agent)
    if not result.get("ok"):
        return _error(str(result.get("message", "Open failed")))
    print(result.get("message", "Opened"))
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)
    try:
        plan = _resolve_plan_for_cli(plans_dir, args.plan_ref, sources)
    except ValueError as e:
        return _error(str(e))

    if plan.get("source") != SOURCE_LOCAL:
        plan = import_plan_to_local(plans_dir, str(plan.get("plan_id")), sources=sources)

    editor = args.editor or os.environ.get("EDITOR") or "vi"
    cmd = shlex.split(editor) + [str(plan["path"])]
    result = os.spawnvp(os.P_WAIT, cmd[0], cmd)
    if int(result) == 0 and plan.get("date") and plan.get("slug"):
        sync_plan_dir(plans_dir, str(plan["date"]), str(plan["slug"]))
    return int(result)


def _dashboard_once(plans_dir: Path, limit: int | None, date: str | None, sources: list[str]) -> int:
    plans = scan_plans(plans_dir, sources=sources)
    plans = _filter_plans(plans, date, limit)
    plans = _plans_for_display(plans)

    rows = []
    for p in plans:
        rows.append(
            [
                str(p.get("plan_id", ""))[:16],
                str(p.get("source", "")),
                str(p.get("status", "unscored")),
                _format_progress(p),
                _format_relative_time(str(p.get("updated", ""))),
                str(p.get("lineage_rollup", {}).get("member_count", 1)),
                str(p.get("title", "")),
            ]
        )
    _print_table(rows, ["Plan ID", "Src", "Status", "Progress", "Updated", "Group", "Title"])
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)
    watch = float(args.watch or 0)

    if watch <= 0:
        return _dashboard_once(plans_dir, args.limit, args.date, sources)

    try:
        while True:
            print("\033[2J\033[H", end="")
            print(f"Plan Viewer Dashboard ({plans_dir})")
            print(f"Sources: {','.join(sources)}")
            print()
            _dashboard_once(plans_dir, args.limit, args.date, sources)
            print()
            print(f"Refreshing every {watch:.1f}s. Ctrl+C to stop.")
            time.sleep(watch)
    except KeyboardInterrupt:
        return 0


def _plan_share_url(
    plans_dir: Path,
    plan_ref: str,
    sources: list[str],
    port: int,
) -> str:
    if plan_ref.startswith("pv_"):
        plan = get_plan_by_id(plans_dir, plan_ref, sources=sources)
        if not plan:
            raise ValueError(f"Plan not found: {plan_ref}")
        return f"http://localhost:{port}/api/plan/{plan_ref}/download?sources={','.join(sources)}"

    date, slug, _ = resolve_plan_ref(plans_dir, plan_ref, sources=sources)
    for plan in scan_plans(plans_dir, sources=sources, include_lineage=False):
        if plan.get("source") in {SOURCE_LOCAL, "agent"} and plan.get("date") == date and plan.get("slug") == slug:
            plan_id = str(plan.get("plan_id", ""))
            if plan_id.startswith("pv_"):
                return f"http://localhost:{port}/api/plan/{plan_id}/download?sources={','.join(sources)}"

    return f"http://localhost:{port}/api/plans/{date}/{slug}/download"


def cmd_share(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)
    try:
        share_url = _plan_share_url(plans_dir, args.plan_ref, sources, args.port)
    except ValueError as e:
        return _error(str(e))

    print(share_url)
    if args.open:
        webbrowser.open(share_url)
    return 0


class PlanViewerHandler(http.server.BaseHTTPRequestHandler):
    plans_dir: Path = resolve_plans_dir(None)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        pass

    def _json_response(self, data: object, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, path: Path) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file_response(self, content: bytes, mime_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _file_response_with_cors(self, content: bytes, mime_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _download_response(self, content: bytes, filename: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _query_sources(self, parsed) -> list[str]:
        query = parse_qs(parsed.query)
        return resolve_sources(query.get("sources", [None])[0])

    def do_GET(self) -> None:
        parsed = urlparse(unquote(self.path))
        path = parsed.path

        if path in {"/", "/index.html"}:
            self._html_response(VIEWER_DIR / "index.html")
            return

        if path == "/api/plans":
            sources = self._query_sources(parsed)
            plans = scan_plans(self.plans_dir, sources=sources)
            query = parse_qs(parsed.query)
            lineage_for = query.get("lineage_for", [None])[0]
            if lineage_for:
                view = lineage_for_plan(self.plans_dir, lineage_for, sources=sources)
                group = set(view.get("group_ids", []))
                plans = [p for p in plans if p.get("plan_id") in group]
            self._json_response(plans)
            return

        m = re.match(r"^/api/plan/(pv_[^/]+)$", path)
        if m:
            plan_id = m.group(1)
            sources = self._query_sources(parsed)
            content = read_plan_by_id(self.plans_dir, plan_id, sources=sources)
            plan = get_plan_by_id(self.plans_dir, plan_id, sources=sources)
            if content is None or not plan:
                self._json_response({"error": "Not found"}, 404)
            else:
                self._json_response({"content": content, "plan": plan})
            return

        m = re.match(r"^/api/plan/(pv_[^/]+)/download$", path)
        if m:
            sources = self._query_sources(parsed)
            result = plan_download_by_id(self.plans_dir, m.group(1), sources=sources)
            if result is None:
                self.send_error(404)
            else:
                self._download_response(*result)
            return

        m = re.match(r"^/api/lineage/(pv_[^/]+)$", path)
        if m:
            plan_id = m.group(1)
            sources = self._query_sources(parsed)
            self._json_response(lineage_for_plan(self.plans_dir, plan_id, sources=sources))
            return

        # Legacy routes
        m = re.match(r"^/api/plans/([^/]+)/([^/]+)$", path)
        if m:
            content = read_plan(self.plans_dir, m.group(1), m.group(2))
            if content is None:
                self._json_response({"error": "Not found"}, 404)
            else:
                self._json_response({"content": content})
            return

        m = re.match(r"^/api/plans/([^/]+)/([^/]+)/download$", path)
        if m:
            result = plan_download_legacy(self.plans_dir, m.group(1), m.group(2))
            if result is None:
                self.send_error(404)
            else:
                self._download_response(*result)
            return

        m = re.match(r"^/api/plans/([^/]+)/([^/]+)/docs$", path)
        if m:
            docs = list_docs(self.plans_dir, m.group(1), m.group(2))
            self._json_response(docs)
            return

        m = re.match(r"^/api/plans/([^/]+)/([^/]+)/docs/([^/]+)$", path)
        if m:
            result = serve_doc_file(self.plans_dir, m.group(1), m.group(2), m.group(3))
            if result is None:
                self.send_error(404)
            else:
                self._file_response(*result)
            return

        m = re.match(r"^/api/plans/([^/]+)/([^/]+)/files$", path)
        if m:
            files = list_plan_files(self.plans_dir, m.group(1), m.group(2))
            self._json_response(files)
            return

        m = re.match(r"^/api/plans/([^/]+)/([^/]+)/files/([^/]+)$", path)
        if m:
            result = serve_plan_file(self.plans_dir, m.group(1), m.group(2), m.group(3))
            if result is None:
                self.send_error(404)
            else:
                self._file_response_with_cors(*result)
            return

        m = re.match(r"^/api/plan/(pv_[^/]+)/docs$", path)
        if m:
            sources = self._query_sources(parsed)
            self._json_response(list_docs_by_plan_id(self.plans_dir, m.group(1), sources=sources))
            return

        m = re.match(r"^/api/plan/(pv_[^/]+)/docs/([^/]+)$", path)
        if m:
            sources = self._query_sources(parsed)
            result = serve_doc_file_by_plan_id(self.plans_dir, m.group(1), m.group(2), sources=sources)
            if result is None:
                self.send_error(404)
            else:
                self._file_response(*result)
            return

        m = re.match(r"^/api/plan/(pv_[^/]+)/files$", path)
        if m:
            sources = self._query_sources(parsed)
            self._json_response(list_plan_files_by_plan_id(self.plans_dir, m.group(1), sources=sources))
            return

        m = re.match(r"^/api/plan/(pv_[^/]+)/files/([^/]+)$", path)
        if m:
            sources = self._query_sources(parsed)
            result = serve_plan_file_by_plan_id(self.plans_dir, m.group(1), m.group(2), sources=sources)
            if result is None:
                self.send_error(404)
            else:
                self._file_response_with_cors(*result)
            return

        self.send_error(404)

    def do_PUT(self) -> None:
        parsed = urlparse(unquote(self.path))
        path = parsed.path

        m = re.match(r"^/api/plan/(pv_[^/]+)$", path)
        if m:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            sources = self._query_sources(parsed)
            result = write_plan_by_id(self.plans_dir, m.group(1), body.get("content", ""), sources=sources)
            self._json_response(result, 200 if result.get("ok") else 404)
            return

        # Legacy
        m = re.match(r"^/api/plans/([^/]+)/([^/]+)$", path)
        if m:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            result = write_plan(self.plans_dir, m.group(1), m.group(2), body.get("content", ""))
            self._json_response(result)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(unquote(self.path))
        path = parsed.path

        if path == "/api/new":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                title = str(body.get("title", "")).strip()
                if not title:
                    self._json_response({"ok": False, "error": "Title is required"}, 400)
                    return

                launch_cursor = bool(body.get("launch_cursor", True))
                model = str(body.get("model", "opus"))
                date, slug, plan_file = create_plan(self.plans_dir, title)
                sync_plan_dir(self.plans_dir, date, slug, resolve_mirror_dir(self.plans_dir, None))

                message = "Created plan"
                if launch_cursor:
                    ok, launch_msg, _ = _launch_cursor_plan_agent(
                        plan_file=plan_file,
                        title=title,
                        model=model,
                        cwd=None,
                        wait=False,
                    )
                    if not ok:
                        self._json_response({"ok": False, "error": launch_msg}, 500)
                        return
                    message = launch_msg

                plan_id = ""
                for candidate in scan_plans(self.plans_dir, sources=[SOURCE_LOCAL], include_lineage=False):
                    if candidate.get("path") == str(plan_file):
                        plan_id = candidate.get("plan_id", "")
                        break

                self._json_response(
                    {
                        "ok": True,
                        "date": date,
                        "slug": slug,
                        "path": str(plan_file),
                        "plan_id": plan_id,
                        "source": SOURCE_LOCAL,
                        "message": message,
                    }
                )
            except json.JSONDecodeError:
                self._json_response({"ok": False, "error": "Invalid JSON"}, 400)
            return

        m = re.match(r"^/api/plan/(pv_[^/]+)/import$", path)
        if m:
            try:
                sources = self._query_sources(parsed)
                imported = import_plan_to_local(self.plans_dir, m.group(1), sources=sources)
                self._json_response({"ok": True, "plan": imported})
            except ValueError as e:
                self._json_response({"ok": False, "error": str(e)}, 404)
            return

        if path == "/api/lineage/merge":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                left = str(body.get("left_plan_id", "")).strip()
                right = str(body.get("right_plan_id", "")).strip()
                if not left or not right:
                    self._json_response({"ok": False, "error": "left_plan_id and right_plan_id are required"}, 400)
                    return
                sources = self._query_sources(parsed)
                result = merge_plans(self.plans_dir, left, right, sources=sources)
                self._json_response(result)
            except ValueError as e:
                self._json_response({"ok": False, "error": str(e)}, 404)
            return

        if path == "/api/lineage/spinout":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                parent_id = str(body.get("parent_plan_id", "")).strip()
                title = str(body.get("title", "")).strip()
                if not parent_id or not title:
                    self._json_response({"ok": False, "error": "parent_plan_id and title are required"}, 400)
                    return
                sources = self._query_sources(parsed)
                child = spinout_plan(self.plans_dir, parent_id, title, sources=sources)
                self._json_response({"ok": True, "child": child})
            except ValueError as e:
                self._json_response({"ok": False, "error": str(e)}, 404)
            return

        if path == "/api/open":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            result = open_with_agent(body["path"], body["agent"])
            self._json_response(result)
            return

        self.send_error(404)


def run_server(plans_dir: Path, port: int, open_browser: bool = True) -> int:
    PlanViewerHandler.plans_dir = plans_dir
    server = http.server.HTTPServer(("127.0.0.1", port), PlanViewerHandler)
    url = f"http://localhost:{port}"
    print(f"Plan viewer running at {url}")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()

    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    return run_server(plans_dir, args.port, args.open_browser)


def cmd_init(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    plans_dir.mkdir(parents=True, exist_ok=True)
    print(f"Initialized plans directory: {plans_dir}")
    mirror_dir = resolve_mirror_dir(plans_dir, args.mirror_dir)
    if mirror_dir is not None:
        mirror_dir.mkdir(parents=True, exist_ok=True)
        print(f"Mirror directory: {mirror_dir}")
    return 0


def _default_cursor_plan_prompt(plan_file: Path, title: str) -> str:
    return (
        f"Create and save a complete implementation plan in {plan_file}.\n"
        f"Plan title: {title}\n\n"
        "Requirements:\n"
        "- Fill the entire plan with concrete details (no TODO placeholders)\n"
        "- Include three mermaid diagrams: system overview, request/data flow, work breakdown\n"
        "- Include clear backlog checkboxes and acceptance criteria\n"
        "- Save directly to disk in that file path\n"
    )


def _open_in_cursor_fallback(path: Path) -> tuple[bool, str]:
    cursor_bin = shutil.which("cursor")
    if cursor_bin:
        subprocess.Popen([cursor_bin, str(path)])
        return True, f"Opened {path} in Cursor"
    try:
        subprocess.Popen(["open", "-a", "Cursor", str(path)])
        return True, f"Opened {path} in Cursor"
    except Exception as exc:
        return False, f"Could not launch Cursor: {exc}"


def _launch_cursor_plan_agent(
    plan_file: Path,
    title: str,
    model: str,
    cwd: str | None = None,
    wait: bool = True,
) -> tuple[bool, str, int]:
    prompt = _default_cursor_plan_prompt(plan_file, title)
    agent_bin = shutil.which("cursor-agent")
    if agent_bin:
        cmd = [agent_bin, "-m", model, prompt]
        if wait:
            rc = subprocess.run(cmd, cwd=cwd or None, check=False).returncode
            return rc == 0, f"cursor-agent exited with code {rc}", rc
        subprocess.Popen(cmd, cwd=cwd or None)
        return True, "Started cursor-agent", 0

    ok, msg = _open_in_cursor_fallback(plan_file)
    if ok:
        return True, "cursor-agent not found; opened file in Cursor for manual generation.", 0
    return False, msg, 1


def create_and_prepare_plan(
    plans_dir: Path,
    title: str,
    mirror_dir: Path | None,
) -> tuple[str, str, Path]:
    plans_dir.mkdir(parents=True, exist_ok=True)
    date, slug, plan_file = create_plan(plans_dir, title)
    sync_plan_dir(plans_dir, date, slug, mirror_dir)
    return date, slug, plan_file


def cmd_new(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    mirror_dir = resolve_mirror_dir(plans_dir, args.mirror_dir)
    date, slug, plan_file = create_and_prepare_plan(plans_dir, args.title, mirror_dir)

    plan_ref = f"{date}/{slug}"
    print(f"Created plan: {plan_ref}")
    print(f"Path: {plan_file}")

    if args.no_cursor:
        return 0

    ok, msg, rc = _launch_cursor_plan_agent(
        plan_file=plan_file,
        title=args.title,
        model=args.model,
        cwd=args.cwd,
        wait=True,
    )
    print(msg)
    return rc if not ok else 0


def cmd_merge(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)
    try:
        result = merge_plans(plans_dir, args.plan_id, args.with_plan_id, sources=sources)
    except ValueError as e:
        return _error(str(e))
    print(json.dumps(result, indent=2))
    return 0


def cmd_spinout(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)
    try:
        child = spinout_plan(plans_dir, args.plan_id, args.title, sources=sources)
    except ValueError as e:
        return _error(str(e))
    print(json.dumps({"ok": True, "child": child}, indent=2))
    return 0


def cmd_related(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    sources = _resolve_sources_arg(args.sources)
    print(json.dumps(lineage_for_plan(plans_dir, args.plan_id, sources=sources), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan Viewer CLI")
    subparsers = parser.add_subparsers(dest="command", required=False)

    init_cmd = subparsers.add_parser("init", help="Initialize local plans directory")
    init_cmd.add_argument("--plans-dir", help="Plans root directory")
    init_cmd.add_argument("--mirror-dir", help="Mirror plans directory (default: ~/agent-plans/plans)")
    init_cmd.set_defaults(func=cmd_init)

    new_cmd = subparsers.add_parser("new", help="Create a new plan and launch Cursor Opus flow")
    new_cmd.add_argument("title", help="Plan title")
    new_cmd.add_argument("--plans-dir", help="Plans root directory")
    new_cmd.add_argument("--mirror-dir", help="Mirror plans directory (default: ~/agent-plans/plans)")
    new_cmd.add_argument(
        "--model",
        default=os.environ.get("PLAN_VIEWER_CURSOR_MODEL", "opus"),
        help="Cursor model passed to cursor-agent -m (default: opus)",
    )
    new_cmd.add_argument("--cwd", help="Working directory for cursor-agent (defaults to current directory)")
    new_cmd.add_argument("--no-cursor", action="store_true", help="Only create plan files, do not launch Cursor")
    new_cmd.set_defaults(func=cmd_new)

    serve = subparsers.add_parser("serve", help="Run browser server")
    serve.add_argument("--plans-dir", help="Plans root directory")
    serve.add_argument("--port", type=int, default=PORT, help=f"Server port (default: {PORT})")
    browser_group = serve.add_mutually_exclusive_group()
    browser_group.add_argument("--open-browser", dest="open_browser", action="store_true", default=True)
    browser_group.add_argument("--no-open-browser", dest="open_browser", action="store_false")
    serve.set_defaults(func=cmd_serve)

    list_cmd = subparsers.add_parser("list", help="List plans")
    list_cmd.add_argument("--plans-dir", help="Plans root directory")
    list_cmd.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    list_cmd.add_argument("--date", help="Filter by YYYY-MM-DD")
    list_cmd.add_argument("--limit", type=int, default=50, help="Max rows")
    list_cmd.add_argument("--json", action="store_true", help="Output JSON")
    list_cmd.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="Print a plan")
    show.add_argument("plan_ref", help="plan_id, YYYY-MM-DD/slug, or unique slug")
    show.add_argument("--plans-dir", help="Plans root directory")
    show.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    show.set_defaults(func=cmd_show)

    share = subparsers.add_parser("share", help="Print downloadable local URL for a plan")
    share.add_argument("plan_ref", help="plan_id, YYYY-MM-DD/slug, or unique slug")
    share.add_argument("--plans-dir", help="Plans root directory")
    share.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    share.add_argument("--port", type=int, default=11444, help="Port used in generated localhost URL")
    share.add_argument("--open", action="store_true", help="Open the generated URL in the default browser")
    share.set_defaults(func=cmd_share)

    dashboard = subparsers.add_parser("dashboard", help="Arc-style dashboard")
    dashboard.add_argument("--plans-dir", help="Plans root directory")
    dashboard.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    dashboard.add_argument("--date", help="Filter by YYYY-MM-DD")
    dashboard.add_argument("--limit", type=int, default=50, help="Max rows")
    dashboard.add_argument("--watch", type=float, default=0, help="Refresh interval seconds (0 = once)")
    dashboard.set_defaults(func=cmd_dashboard)

    open_cmd = subparsers.add_parser("open", help="Open plan with agent")
    open_cmd.add_argument("plan_ref", help="plan_id, YYYY-MM-DD/slug, or unique slug")
    open_cmd.add_argument("--plans-dir", help="Plans root directory")
    open_cmd.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    open_cmd.add_argument("--agent", required=True, choices=["cursor", "claude", "codex"])
    open_cmd.set_defaults(func=cmd_open)

    edit = subparsers.add_parser("edit", help="Edit plan in terminal editor")
    edit.add_argument("plan_ref", help="plan_id, YYYY-MM-DD/slug, or unique slug")
    edit.add_argument("--plans-dir", help="Plans root directory")
    edit.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    edit.add_argument("--editor", help="Editor command (default: $EDITOR or vi)")
    edit.set_defaults(func=cmd_edit)

    merge_cmd = subparsers.add_parser("merge", help="Create a merge relationship between two plans")
    merge_cmd.add_argument("plan_id", help="Left plan_id")
    merge_cmd.add_argument("--with", dest="with_plan_id", required=True, help="Right plan_id")
    merge_cmd.add_argument("--plans-dir", help="Plans root directory")
    merge_cmd.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    merge_cmd.set_defaults(func=cmd_merge)

    spinout_cmd = subparsers.add_parser("spinout", help="Create a child plan linked to a parent plan")
    spinout_cmd.add_argument("plan_id", help="Parent plan_id")
    spinout_cmd.add_argument("--title", required=True, help="Child plan title")
    spinout_cmd.add_argument("--plans-dir", help="Plans root directory")
    spinout_cmd.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    spinout_cmd.set_defaults(func=cmd_spinout)

    related_cmd = subparsers.add_parser("related", help="Show lineage relationships for a plan")
    related_cmd.add_argument("plan_id", help="Plan id")
    related_cmd.add_argument("--plans-dir", help="Plans root directory")
    related_cmd.add_argument("--sources", help=f"Comma-separated sources (default: {','.join(ALL_SOURCES)})")
    related_cmd.set_defaults(func=cmd_related)

    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["dashboard"]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
