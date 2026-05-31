---
name: agent-conversation-logger
description: Install, repair, verify, or explain agent conversation/session logging for Codex and Claude Code workflows, including Obsidian live logs, lifecycle hooks, JSONL exports, prompt collection, 대화 수집기, 프롬프트 수집, 세션 로그, or checking whether a conversation was captured.
---

# Agent Conversation Logger

## Purpose

Capture Codex and Claude Code conversations directly into an Obsidian vault as live, append-only Markdown transcripts, plus lightweight event streams for tooling. Hooks run on every prompt/tool/stop event, so the vault is always current without manual export.

## Layout (vault-first)

Both agents write to a **single shared output root** — by default the auto-detected Obsidian vault — so transcripts land natively inside the vault and Obsidian indexes them with no symlink layer.

```
<vault>/agent-logs/
├── codex-logs/<session_id>/transcript.md   ← Codex live transcript (one dir per session)
├── claude-logs/<session_id>/transcript.md  ← Claude Code live transcript
├── data/
│   ├── codex_live_events.jsonl             ← Codex lifecycle event stream
│   ├── claude_live_events.jsonl            ← Claude Code lifecycle event stream
│   ├── codex_sessions.jsonl                ← Codex analysis table (backfill)
│   ├── codex_turns.jsonl                   ← Codex analysis table (backfill)
│   └── codex_tool_calls.jsonl              ← Codex analysis table (backfill)
└── state/
    ├── live_append_state.json              ← Codex append offsets
    └── claude_live_append_state.json       ← Claude Code append offsets
```

Each `<session_id>/` directory holds the live `transcript.md` and can carry additional per-session artifacts later (summaries, decision logs, attachments).

## Output Root Resolution

Resolved in this order:

1. `--output-root` / `--claude-output-root` CLI flags
2. `AGENT_LOGS_OUTPUT_ROOT` env var
3. `OBSIDIAN_VAULT` / `OBSIDIAN_VAULT_PATH` env var → `<vault>/agent-logs`
4. Auto-detect macOS iCloud Obsidian container; prefer `DesignC/개발/agent-logs`, fall back to the only vault if there's exactly one
5. Last-resort fallback: `~/.local/share/agent-conversation-logger/agent-logs`

Both Codex and Claude Code use the same resolution — when a vault is detected they share `<vault>/agent-logs/`, and `codex-logs/` / `claude-logs/` keep them separated inside.

### Size cap keeps the vault Obsidian-safe

A single multi-MB markdown note **freezes Obsidian** (it reopens the last-active file on launch).
Codex transcripts can grow to 12MB+, which is what caused freezes. Rather than splitting Codex
out of the vault, **both engines cap transcript size and rotate**:

- When `transcript.md` crosses **`AGENT_LOGS_MAX_MD_BYTES`** (default **1 MB**), it rolls to
  `transcript.001.md`, `transcript.002.md`, … and a fresh `transcript.md` continues. No single
  file gets large; nothing is lost (rotation, not truncation).
- So **both Codex and Claude logs live in the vault** (`<vault>/agent-logs/{codex,claude}-logs/`),
  browsable in Obsidian, and Obsidian never has to open a huge file.
- Every rotated part keeps the frontmatter header (incl. the `*-live-log` tag), and the viewer
  recognizes `transcript.md` and `transcript.NNN.md` — so any part opens cleanly.

Lower the cap (e.g. `AGENT_LOGS_MAX_MD_BYTES=500000`) for even snappier Obsidian; raise it for
fewer files. A pre-existing giant transcript should be split into ≤cap parts before it sits in the
vault (the loggers only cap going forward).

## Other Paths

| Component | Path |
|---|---|
| Codex runtime exporter | `${CODEX_HOME:-$HOME/.codex}/codex-session-exporter/exporter.py` |
| Codex hook config | `${CODEX_HOME:-$HOME/.codex}/hooks.json` |
| Codex hook diagnostics | `${CODEX_HOME:-$HOME/.codex}/codex-session-exporter/hook.log.jsonl` |
| Codex hook events | `UserPromptSubmit`, `PostToolUse`, `Stop` |
| Claude Code runtime logger | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-conversation-logger/claude_logger.py` |
| Claude Code hook config | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json` |
| Claude Code hook diagnostics | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-conversation-logger/hook.log.jsonl` |
| Claude Code hook events | `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop` |

## Install Or Repair

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/install.py"
```

The installer:

- Copies exporter scripts into their runtime homes
- Registers lifecycle hooks in `hooks.json` (Codex) and `settings.json` (Claude Code)
- Creates `<vault>/agent-logs/{codex-logs,claude-logs,data,state}` skeleton when the resolved root is a vault path
- Removes the legacy Codex LaunchAgent polling job

Useful flags:

| Flag | Purpose |
|---|---|
| `--output-root <path>` | Override Codex output root (e.g. point to a different vault) |
| `--claude-output-root <path>` | Override Claude Code output root |
| `--no-claude` | Install Codex only |
| `--obsidian-link <path>` | Create a separate Obsidian symlink (only needed when output_root is *outside* a vault) |
| `--claude-obsidian-link <path>` | Same for Claude Code |
| `--claude-config-dir <path>` | Non-default Claude Code config root (e.g. `~/.claude-work`) |

## Verify

```bash
codex features list | rg hooks
VAULT_LOGS="${AGENT_LOGS_OUTPUT_ROOT:-$OBSIDIAN_VAULT/agent-logs}"
ls "$VAULT_LOGS/codex-logs" | wc -l
ls "$VAULT_LOGS/claude-logs" | wc -l
tail -n 5 "${CODEX_HOME:-$HOME/.codex}/codex-session-exporter/hook.log.jsonl"
tail -n 5 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-conversation-logger/hook.log.jsonl"
```

For a specific keyword across all transcripts and event streams:

```bash
rg -n "<keyword>" "$VAULT_LOGS/codex-logs" "$VAULT_LOGS/claude-logs" "$VAULT_LOGS/data"
```

Report the session ID, transcript path, matching event count, and whether diagnostics include the expected hook events.

## Backfill For Analysis

Live transcripts are written immediately. The Codex normalized tables (`codex_sessions.jsonl`, `codex_turns.jsonl`, `codex_tool_calls.jsonl`) are refreshed on demand:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/export.py" \
  --codex-home "${CODEX_HOME:-$HOME/.codex}" \
  --output-root "${AGENT_LOGS_OUTPUT_ROOT:-$OBSIDIAN_VAULT/agent-logs}" \
  --include-active
```

Use `--force` only for intentional rebuilds. Don't pass `--include-developer-prompts` unless the user explicitly wants developer/system prompt bodies in the export; the default keeps only presence, hash, and character count.

## Explain Clearly

- **Live log** — append-only transcript at `<vault>/agent-logs/{codex,claude}-logs/<session>/transcript.md`, written on every lifecycle hook. Always current; review directly in Obsidian.
- **Live events JSONL** — `codex_live_events.jsonl` / `claude_live_events.jsonl`, one JSON object per hook event for lightweight parsing.
- **Normalized JSONL** — Codex analysis tables generated by `export.py` backfill (`sessions`, `turns`, `tool_calls`). Refresh only when needed.

If live transcripts exist but normalized tables do not, the conversation **was captured**; only the analysis table refresh is pending.

## Common Transcript Schema

Both Codex and Claude Code transcripts share the **same Markdown structure with identical frontmatter keys (in the same order)** so a single renderer can read either:

```
---
agent: "codex" | "claude-code"
session_id: "<uuid>"
started_at: "<iso8601>"
cwd: "<path>"
source_path: "<raw jsonl path>"
tags:
  - "<agent>-live-log"
---

# Live Log - <session_id>

> Append-only refined log. Existing sections are not rewritten.

## <iso8601 ts> - <KIND>[ `<identifier>`]

[- <key>: `<value>`]   ← 0+ metadata bullets (call_id, exit_code, is_error, …)
[<blank line>]
[```<lang>             ← 0/1 fenced code block
<body>
```]
```

- **KIND** ∈ `USER`, `ASSISTANT`, `SYSTEM`, `THINKING`, `TOOL CALL`, `TOOL OUTPUT`, `USAGE`
- **identifier** —
  - `TOOL CALL`: tool name (e.g. `Bash`, `exec_command`)
  - `TOOL OUTPUT`: prefer `tool_name (call_id)` (e.g. `Bash (toolu_01SzZ4...)`); fall back to bare `call_id` when the mapping is unknown
- **metadata bullets** — `- call_id: \`...\``, `- tool_name: \`...\`` (TOOL OUTPUT when mapped), `- exit_code: \`...\``, `- is_error: \`true\`` (use what applies)
- `USAGE` sections carry **per-turn token deltas** as bullets — `- in:`, `- out:`, `- cache_read:`, `- cache_write:` (Claude), `- reasoning:` (Codex), `- total:`. Claude emits one per assistant message; Codex collapses its many cumulative `token_count` events into one per-batch delta. Either way, **summing all USAGE sections gives the session total**, so the viewer's Insights tab aggregates them (totals + cache-hit ratio).
- `THINKING` sections carry the model's internal reasoning when present; signature-only thinking parts are dropped, never inlined as raw JSON.
- **tool_name mapping** — Both loggers persist a `call_names: {call_id: name}` map in their state files. TOOL CALL events register the mapping; later TOOL OUTPUT events look it up so transcripts show a human-readable name instead of a bare UUID. Session-level identifiers without a natural-language counterpart (`session_id`) stay as plain UUIDs.

Both loggers go through `build_frontmatter` / `build_live_header` which produce byte-identical layouts — only the values differ.

---

Original (legacy) layout reference, kept for parsers that read older files:

```
---
<YAML frontmatter — session_id, agent, started_at, source_path, cwd?, tags[]>
---

# <Title>

> <optional blockquote>

## <ISO8601 timestamp> - <KIND>[ `<identifier>`]

[- <key>: <value>]     ← 0+ metadata bullets
[<blank line>]
[```<lang>              ← 0/1 fenced code block
<body>
```]
```

- **KIND** ∈ `USER`, `ASSISTANT`, `SYSTEM`, `TOOL CALL`, `TOOL OUTPUT`
- **identifier** — `TOOL CALL`/`TOOL OUTPUT` only. Tool name for `TOOL CALL`, `tool_use_id` (Claude Code) / `call_id` (Codex) for `TOOL OUTPUT`.
- **metadata bullets** — `- tool_use_id: \`...\``, `- call_id: \`...\``, `- exit_code: \`...\``, `- is_error: \`true\``

Claude Code unwraps tool_use / tool_result parts buried inside assistant/user messages into their own sections — so tool invocations are first-class events, not text inside a message bubble.

## Render To HTML

Convert a `transcript.md` into a self-contained, messenger-style HTML viewer (CSS + JS inlined, no server needed):

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_html.py" <vault>/agent-logs/claude-logs/<session_id>/transcript.md
# → writes transcript.html next to the .md
```

Render every transcript under a directory:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_html.py" <vault>/agent-logs --recursive
```

The HTML viewer:

- USER messages right-aligned (iMessage blue), ASSISTANT left-aligned (light bubble)
- TOOL CALL / TOOL OUTPUT as collapsible cards with metadata (`tool_use_id`, `call_id`, `exit_code`, `is_error`) and the pretty-printed input/output body
- Toolbar: client-side filter, "Expand all tools" toggle, "Copy link"
- Day dividers, frontmatter card with stats, dark mode follows `prefers-color-scheme`

Open the resulting HTML in any browser (`open <path>` on macOS).

## Interactive Viewer (viewer.html)

`render_html.py` writes one HTML file per transcript. For ad-hoc browsing without
running anything, use the standalone client-side viewer at the repo root:

```bash
open "${CLAUDE_SKILL_DIR}/viewer.html"
```

Then **drag-and-drop a `transcript.md`** onto the page, or click **파일 선택 / Choose file**.
The viewer parses the common transcript schema and renders the same iMessage-style UI
entirely in the browser (no Python, no server, nothing written to disk — the dropped
file is read locally via `FileReader`). Use **← 다른 파일 열기** to swap transcripts.

**File detection** recognizes exactly **two filenames**: `transcript.md` → transcript
(chat/insights) mode, and `*.eval.md` → document mode (formatted markdown). A
renamed/exported transcript also resolves to transcript mode via its `*-live-log`
frontmatter tag (safety net). **Any other filename opens a "어떻게 열까요?" popup** that
lets the user pick chat or document (hinting the detected event count). Content is not used
to auto-classify, so a note that merely contains an example `## <ts> - USER` line is never
silently misread as a chat — it just prompts.

Two top tabs on the same dropped file: **💬 대화** (the chat view) and **📊 인사이트**
(per-session deterministic analytics — duration, turn/tool/error counts, tool-usage bar
chart, error list with exit codes, and a time-ordered event-flow strip). Insights are
computed purely in JS — no model or network — so the viewer stays fully static and works
the same when hosted (e.g. Netlify).

It reuses `render_html.py`'s parser and CSS ported to JavaScript, so both stay in sync
with the schema. Pick by use case:

| Need | Use |
|---|---|
| Browse a transcript right now | `viewer.html` (open once, drop files) |
| Pre-render / share a fixed `.html`, batch a folder | `render_html.py` |

A demo transcript covering every KIND lives at `examples/sample-transcript.md` — drop it
into `viewer.html` to see the layout.

### Web app (`web/`) — multi-file, virtualized

For larger / multi-file viewing there is a React (Vite + TS) static app in `web/`:

- **Drop many files at once** (or a folder). Files are grouped by `session_id`; a session's
  rotation parts (`transcript.001.md` … `transcript.md`) are auto-ordered and concatenated.
- **Left sidebar** lists sessions (and `*.eval.md` docs); expanding a session shows its parts —
  click to **jump** to that part. 💬 대화 / 📊 인사이트 tabs per session.
- **Virtualized stream** (react-virtuoso): only visible rows render, so a 16k-event session or
  many concatenated parts scroll smoothly — no freeze, even for the largest sessions.
- 100% client-side (FileReader, no upload) and fully static → deployable to Netlify with no
  backend. Build: `cd web && npm install && npm run build` (→ `web/dist/`); dev: `npm run dev`.

`viewer.html` stays as the zero-install, single-file fallback (double-click, no build).

## Repository Layout

Two-layer model: this repo is the **source of truth**; `install.py` deploys copies to the
runtime homes. Never edit the deployed runtime copies — edit here and re-run `install.py`.

```
SKILL.md                         skill manifest + agent operating manual (this file)
scripts/
  install.py / export.py         thin CLI wrappers
  claude_logger.py               Claude Code hook entrypoint + redaction
  render_html.py                 transcript.md → static HTML (per file / --recursive)
  codex_session_exporter/
    exporter.py                  Codex hook entrypoint + backfill + redaction
    install_hooks.py             installs both Codex + Claude hooks
    install_launch_agent.py      vault auto-detect + symlink + legacy cleanup
    tests/                       pytest suite
viewer.html                      standalone single-file viewer (no build, double-click)
web/                             React (Vite) static app: multi-file, sidebar, virtualized
examples/sample-transcript.md    demo transcript (input for viewer/render)
```

## Source Repository

Version-controlled at <https://github.com/miridih-jmyou/agent-conversation-logger>. Treat the local skill directory as a working copy of that repo.
