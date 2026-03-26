# Agent Plans Viewer

Local CLI + web viewer for browsing, reading, editing, and launching plans.

## Quick Start

### Web (backward compatible)

```bash
python3 ~/agent-plans/apps/plan-viewer/server.py
```

or with the new CLI:

```bash
python3 ~/agent-plans/apps/plan-viewer/plan_viewer.py serve
```

### CLI

```bash
python3 ~/agent-plans/apps/plan-viewer/plan_viewer.py list
python3 ~/agent-plans/apps/plan-viewer/plan_viewer.py dashboard
python3 ~/agent-plans/apps/plan-viewer/plan_viewer.py show 2026-03-04/2026-03-04_12-57_plan-viewer-ui
```

## CLI Commands

- `serve [--port] [--plans-dir] [--open-browser|--no-open-browser]`
- `list [--plans-dir] [--date YYYY-MM-DD] [--limit N] [--json]`
- `show <plan_ref> [--plans-dir]`
- `dashboard [--plans-dir] [--date YYYY-MM-DD] [--limit N] [--watch SECONDS]`
- `open <plan_ref> --agent {cursor,claude,codex} [--plans-dir]`
- `edit <plan_ref> [--plans-dir] [--editor CMD]`

`plan_ref` supports:
- `YYYY-MM-DD/slug`
- `slug` (must be unique across all days)

## Plan Directory Resolution

Precedence:
1. `--plans-dir`
2. `PLAN_VIEWER_PLANS_DIR`
3. `~/agent-plans/plans`

## What It Does

- **Browse plans** in web UI (grouped by date)
- **Read plans** in web UI and CLI
- **Edit plans** in web UI and terminal editor
- **Render mermaid diagrams** in browser
- **View attached docs** from each plan's `docs/` folder
- **Open plans in agents**:
  - **Cursor** via `cursor` CLI
  - **Claude** via `pbcopy` + browser
  - **Codex** via `pbcopy` + browser

## API

All endpoints are local-only (`127.0.0.1`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serves the UI |
| `GET` | `/api/plans` | Lists plans with metadata (`status`, `task_total`, `task_done` included) |
| `GET` | `/api/plans/:date/:slug` | Returns plan markdown |
| `PUT` | `/api/plans/:date/:slug` | Saves plan markdown |
| `GET` | `/api/plans/:date/:slug/docs` | Lists docs files |
| `GET` | `/api/plans/:date/:slug/docs/:file` | Serves a docs file |
| `GET` | `/api/plans/:date/:slug/files` | Lists `.excalidraw` files |
| `GET` | `/api/plans/:date/:slug/files/:file` | Serves `.excalidraw` file (CORS enabled) |
| `POST` | `/api/open` | Opens plan in Cursor/Claude/Codex |

## Requirements

- Python 3.10+
- macOS for agent launch/copy flows (`pbcopy`, `open`)
- Internet connection (browser loads marked.js + mermaid.js from CDN)
