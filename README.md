# agentland

Living in agentland can be fun.

A collection of small, zero-dependency tools I build with AI agents to improve my productivity and organization. Everything here is designed to work across agents (Cursor, Claude, Codex) and stay out of the way.

## Apps

| App | Description | Status |
|-----|-------------|--------|
| [Plan Viewer](.viewer/) | Local web UI for browsing, reading, and launching implementation plans. Renders mermaid diagrams, serves attached docs, and opens plans directly in Cursor/Claude/Codex. | Active |

## Setup

```bash
git clone https://github.com/eunicekokor/agentland.git ~/agent-plans
```

This clones the repo directly into `~/agent-plans/`, which is the directory all tools expect. Plan data (day folders like `2026-03-04/`) is gitignored — only the tooling is tracked.

### Plan Viewer

```bash
python3 ~/agent-plans/.viewer/server.py
```

Opens a local web UI at `http://localhost:8787`. Zero dependencies — Python 3.10+ only.

See [.viewer/README.md](.viewer/README.md) for full docs and API reference.

### Plan Management (Cursor rule)

To have agents automatically create and update plans in `~/agent-plans/`, copy the Cursor rule into any workspace:

```bash
mkdir -p <your-workspace>/.cursor/rules
cp plan-management.mdc <your-workspace>/.cursor/rules/
```

This rule instructs agents to persist plans with mandatory mermaid diagrams (architecture, data flow, work breakdown) whenever you ask them to create a plan or tech spec. Plans are organized by day and timestamped.

## How It Works

```
~/agent-plans/                    # this repo
├── .viewer/                      # Plan Viewer app
│   ├── server.py                 # Python stdlib HTTP server
│   ├── index.html                # SPA with mermaid rendering
│   └── README.md
├── plan-management.mdc           # Cursor rule for auto-creating plans
├── 2026-03-04/                   # (gitignored) day folder
│   └── 2026-03-04_14-30_slug/
│       ├── plan.md               # plan with embedded mermaid diagrams
│       └── docs/                 # supplementary visuals
└── ...
```

## Requirements

- Python 3.10+
- Internet connection (loads [marked.js](https://github.com/markedjs/marked) and [mermaid.js](https://github.com/mermaid-js/mermaid) from CDN)

### Platform-specific agent launching

| Platform | Cursor | Claude / Codex |
|----------|--------|----------------|
| macOS | `cursor` CLI | `pbcopy` + `open` URL |
| Linux | `cursor` CLI | `xclip` + `xdg-open` URL (not yet implemented) |
| Windows | `cursor` CLI | `clip` + `start` URL (not yet implemented) |

Currently macOS only for clipboard + browser. PRs welcome for Linux/Windows support.
