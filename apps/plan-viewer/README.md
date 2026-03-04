# Agent Plans Viewer

Local web UI for browsing, reading, and launching plans stored in `~/agent-plans/`.

## Quick Start

```bash
python3 ~/agent-plans/apps/plan-viewer/server.py
```

Opens `http://localhost:8787` in your default browser. No dependencies — uses Python stdlib only.

To use a different port:

```bash
python3 ~/agent-plans/apps/plan-viewer/server.py 9000
```

## What It Does

- **Browse plans** in a sidebar grouped by date, most recent first
- **Read plans** with full markdown rendering (headings, tables, checkboxes, code blocks)
- **Render mermaid diagrams** inline (flowcharts, sequence diagrams, gantt charts, etc.)
- **View attached docs** from each plan's `docs/` folder (images, SVGs, PDFs)
- **Open in an agent** with one click:
  - **Cursor** — opens the plan file via `cursor` CLI
  - **Claude** — copies plan to clipboard + opens claude.ai
  - **Codex** — copies plan to clipboard + opens ChatGPT

## Directory Structure

The viewer expects plans organized like this:

```
~/agent-plans/
├── apps/plan-viewer/     # this viewer (server + UI)
│   ├── server.py
│   ├── index.html
│   └── README.md
├── plans/                # plan data (gitignored)
│   └── 2026-03-04/
│       └── 2026-03-04_14-30_my-feature/
│           ├── plan.md   # the plan (with mermaid diagrams)
│           └── docs/     # optional attachments (SVGs, screenshots, etc.)
└── ...
```

Plans are created automatically by the plan-management Cursor rule when you ask an agent to create a plan.

## Requirements

- Python 3.10+
- macOS (uses `pbcopy` for clipboard, `open` for browser)
- Internet connection (loads marked.js and mermaid.js from CDN)

## API

All endpoints are local-only (`127.0.0.1`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Serves the UI |
| `GET` | `/api/plans` | Lists all plans with metadata |
| `GET` | `/api/plans/:date/:slug` | Returns a plan's markdown content |
| `GET` | `/api/plans/:date/:slug/docs` | Lists files in a plan's docs folder |
| `GET` | `/api/plans/:date/:slug/docs/:file` | Serves a doc file (images, SVGs, etc.) |
| `POST` | `/api/open` | Opens a plan in Cursor, Claude, or Codex |
