#!/usr/bin/env python3
"""Zero-dependency local server for browsing ~/agent-plans/."""

import http.server
import json
import mimetypes
import os
import re
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

REPO_DIR = Path.home() / "agent-plans"
PLANS_DIR = REPO_DIR / "plans"
VIEWER_DIR = Path(__file__).resolve().parent
PORT = 8787

SAFE_EXTENSIONS = {
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".pdf", ".md", ".txt", ".json", ".html",
}


def scan_plans():
    """Walk ~/agent-plans/ and return a sorted list of plan metadata."""
    plans = []
    for day_dir in sorted(PLANS_DIR.iterdir(), reverse=True):
        if not day_dir.is_dir() or day_dir.name.startswith("."):
            continue
        for plan_dir in sorted(day_dir.iterdir(), reverse=True):
            if not plan_dir.is_dir():
                continue
            plan_file = plan_dir / "plan.md"
            if not plan_file.exists():
                continue

            title = plan_dir.name
            try:
                with open(plan_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("# "):
                            title = line[2:].strip()
                            break
            except OSError:
                pass

            parts = plan_dir.name.split("_", 2)
            time_str = parts[1] if len(parts) >= 2 else ""

            docs_dir = plan_dir / "docs"
            doc_files = []
            if docs_dir.is_dir():
                doc_files = [
                    f.name for f in sorted(docs_dir.iterdir())
                    if f.is_file() and f.suffix.lower() in SAFE_EXTENSIONS
                ]

            updated_ts = plan_file.stat().st_mtime
            updated_iso = datetime.fromtimestamp(updated_ts, tz=timezone.utc).isoformat()

            plans.append(
                {
                    "date": day_dir.name,
                    "time": time_str,
                    "slug": plan_dir.name,
                    "title": title,
                    "path": str(plan_file),
                    "docs": doc_files,
                    "updated": updated_iso,
                }
            )
    return plans


def read_plan(date: str, slug: str) -> str | None:
    plan_file = PLANS_DIR / date / slug / "plan.md"
    if not plan_file.exists():
        return None
    return plan_file.read_text()


def write_plan(date: str, slug: str, content: str) -> dict:
    plan_file = PLANS_DIR / date / slug / "plan.md"
    if not plan_file.exists():
        return {"ok": False, "error": "Plan not found"}
    try:
        plan_file.write_text(content)
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def list_docs(date: str, slug: str) -> list[dict]:
    docs_dir = PLANS_DIR / date / slug / "docs"
    if not docs_dir.is_dir():
        return []
    files = []
    for f in sorted(docs_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in SAFE_EXTENSIONS:
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "type": mimetypes.guess_type(f.name)[0] or "application/octet-stream",
            })
    return files


def serve_doc_file(date: str, slug: str, filename: str) -> tuple[bytes, str] | None:
    """Safely serve a file from the docs/ folder. Returns (content, mime_type) or None."""
    if ".." in filename or "/" in filename:
        return None
    doc_path = PLANS_DIR / date / slug / "docs" / filename
    if not doc_path.is_file() or doc_path.suffix.lower() not in SAFE_EXTENSIONS:
        return None
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return doc_path.read_bytes(), mime_type


def open_with_agent(path: str, agent: str) -> dict:
    """Open a plan with the chosen agent."""
    if agent == "cursor":
        subprocess.Popen(["cursor", path])
        return {"ok": True, "message": f"Opened {path} in Cursor"}

    if agent == "claude":
        try:
            content = Path(path).read_text()
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(content.encode())
        except Exception:
            pass
        subprocess.Popen(["open", "https://claude.ai/new"])
        return {
            "ok": True,
            "message": "Plan copied to clipboard. Paste it into Claude.",
        }

    if agent == "codex":
        try:
            content = Path(path).read_text()
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(content.encode())
        except Exception:
            pass
        subprocess.Popen(["open", "https://chatgpt.com/?model=o3"])
        return {
            "ok": True,
            "message": "Plan copied to clipboard. Paste it into Codex/ChatGPT.",
        }

    return {"ok": False, "message": f"Unknown agent: {agent}"}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, path: Path):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file_response(self, content: bytes, mime_type: str):
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = unquote(self.path)

        if path == "/" or path == "/index.html":
            self._html_response(VIEWER_DIR / "index.html")
            return

        if path == "/api/plans":
            self._json_response(scan_plans())
            return

        # GET /api/plans/<date>/<slug> — plan content
        m = re.match(r"^/api/plans/([^/]+)/([^/]+)$", path)
        if m:
            content = read_plan(m.group(1), m.group(2))
            if content is None:
                self._json_response({"error": "Not found"}, 404)
            else:
                self._json_response({"content": content})
            return

        # GET /api/plans/<date>/<slug>/docs — list docs
        m = re.match(r"^/api/plans/([^/]+)/([^/]+)/docs$", path)
        if m:
            docs = list_docs(m.group(1), m.group(2))
            self._json_response(docs)
            return

        # GET /api/plans/<date>/<slug>/docs/<filename> — serve a doc file
        m = re.match(r"^/api/plans/([^/]+)/([^/]+)/docs/([^/]+)$", path)
        if m:
            result = serve_doc_file(m.group(1), m.group(2), m.group(3))
            if result is None:
                self.send_error(404)
            else:
                self._file_response(*result)
            return

        self.send_error(404)

    def do_PUT(self):
        path = unquote(self.path)

        # PUT /api/plans/<date>/<slug> — save plan content
        m = re.match(r"^/api/plans/([^/]+)/([^/]+)$", path)
        if m:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            result = write_plan(m.group(1), m.group(2), body.get("content", ""))
            self._json_response(result)
            return

        self.send_error(404)

    def do_POST(self):
        path = unquote(self.path)

        if path == "/api/open":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            result = open_with_agent(body["path"], body["agent"])
            self._json_response(result)
            return

        self.send_error(404)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    print(f"Plan viewer running at {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
