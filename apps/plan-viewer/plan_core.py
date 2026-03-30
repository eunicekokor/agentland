#!/usr/bin/env python3
"""Shared plan-viewer core logic for CLI and web server."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLANS_DIR = REPO_ROOT / "plans"
DEFAULT_MIRROR_DIR = Path.home() / "agent-plans" / "plans"
LINEAGE_STORE_PATH = REPO_ROOT / ".plan-viewer" / "lineage.json"

SOURCE_LOCAL = "local"
SOURCE_AGENT = "agent"
SOURCE_CURSOR = "cursor"
SOURCE_CLAUDE = "claude"
ALL_SOURCES = (SOURCE_LOCAL, SOURCE_AGENT, SOURCE_CURSOR, SOURCE_CLAUDE)

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
    """Resolve primary local plans directory with precedence: CLI > ENV > local default."""
    if cli_plans_dir:
        return Path(cli_plans_dir).expanduser()
    env_value = os.environ.get("PLAN_VIEWER_PLANS_DIR")
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_PLANS_DIR


def resolve_sources(cli_sources: str | None = None) -> list[str]:
    """Resolve enabled sources with precedence: CLI > ENV > all defaults."""
    raw = cli_sources or os.environ.get("PLAN_VIEWER_SOURCES")
    if not raw:
        return list(ALL_SOURCES)

    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        if p in ALL_SOURCES and p not in out:
            out.append(p)
    return out or list(ALL_SOURCES)


def source_roots(local_plans_dir: Path) -> dict[str, Path]:
    return {
        SOURCE_LOCAL: local_plans_dir,
        SOURCE_AGENT: Path.home() / "agent-plans" / "plans",
        SOURCE_CURSOR: Path.home() / ".cursor" / "plans",
        SOURCE_CLAUDE: Path.home() / ".claude" / "plans",
    }


def make_plan_id(source: str, path: Path) -> str:
    digest = hashlib.sha1(f"{source}:{path.resolve()}".encode("utf-8")).hexdigest()[:16]
    return f"pv_{source}_{digest}"


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


def resolve_lineage_store_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    env_value = os.environ.get("PLAN_VIEWER_LINEAGE_STORE")
    if env_value:
        return Path(env_value).expanduser()
    return LINEAGE_STORE_PATH


def sync_plan_dir(
    plans_dir: Path,
    date: str,
    slug: str,
    mirror_dir: Path | None = None,
) -> None:
    """Mirror one structured local plan directory to mirror root."""
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
    """Extract first markdown H1 title, fallback to filename/slug."""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def parse_task_stats(content: str) -> tuple[int, int]:
    """Parse markdown checkbox tasks from any section."""
    matches = _CHECKBOX_RE.findall(content)
    total = len(matches)
    done = sum(1 for v in matches if v.lower() == "x")
    return total, done


def derive_status(task_total: int, task_done: int) -> str:
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


def _read_plan_content(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _build_plan_record(
    source: str,
    plan_file: Path,
    title_fallback: str,
    date: str | None = None,
    slug: str | None = None,
    docs: list[str] | None = None,
) -> dict | None:
    content = _read_plan_content(plan_file)
    if content is None:
        return None

    title = extract_title(plan_file, content) or title_fallback
    task_total, task_done = parse_task_stats(content)
    status = derive_status(task_total, task_done)
    updated_iso = datetime.fromtimestamp(plan_file.stat().st_mtime, tz=timezone.utc).isoformat()

    record = {
        "plan_id": make_plan_id(source, plan_file),
        "source": source,
        "date": date or datetime.fromtimestamp(plan_file.stat().st_mtime).strftime("%Y-%m-%d"),
        "time": _plan_time(slug or ""),
        "slug": slug or plan_file.stem,
        "title": title,
        "path": str(plan_file),
        "docs": docs or [],
        "docs_count": len(docs or []),
        "updated": updated_iso,
        "task_total": task_total,
        "task_done": task_done,
        "status": status,
        "read_only": source in (SOURCE_AGENT, SOURCE_CURSOR, SOURCE_CLAUDE),
    }
    return record


def _scan_structured_source(root: Path, source: str) -> list[dict]:
    plans: list[dict] = []
    if not root.exists() or not root.is_dir():
        return plans

    for day_dir in sorted(root.iterdir(), reverse=True):
        if not day_dir.is_dir() or day_dir.name.startswith("."):
            continue

        for plan_dir in sorted(day_dir.iterdir(), reverse=True):
            if not plan_dir.is_dir():
                continue

            plan_file = plan_dir / "plan.md"
            if not plan_file.exists():
                continue

            docs_dir = plan_dir / "docs"
            docs: list[str] = []
            if docs_dir.is_dir():
                docs = [
                    f.name
                    for f in sorted(docs_dir.iterdir())
                    if f.is_file() and f.suffix.lower() in SAFE_EXTENSIONS
                ]

            rec = _build_plan_record(
                source=source,
                plan_file=plan_file,
                title_fallback=plan_dir.name,
                date=day_dir.name,
                slug=plan_dir.name,
                docs=docs,
            )
            if rec:
                plans.append(rec)

    return plans


def _scan_flat_source(root: Path, source: str) -> list[dict]:
    plans: list[dict] = []
    if not root.exists() or not root.is_dir():
        return plans

    for f in sorted(root.iterdir(), reverse=True):
        if not f.is_file() or f.suffix.lower() != ".md":
            continue
        if source == SOURCE_CURSOR and not f.name.endswith(".plan.md"):
            continue

        rec = _build_plan_record(
            source=source,
            plan_file=f,
            title_fallback=f.stem,
            date=None,
            slug=f.stem,
            docs=[],
        )
        if rec:
            plans.append(rec)

    return plans


def _normalize_lineage_store(store: dict) -> dict:
    raw = store if isinstance(store, dict) else {}
    plans = raw.get("plans") if isinstance(raw.get("plans"), dict) else {}

    normalized: dict[str, dict] = {}
    for plan_id, rel in plans.items():
        if not isinstance(rel, dict):
            rel = {}
        normalized[str(plan_id)] = {
            "merged_with": sorted(set(str(x) for x in rel.get("merged_with", []) if x)),
            "parent_of": sorted(set(str(x) for x in rel.get("parent_of", []) if x)),
            "child_of": sorted(set(str(x) for x in rel.get("child_of", []) if x)),
        }

    return {"plans": normalized}


def load_lineage_store(path: Path | None = None) -> dict:
    path = resolve_lineage_store_path(path)
    if not path.exists():
        return {"plans": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"plans": {}}
    return _normalize_lineage_store(raw)


def save_lineage_store(store: dict, path: Path | None = None) -> None:
    path = resolve_lineage_store_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    norm = _normalize_lineage_store(store)
    path.write_text(json.dumps(norm, indent=2, sort_keys=True), encoding="utf-8")


def _lineage_entry(store: dict, plan_id: str) -> dict:
    plans = store.setdefault("plans", {})
    entry = plans.setdefault(plan_id, {"merged_with": [], "parent_of": [], "child_of": []})
    entry.setdefault("merged_with", [])
    entry.setdefault("parent_of", [])
    entry.setdefault("child_of", [])
    return entry


def add_merge_relation(store: dict, left_plan_id: str, right_plan_id: str) -> None:
    if left_plan_id == right_plan_id:
        return
    l = _lineage_entry(store, left_plan_id)
    r = _lineage_entry(store, right_plan_id)
    if right_plan_id not in l["merged_with"]:
        l["merged_with"].append(right_plan_id)
    if left_plan_id not in r["merged_with"]:
        r["merged_with"].append(left_plan_id)


def add_parent_child_relation(store: dict, parent_plan_id: str, child_plan_id: str) -> None:
    if parent_plan_id == child_plan_id:
        return
    p = _lineage_entry(store, parent_plan_id)
    c = _lineage_entry(store, child_plan_id)
    if child_plan_id not in p["parent_of"]:
        p["parent_of"].append(child_plan_id)
    if parent_plan_id not in c["child_of"]:
        c["child_of"].append(parent_plan_id)


def lineage_related(store: dict, plan_id: str) -> dict:
    entry = store.get("plans", {}).get(plan_id, {})
    return {
        "merged_with": list(entry.get("merged_with", [])),
        "parent_of": list(entry.get("parent_of", [])),
        "child_of": list(entry.get("child_of", [])),
    }


def lineage_group_ids(store: dict, root_plan_id: str) -> list[str]:
    if root_plan_id not in store.get("plans", {}):
        return [root_plan_id]

    seen = {root_plan_id}
    q: deque[str] = deque([root_plan_id])
    while q:
        cur = q.popleft()
        rel = lineage_related(store, cur)
        neighbors = rel["merged_with"] + rel["parent_of"] + rel["child_of"]
        for nxt in neighbors:
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return sorted(seen)


def lineage_rollup(group_ids: list[str], plans_by_id: dict[str, dict]) -> dict:
    done = 0
    total = 0
    latest: datetime | None = None
    members = 0
    for pid in group_ids:
        plan = plans_by_id.get(pid)
        if not plan:
            continue
        members += 1
        done += int(plan.get("task_done") or 0)
        total += int(plan.get("task_total") or 0)
        try:
            ts = datetime.fromisoformat(str(plan.get("updated", "")))
            latest = ts if latest is None or ts > latest else latest
        except ValueError:
            pass

    return {
        "member_count": members,
        "task_done": done,
        "task_total": total,
        "updated": latest.isoformat() if latest else "",
    }


def scan_plans(
    plans_dir: Path,
    sources: list[str] | None = None,
    include_lineage: bool = True,
) -> list[dict]:
    """Scan plans across enabled sources."""
    enabled = sources or resolve_sources(None)
    roots = source_roots(plans_dir)

    plans: list[dict] = []
    for source in enabled:
        root = roots.get(source)
        if root is None:
            continue
        if source in (SOURCE_LOCAL, SOURCE_AGENT):
            plans.extend(_scan_structured_source(root, source))
        elif source in (SOURCE_CURSOR, SOURCE_CLAUDE):
            plans.extend(_scan_flat_source(root, source))

    plans.sort(key=lambda p: str(p.get("updated", "")), reverse=True)

    if include_lineage:
        store = load_lineage_store()
        plans_by_id = {p["plan_id"]: p for p in plans}
        for p in plans:
            rel = lineage_related(store, p["plan_id"])
            p["lineage"] = {
                "merged_with": rel["merged_with"],
                "parent_of": rel["parent_of"],
                "child_of": rel["child_of"],
                "group_ids": lineage_group_ids(store, p["plan_id"]),
            }
            p["lineage_rollup"] = lineage_rollup(p["lineage"]["group_ids"], plans_by_id)

    return plans


def get_plan_by_id(plans_dir: Path, plan_id: str, sources: list[str] | None = None) -> dict | None:
    for p in scan_plans(plans_dir, sources=sources, include_lineage=True):
        if p["plan_id"] == plan_id:
            return p
    return None


def read_plan_by_id(plans_dir: Path, plan_id: str, sources: list[str] | None = None) -> str | None:
    p = get_plan_by_id(plans_dir, plan_id, sources=sources)
    if not p:
        return None
    return _read_plan_content(Path(p["path"]))


def _extract_parent_summary(content: str, max_lines: int = 20) -> str:
    lines = [ln.rstrip() for ln in content.splitlines()]
    trimmed = [ln for ln in lines if ln.strip()]
    if not trimmed:
        return ""
    if trimmed and trimmed[0].startswith("# "):
        trimmed = trimmed[1:]
    return "\n".join(trimmed[:max_lines])


def import_plan_to_local(plans_dir: Path, plan_id: str, sources: list[str] | None = None) -> dict:
    src = get_plan_by_id(plans_dir, plan_id, sources=sources)
    if not src:
        raise ValueError(f"Plan not found: {plan_id}")

    src_path = Path(src["path"])
    content = _read_plan_content(src_path)
    if content is None:
        raise ValueError("Unable to read source plan")

    title = src.get("title") or src_path.stem
    date, slug, new_file = create_plan(plans_dir, f"Imported - {title}")

    imported = (
        content.rstrip()
        + "\n\n## Imported From\n\n"
        + f"- source: {src.get('source')}\n"
        + f"- original_plan_id: {src.get('plan_id')}\n"
        + f"- original_path: {src_path}\n"
    )
    new_file.write_text(imported + "\n", encoding="utf-8")
    sync_plan_dir(plans_dir, date, slug)

    local_plan = get_plan_by_id(plans_dir, make_plan_id(SOURCE_LOCAL, new_file), sources=[SOURCE_LOCAL])
    if local_plan:
        return local_plan
    # fallback if scan race/quirk
    return {
        "plan_id": make_plan_id(SOURCE_LOCAL, new_file),
        "source": SOURCE_LOCAL,
        "date": date,
        "slug": slug,
        "path": str(new_file),
        "title": f"Imported - {title}",
    }


def write_plan_by_id(
    plans_dir: Path,
    plan_id: str,
    content: str,
    sources: list[str] | None = None,
) -> dict:
    plan = get_plan_by_id(plans_dir, plan_id, sources=sources)
    if not plan:
        return {"ok": False, "error": "Plan not found"}

    imported = False
    if plan.get("source") != SOURCE_LOCAL:
        plan = import_plan_to_local(plans_dir, plan_id, sources=sources)
        imported = True

    try:
        path = Path(plan["path"])
        path.write_text(content, encoding="utf-8")
        if plan.get("date") and plan.get("slug"):
            sync_plan_dir(plans_dir, str(plan["date"]), str(plan["slug"]))
        return {
            "ok": True,
            "plan_id": plan.get("plan_id"),
            "source": plan.get("source"),
            "imported": imported,
        }
    except OSError as e:
        return {"ok": False, "error": str(e)}


def spinout_plan(
    plans_dir: Path,
    parent_plan_id: str,
    title: str,
    sources: list[str] | None = None,
) -> dict:
    parent = get_plan_by_id(plans_dir, parent_plan_id, sources=sources)
    if not parent:
        raise ValueError(f"Plan not found: {parent_plan_id}")

    parent_content = read_plan_by_id(plans_dir, parent_plan_id, sources=sources) or ""
    summary = _extract_parent_summary(parent_content)

    date, slug, child_file = create_plan(plans_dir, title)
    child_content = child_file.read_text(encoding="utf-8")
    child_content += (
        "\n\n## Parent Plan\n\n"
        + f"- parent_plan_id: {parent.get('plan_id')}\n"
        + f"- parent_title: {parent.get('title')}\n"
        + f"- parent_source: {parent.get('source')}\n"
    )
    if summary:
        child_content += "\n## Parent Context Summary\n\n" + summary + "\n"

    child_file.write_text(child_content, encoding="utf-8")
    sync_plan_dir(plans_dir, date, slug)

    child_id = make_plan_id(SOURCE_LOCAL, child_file)
    store = load_lineage_store()
    add_parent_child_relation(store, parent_plan_id, child_id)
    save_lineage_store(store)

    child = get_plan_by_id(plans_dir, child_id, sources=[SOURCE_LOCAL])
    if not child:
        child = {
            "plan_id": child_id,
            "source": SOURCE_LOCAL,
            "date": date,
            "slug": slug,
            "path": str(child_file),
            "title": title,
        }
    return child


def merge_plans(plans_dir: Path, left_plan_id: str, right_plan_id: str, sources: list[str] | None = None) -> dict:
    left = get_plan_by_id(plans_dir, left_plan_id, sources=sources)
    right = get_plan_by_id(plans_dir, right_plan_id, sources=sources)
    if not left or not right:
        raise ValueError("One or both plan_ids not found")

    store = load_lineage_store()
    add_merge_relation(store, left_plan_id, right_plan_id)
    save_lineage_store(store)

    return {
        "ok": True,
        "left_plan_id": left_plan_id,
        "right_plan_id": right_plan_id,
    }


def lineage_for_plan(plans_dir: Path, plan_id: str, sources: list[str] | None = None) -> dict:
    store = load_lineage_store()
    plans = scan_plans(plans_dir, sources=sources, include_lineage=False)
    plans_by_id = {p["plan_id"]: p for p in plans}

    rel = lineage_related(store, plan_id)
    group_ids = lineage_group_ids(store, plan_id)
    group_plans = [plans_by_id[g] for g in group_ids if g in plans_by_id]

    return {
        "plan_id": plan_id,
        "related": rel,
        "group_ids": group_ids,
        "group_rollup": lineage_rollup(group_ids, plans_by_id),
        "group_plans": group_plans,
    }


# ---------------------------------------------------------------------------
# Legacy compatibility helpers (date/slug routes and old CLI refs)
# ---------------------------------------------------------------------------

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
    if ".." in filename or "/" in filename or "\\" in filename:
        return None

    doc_path = plans_dir / date / slug / "docs" / filename
    if not doc_path.is_file() or doc_path.suffix.lower() not in SAFE_EXTENSIONS:
        return None

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return doc_path.read_bytes(), mime_type


def list_plan_files(plans_dir: Path, date: str, slug: str) -> list[dict]:
    plan_dir = plans_dir / date / slug
    if not plan_dir.is_dir():
        return []

    files: list[dict] = []
    for f in sorted(plan_dir.iterdir()):
        if f.is_file() and f.suffix.lower() == ".excalidraw":
            files.append({"name": f.name, "size": f.stat().st_size})
    return files


def serve_plan_file(plans_dir: Path, date: str, slug: str, filename: str) -> tuple[bytes, str] | None:
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    if not filename.lower().endswith(".excalidraw"):
        return None

    file_path = plans_dir / date / slug / filename
    if not file_path.is_file():
        return None

    return file_path.read_bytes(), "application/json"


def list_docs_by_plan_id(plans_dir: Path, plan_id: str, sources: list[str] | None = None) -> list[dict]:
    p = get_plan_by_id(plans_dir, plan_id, sources=sources)
    if not p:
        return []
    if p.get("source") not in (SOURCE_LOCAL, SOURCE_AGENT):
        return []
    return list_docs(plans_dir if p.get("source") == SOURCE_LOCAL else source_roots(plans_dir)[SOURCE_AGENT], p["date"], p["slug"])


def list_plan_files_by_plan_id(plans_dir: Path, plan_id: str, sources: list[str] | None = None) -> list[dict]:
    p = get_plan_by_id(plans_dir, plan_id, sources=sources)
    if not p:
        return []
    if p.get("source") not in (SOURCE_LOCAL, SOURCE_AGENT):
        return []
    root = plans_dir if p.get("source") == SOURCE_LOCAL else source_roots(plans_dir)[SOURCE_AGENT]
    return list_plan_files(root, p["date"], p["slug"])


def serve_doc_file_by_plan_id(
    plans_dir: Path,
    plan_id: str,
    filename: str,
    sources: list[str] | None = None,
) -> tuple[bytes, str] | None:
    p = get_plan_by_id(plans_dir, plan_id, sources=sources)
    if not p or p.get("source") not in (SOURCE_LOCAL, SOURCE_AGENT):
        return None
    root = plans_dir if p.get("source") == SOURCE_LOCAL else source_roots(plans_dir)[SOURCE_AGENT]
    return serve_doc_file(root, p["date"], p["slug"], filename)


def serve_plan_file_by_plan_id(
    plans_dir: Path,
    plan_id: str,
    filename: str,
    sources: list[str] | None = None,
) -> tuple[bytes, str] | None:
    p = get_plan_by_id(plans_dir, plan_id, sources=sources)
    if not p or p.get("source") not in (SOURCE_LOCAL, SOURCE_AGENT):
        return None
    root = plans_dir if p.get("source") == SOURCE_LOCAL else source_roots(plans_dir)[SOURCE_AGENT]
    return serve_plan_file(root, p["date"], p["slug"], filename)


def open_with_agent(path: str, agent: str) -> dict:
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


def resolve_plan_ref(
    plans_dir: Path,
    plan_ref: str,
    sources: list[str] | None = None,
) -> tuple[str, str, Path]:
    """Legacy resolver returning (date, slug, path) for local structured plans.

    Supports plan_id and legacy date/slug and slug lookups.
    """
    if plan_ref.startswith("pv_"):
        p = get_plan_by_id(plans_dir, plan_ref, sources=sources)
        if not p:
            raise ValueError(f"Plan not found: {plan_ref}")
        if p.get("source") not in (SOURCE_LOCAL, SOURCE_AGENT):
            raise ValueError(f"Plan {plan_ref} is not structured; use plan_id endpoints")
        return str(p["date"]), str(p["slug"]), Path(p["path"])

    if "/" in plan_ref:
        date, slug = plan_ref.split("/", 1)
        plan_file = plans_dir / date / slug / "plan.md"
        if not plan_file.exists():
            raise ValueError(f"Plan not found: {plan_ref}")
        return date, slug, plan_file

    matches: list[tuple[str, str, Path]] = []
    for plan in scan_plans(plans_dir, sources=sources, include_lineage=False):
        if plan.get("slug") == plan_ref and plan.get("source") in (SOURCE_LOCAL, SOURCE_AGENT):
            matches.append((str(plan["date"]), str(plan["slug"]), Path(str(plan["path"]))))

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
