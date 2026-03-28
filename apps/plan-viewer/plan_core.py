#!/usr/bin/env python3
"""Shared plan-viewer core logic for CLI and web server."""

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLANS_DIR = REPO_ROOT / "plans"
DEFAULT_MIRROR_DIR = Path.home() / "agent-plans" / "plans"
SAFE_EXTENSIONS = {
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".pdf",
    ".md",
    ".txt",
    ".json",
    ".html",
    ".excalidraw",
}

_CHECKBOX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)\[([ xX])\]", re.MULTILINE)


def resolve_plans_dir(cli_plans_dir: str | None = None) -> Path:
    """Resolve plans directory with precedence: CLI > ENV > local-app default."""
    if cli_plans_dir:
        return Path(cli_plans_dir).expanduser()
    env_value = os.environ.get("PLAN_VIEWER_PLANS_DIR")
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_PLANS_DIR


def resolve_mirror_dir(
    plans_dir: Path,
    cli_mirror_dir: str | None = None,
) -> Path | None:
    """Resolve optional mirror directory for auto-sync."""
    if cli_mirror_dir:
        candidate = Path(cli_mirror_dir).expanduser()
    else:
        env_value = os.environ.get("PLAN_VIEWER_MIRROR_DIR")
        candidate = Path(env_value).expanduser() if env_value else DEFAULT_MIRROR_DIR

    if candidate.resolve() == plans_dir.resolve():
        return None
    return candidate


def sync_plan_dir(
    plans_dir: Path,
    date: str,
    slug: str,
    mirror_dir: Path | None = None,
) -> None:
    """Mirror one plan directory to mirror root."""
    mirror = mirror_dir or resolve_mirror_dir(plans_dir)
    if mirror is None:
        return

    src = plans_dir / date / slug
    if not src.exists():
        return

    dest = mirror / date / slug
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)


def extract_title(plan_file: Path, content: str) -> str:
    """Extract first markdown H1 title, fallback to folder slug."""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return plan_file.parent.name


def parse_task_stats(content: str) -> tuple[int, int]:
    """Parse markdown checkbox tasks from any section."""
    matches = _CHECKBOX_RE.findall(content)
    total = len(matches)
    done = sum(1 for v in matches if v.lower() == "x")
    return total, done


def derive_status(task_total: int, task_done: int) -> str:
    """Derive status from checkbox counts."""
    if task_total <= 0:
        return "unscored"
    if task_done <= 0:
        return "planned"
    if task_done >= task_total:
        return "complete"
    return "active"


def _plan_time(slug: str) -> str:
    parts = slug.split("_", 2)
    if len(parts) >= 2:
        return parts[1]
    return ""


def scan_plans(plans_dir: Path) -> list[dict]:
    """Walk plan directory and return sorted plan metadata."""
    plans: list[dict] = []
    if not plans_dir.exists() or not plans_dir.is_dir():
        return plans

    for day_dir in sorted(plans_dir.iterdir(), reverse=True):
        if not day_dir.is_dir() or day_dir.name.startswith("."):
            continue

        for plan_dir in sorted(day_dir.iterdir(), reverse=True):
            if not plan_dir.is_dir():
                continue

            plan_file = plan_dir / "plan.md"
            if not plan_file.exists():
                continue

            try:
                content = plan_file.read_text(encoding="utf-8")
            except OSError:
                continue

            title = extract_title(plan_file, content)
            task_total, task_done = parse_task_stats(content)
            status = derive_status(task_total, task_done)

            docs_dir = plan_dir / "docs"
            docs: list[str] = []
            if docs_dir.is_dir():
                docs = [
                    f.name
                    for f in sorted(docs_dir.iterdir())
                    if f.is_file() and f.suffix.lower() in SAFE_EXTENSIONS
                ]

            updated_iso = datetime.fromtimestamp(
                plan_file.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat()

            plans.append(
                {
                    "date": day_dir.name,
                    "time": _plan_time(plan_dir.name),
                    "slug": plan_dir.name,
                    "title": title,
                    "path": str(plan_file),
                    "docs": docs,
                    "docs_count": len(docs),
                    "updated": updated_iso,
                    "task_total": task_total,
                    "task_done": task_done,
                    "status": status,
                }
            )

    return plans


def read_plan(plans_dir: Path, date: str, slug: str) -> str | None:
    plan_file = plans_dir / date / slug / "plan.md"
    if not plan_file.exists():
        return None
    return plan_file.read_text(encoding="utf-8")


def write_plan(plans_dir: Path, date: str, slug: str, content: str) -> dict:
    plan_file = plans_dir / date / slug / "plan.md"
    if not plan_file.exists():
        return {"ok": False, "error": "Plan not found"}
    try:
        plan_file.write_text(content, encoding="utf-8")
        sync_plan_dir(plans_dir, date, slug)
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def list_docs(plans_dir: Path, date: str, slug: str) -> list[dict]:
    docs_dir = plans_dir / date / slug / "docs"
    if not docs_dir.is_dir():
        return []

    files: list[dict] = []
    for f in sorted(docs_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in SAFE_EXTENSIONS:
            files.append(
                {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "type": mimetypes.guess_type(f.name)[0] or "application/octet-stream",
                }
            )
    return files


def serve_doc_file(plans_dir: Path, date: str, slug: str, filename: str) -> tuple[bytes, str] | None:
    """Safely serve a file from the docs/ folder."""
    if ".." in filename or "/" in filename or "\\" in filename:
        return None

    doc_path = plans_dir / date / slug / "docs" / filename
    if not doc_path.is_file() or doc_path.suffix.lower() not in SAFE_EXTENSIONS:
        return None

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return doc_path.read_bytes(), mime_type


def list_plan_files(plans_dir: Path, date: str, slug: str) -> list[dict]:
    """List .excalidraw files in the plan directory."""
    plan_dir = plans_dir / date / slug
    if not plan_dir.is_dir():
        return []

    files: list[dict] = []
    for f in sorted(plan_dir.iterdir()):
        if f.is_file() and f.suffix.lower() == ".excalidraw":
            files.append({"name": f.name, "size": f.stat().st_size})
    return files


def serve_plan_file(plans_dir: Path, date: str, slug: str, filename: str) -> tuple[bytes, str] | None:
    """Safely serve a .excalidraw file from plan directory."""
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    if not filename.lower().endswith(".excalidraw"):
        return None

    file_path = plans_dir / date / slug / filename
    if not file_path.is_file():
        return None

    return file_path.read_bytes(), "application/json"


def open_with_agent(path: str, agent: str) -> dict:
    """Open plan with selected tool (macOS-oriented flows)."""
    if agent == "cursor":
        subprocess.Popen(["cursor", path])
        return {"ok": True, "message": f"Opened {path} in Cursor"}

    if agent == "claude":
        try:
            content = Path(path).read_text(encoding="utf-8")
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(content.encode())
        except Exception:
            pass
        subprocess.Popen(["open", "https://claude.ai/new"])
        return {"ok": True, "message": "Plan copied to clipboard. Paste it into Claude."}

    if agent == "codex":
        try:
            content = Path(path).read_text(encoding="utf-8")
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(content.encode())
        except Exception:
            pass
        subprocess.Popen(["open", "https://chatgpt.com/?model=o3"])
        return {"ok": True, "message": "Plan copied to clipboard. Paste it into Codex/ChatGPT."}

    return {"ok": False, "message": f"Unknown agent: {agent}"}


def resolve_plan_ref(plans_dir: Path, plan_ref: str) -> tuple[str, str, Path]:
    """Resolve a plan ref.

    Supported forms:
    - YYYY-MM-DD/slug
    - slug (must be unique across all days)
    """
    if "/" in plan_ref:
        date, slug = plan_ref.split("/", 1)
        plan_file = plans_dir / date / slug / "plan.md"
        if not plan_file.exists():
            raise ValueError(f"Plan not found: {plan_ref}")
        return date, slug, plan_file

    matches: list[tuple[str, str, Path]] = []
    for plan in scan_plans(plans_dir):
        if plan["slug"] == plan_ref:
            plan_file = Path(plan["path"])
            matches.append((plan["date"], plan["slug"], plan_file))

    if not matches:
        raise ValueError(f"Plan not found: {plan_ref}")

    if len(matches) > 1:
        candidates = ", ".join(f"{d}/{s}" for d, s, _ in matches)
        raise ValueError(f"Ambiguous slug '{plan_ref}'. Use one of: {candidates}")

    return matches[0]


def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:60]


def create_plan(
    plans_dir: Path,
    title: str,
    now: datetime | None = None,
) -> tuple[str, str, Path]:
    """Create new timestamped plan directory and starter plan.md."""
    ts = now or datetime.now()
    date = ts.strftime("%Y-%m-%d")
    hhmm = ts.strftime("%H-%M")
    slug_part = slugify(title)
    slug = f"{date}_{hhmm}_{slug_part}"

    plan_dir = plans_dir / date / slug
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "docs").mkdir(exist_ok=True)
    plan_file = plan_dir / "plan.md"

    if not plan_file.exists():
        plan_file.write_text(
            (
                f"# {title}\n\n"
                "## Goal / North Star\n\n"
                "- TODO\n\n"
                "## Non-Goals\n\n"
                "- TODO\n\n"
                "## Architecture / Visual Overview\n\n"
                "### System Overview\n\n"
                "```mermaid\n"
                "flowchart TD\n"
                "    A[TODO] --> B[TODO]\n"
                "```\n\n"
                "### Data / Request Flow\n\n"
                "```mermaid\n"
                "sequenceDiagram\n"
                "    participant User\n"
                "    participant System\n"
                "    User->>System: TODO\n"
                "    System-->>User: TODO\n"
                "```\n\n"
                "### Work Breakdown\n\n"
                "```mermaid\n"
                "flowchart LR\n"
                "    T1[Task 1] --> T2[Task 2]\n"
                "    T2 --> T3[Task 3]\n"
                "```\n\n"
                "## Work Items / Living Backlog\n\n"
                "- [ ] Define scope\n"
                "- [ ] Draft implementation plan\n"
            ),
            encoding="utf-8",
        )

    return date, slug, plan_file
