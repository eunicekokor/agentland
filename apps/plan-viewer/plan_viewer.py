#!/usr/bin/env python3
"""CLI + web server for plan-viewer."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import shlex
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from plan_core import (
    list_docs,
    list_plan_files,
    open_with_agent,
    read_plan,
    resolve_plan_ref,
    resolve_plans_dir,
    scan_plans,
    serve_doc_file,
    serve_plan_file,
    write_plan,
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


def cmd_list(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    plans = scan_plans(plans_dir)
    plans = _filter_plans(plans, args.date, args.limit)

    if args.json:
        print(json.dumps(plans, indent=2))
        return 0

    rows = []
    for p in plans:
        rows.append(
            [
                f"{p['date']}/{p['slug']}",
                str(p.get("status", "unscored")),
                _format_progress(p),
                str(p.get("docs_count", 0)),
                _format_relative_time(str(p.get("updated", ""))),
                str(p.get("title", "")),
            ]
        )
    _print_table(rows, ["Plan", "Status", "Progress", "Docs", "Updated", "Title"])
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    try:
        date, slug, _ = resolve_plan_ref(plans_dir, args.plan_ref)
    except ValueError as e:
        return _error(str(e))

    content = read_plan(plans_dir, date, slug)
    if content is None:
        return _error(f"Plan not found: {args.plan_ref}")

    print(content)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    try:
        _, _, plan_file = resolve_plan_ref(plans_dir, args.plan_ref)
    except ValueError as e:
        return _error(str(e))

    result = open_with_agent(str(plan_file), args.agent)
    if not result.get("ok"):
        return _error(str(result.get("message", "Open failed")))
    print(result.get("message", "Opened"))
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    try:
        _, _, plan_file = resolve_plan_ref(plans_dir, args.plan_ref)
    except ValueError as e:
        return _error(str(e))

    editor = args.editor or os.environ.get("EDITOR") or "vi"
    cmd = shlex.split(editor) + [str(plan_file)]
    result = os.spawnvp(os.P_WAIT, cmd[0], cmd)
    return int(result)


def _dashboard_once(plans_dir: Path, limit: int | None, date: str | None) -> int:
    plans = scan_plans(plans_dir)
    plans = _filter_plans(plans, date, limit)
    plans = _plans_for_display(plans)

    rows = []
    for p in plans:
        rows.append(
            [
                p.get("slug", ""),
                str(p.get("status", "unscored")),
                _format_progress(p),
                _format_relative_time(str(p.get("updated", ""))),
                str(p.get("docs_count", 0)),
                str(p.get("title", "")),
            ]
        )
    _print_table(rows, ["Slug", "Status", "Progress", "Updated", "Docs", "Title"])
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    plans_dir = resolve_plans_dir(args.plans_dir)
    watch = float(args.watch or 0)

    if watch <= 0:
        return _dashboard_once(plans_dir, args.limit, args.date)

    try:
        while True:
            print("\033[2J\033[H", end="")
            print(f"Plan Viewer Dashboard ({plans_dir})")
            print()
            _dashboard_once(plans_dir, args.limit, args.date)
            print()
            print(f"Refreshing every {watch:.1f}s. Ctrl+C to stop.")
            time.sleep(watch)
    except KeyboardInterrupt:
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

    def do_GET(self) -> None:
        path = unquote(self.path)

        if path in {"/", "/index.html"}:
            self._html_response(VIEWER_DIR / "index.html")
            return

        if path == "/api/plans":
            self._json_response(scan_plans(self.plans_dir))
            return

        m = re.match(r"^/api/plans/([^/]+)/([^/]+)$", path)
        if m:
            content = read_plan(self.plans_dir, m.group(1), m.group(2))
            if content is None:
                self._json_response({"error": "Not found"}, 404)
            else:
                self._json_response({"content": content})
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

        self.send_error(404)

    def do_PUT(self) -> None:
        path = unquote(self.path)
        m = re.match(r"^/api/plans/([^/]+)/([^/]+)$", path)
        if m:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            result = write_plan(self.plans_dir, m.group(1), m.group(2), body.get("content", ""))
            self._json_response(result)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        path = unquote(self.path)

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan Viewer CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run browser server")
    serve.add_argument("--plans-dir", help="Plans root directory")
    serve.add_argument("--port", type=int, default=PORT, help=f"Server port (default: {PORT})")
    browser_group = serve.add_mutually_exclusive_group()
    browser_group.add_argument("--open-browser", dest="open_browser", action="store_true", default=True)
    browser_group.add_argument("--no-open-browser", dest="open_browser", action="store_false")
    serve.set_defaults(func=cmd_serve)

    list_cmd = subparsers.add_parser("list", help="List plans")
    list_cmd.add_argument("--plans-dir", help="Plans root directory")
    list_cmd.add_argument("--date", help="Filter by YYYY-MM-DD")
    list_cmd.add_argument("--limit", type=int, default=50, help="Max rows")
    list_cmd.add_argument("--json", action="store_true", help="Output JSON")
    list_cmd.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="Print a plan")
    show.add_argument("plan_ref", help="YYYY-MM-DD/slug or unique slug")
    show.add_argument("--plans-dir", help="Plans root directory")
    show.set_defaults(func=cmd_show)

    dashboard = subparsers.add_parser("dashboard", help="Arc-style dashboard")
    dashboard.add_argument("--plans-dir", help="Plans root directory")
    dashboard.add_argument("--date", help="Filter by YYYY-MM-DD")
    dashboard.add_argument("--limit", type=int, default=50, help="Max rows")
    dashboard.add_argument("--watch", type=float, default=0, help="Refresh interval seconds (0 = once)")
    dashboard.set_defaults(func=cmd_dashboard)

    open_cmd = subparsers.add_parser("open", help="Open plan with agent")
    open_cmd.add_argument("plan_ref", help="YYYY-MM-DD/slug or unique slug")
    open_cmd.add_argument("--plans-dir", help="Plans root directory")
    open_cmd.add_argument("--agent", required=True, choices=["cursor", "claude", "codex"])
    open_cmd.set_defaults(func=cmd_open)

    edit = subparsers.add_parser("edit", help="Edit plan in terminal editor")
    edit.add_argument("plan_ref", help="YYYY-MM-DD/slug or unique slug")
    edit.add_argument("--plans-dir", help="Plans root directory")
    edit.add_argument("--editor", help="Editor command (default: $EDITOR or vi)")
    edit.set_defaults(func=cmd_edit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
