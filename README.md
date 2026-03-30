# agentland

Living in agentland can be fun.

A collection of small, zero-dependency tools I build with AI agents to improve my productivity and organization. Everything here is designed to work across agents (Cursor, Claude, Codex) and stay out of the way.

## Apps

| App | Description | Stack | Status |
|-----|-------------|-------|--------|
| [Plan Viewer](apps/plan-viewer/) | Browse, read, and launch implementation plans. Renders mermaid diagrams inline, serves attached docs, opens plans in Cursor/Claude/Codex. | Python stdlib | Active |

## Rules

Cursor rules and agent prompts that can be copied into any workspace.

| Rule | Description |
|------|-------------|
| [plan-management](rules/plan-management.mdc) | Auto-persist plans to `~/agent-plans/plans/` with mandatory mermaid diagrams (architecture, data flow, work breakdown). |

## Setup

### 1. Clone

```bash
git clone https://github.com/eunicekokor/agentland.git ~/agent-plans
```

This clones the repo into `~/agent-plans/`. All tools expect this path. Plan data lives in `plans/` (gitignored) — only tooling is tracked.

### 2. Run the Plan Viewer

```bash
python3 apps/plan-viewer/server.py
```

Opens `http://localhost:8787`. Python 3.10+ required, zero pip dependencies.

### 3. Install a rule into a workspace

```bash
mkdir -p <your-workspace>/.cursor/rules
cp ~/agent-plans/rules/plan-management.mdc <your-workspace>/.cursor/rules/
```

## Adding new apps or rules

**New app:** create a folder under `apps/` and add a row to the Apps table above.

```
apps/
├── plan-viewer/
└── your-new-app/
    ├── ...
    └── README.md
```

**New rule:** drop a `.mdc` file into `rules/` and add a row to the Rules table above.

```
rules/
├── plan-management.mdc
└── your-new-rule.mdc
```

## Repo Structure

```
~/agent-plans/
├── apps/                         # standalone tools
│   └── plan-viewer/
│       ├── server.py             # Python stdlib HTTP server
│       ├── index.html            # SPA with mermaid rendering
│       └── README.md
├── rules/                        # Cursor rules / agent prompts
│   └── plan-management.mdc
├── plans/                        # (gitignored) plan data
│   └── 2026-03-04/
│       └── 2026-03-04_14-30_slug/
│           ├── plan.md
│           └── docs/
├── README.md
└── LICENSE
```

## Requirements

- Python 3.10+
- Internet connection (loads [marked.js](https://github.com/markedjs/marked) and [mermaid.js](https://github.com/mermaid-js/mermaid) from CDN)

### Platform support for agent launching

| Platform | Cursor | Claude / Codex |
|----------|--------|----------------|
| macOS | `cursor` CLI | `pbcopy` + `open` URL |
| Linux | `cursor` CLI | `xclip` + `xdg-open` (not yet) |
| Windows | `cursor` CLI | `clip` + `start` (not yet) |
