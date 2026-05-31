# HANDOFF — agent-conversation-logger

A working handoff for whoever (human or agent) picks this up next. Captures **what
exists now**, the **design decisions + why**, the **journey** (incl. the pivots), and
**open items**. Last updated: 2026-06-01.

> Quick orientation: this repo logs Codex + Claude Code conversations into an Obsidian
> vault as append-only Markdown, and ships viewers to read them. The work in this session
> added the viewers, token capture, a qualitative-eval rubric, size-cap rotation, and a
> React app — then moved the repo to a personal GitHub account.

---

## 1. What this is

- **Loggers** (lifecycle hooks) capture every prompt / tool call / output into
  `<vault>/agent-logs/{claude,codex}-logs/<session_id>/transcript.md`, plus a JSONL event
  stream. Both engines emit a **byte-identical transcript schema** so one parser/renderer
  reads either.
- **Viewers** render those transcripts as an iMessage-style chat + analytics.
- **Repo**: `znehraks/agent-conversation-logger` (private). Source of truth; `install.py`
  deploys runtime copies to `~/.claude/...` and `~/.codex/...`. **Never edit the deployed
  copies — edit here and re-run `install.py`.**

## 2. Current state (works today)

| Area | Status |
|---|---|
| Claude logger (`scripts/claude_logger.py`) | ✅ live, hooks installed |
| Codex logger (`scripts/codex_session_exporter/exporter.py`) | ✅ live, hooks installed |
| Both write into the vault | ✅ `<vault>/개발/agent-logs/{claude,codex}-logs/` |
| Size-cap rotation (default **1 MB** → `transcript.NNN.md`) | ✅ both engines |
| Per-turn token capture (`## ts - USAGE`) | ✅ both engines |
| `viewer.html` (single file, no build) | ✅ chat / insights / document mode |
| `web/` React app (Vite, static) | ✅ builds; multi-file + sidebar + virtualized |
| `PROMPT-EVAL-RUBRIC.md` (v3 qualitative eval) | ✅ rubric defined; 1 example report produced |
| Tests | ✅ `cd scripts && python3 -m pytest codex_session_exporter/tests/` → 14 pass |
| Netlify deploy | ⏳ config ready (`netlify.toml`), not yet connected |

## 3. Architecture

```
Codex / Claude session
   │ lifecycle hook (stdin = event JSON)
   ▼
runtime logger  (offset-tracked, incremental, append-only, secrets redacted)
   │  rotation: transcript.md > 1MB  →  transcript.NNN.md, fresh transcript.md continues
   ▼
<vault>/agent-logs/
   ├── claude-logs/<id>/transcript.md          (+ transcript.001.md … if rotated)
   ├── codex-logs/<id>/transcript.md
   └── data/{claude,codex}_live_events.jsonl
   │
   ├─ open in Obsidian (native), or
   └─ drop into a viewer ──► viewer.html (1 file)  /  web/ (React, multi-file + virtualized)
```

**Common transcript schema** (both engines, identical layout):
`## <iso-ts> - <KIND>[ \`ident\`]` + metadata bullets + a fenced block, where
`KIND ∈ USER | ASSISTANT | SYSTEM | THINKING | TOOL CALL | TOOL OUTPUT | USAGE`.
Frontmatter is byte-identical; rotated parts keep the frontmatter (incl. the
`*-live-log` tag) so viewers recognize them.

## 4. Design decisions (the planning) + why

1. **Vault-first, both engines in the vault.** Logs land natively in Obsidian. Codex used
   to be split out (its transcripts hit 12MB and froze Obsidian) but that was **reverted** —
   see journey. Now safety comes from rotation, not from location.
2. **Size-cap rotation (1 MB).** A single multi-MB note freezes Obsidian (it reopens the
   last-active file on launch) and any editor/viewer. Cap + rotate so no file is ever large;
   lossless. Tunable via `AGENT_LOGS_MAX_MD_BYTES`.
3. **Token USAGE as per-turn deltas.** Claude reports per-message usage (already a delta);
   Codex emits many cumulative `token_count` events, so the exporter collapses them to one
   **per-batch delta**. Summing USAGE = session total for both → one aggregation in the viewer.
4. **Two viewers, on purpose.** `viewer.html` = zero-install single file (double-click, drop
   one md). `web/` React app = multi-file + session grouping + sidebar TOC + **virtualized**
   stream (so 16k-event sessions / many rotation parts never freeze). Both ported from the
   same parser/CSS.
5. **Filename-first classification.** Viewers decide chat-vs-document by filename
   (`transcript.md` / `transcript.NNN.md` → chat; `*.eval.md` → document; `*-live-log` tag
   as a safety net), **not by content** — so a note containing an example `## ts - USER`
   line is never misread. Unknown names → a "어떻게 열까요?" prompt (viewer.html).
6. **Qualitative eval, not scores.** The prompt/session analysis report (v3) is narrative —
   ✅ 잘한 점 / 🔧 보완점 with cited evidence and severity tags — no per-part or overall
   numeric scores. Reports are named `*.eval.md` and live in `<vault>/agent-logs/prompt-evals/`.
   Fields are tagged 🧮 deterministic vs 🤖 model-judged so automation only sends 🤖 parts to a model.

## 5. The journey / trail (commits oldest → newest)

Pre-session origin (by the original author, now re-attributed): initial logger, per-session
dirs, vault-first writes, HTML viewer, unified schema, call_id→tool_name mapping.

This session's work:
- `viewer.html` — standalone client-side viewer (iMessage UI).
- Insights tab — per-session deterministic analytics (no model).
- Token USAGE capture — both loggers + viewer card.
- `PROMPT-EVAL-RUBRIC.md` — v1 (scores) → v2 (1–5 + weighted grade) → **v3 (qualitative, no
  scores)** per user direction; one example report generated for session `7d1b0ef0`.
- Document mode — viewer renders non-transcript markdown (reports) too.
- Filename-based classification + ambiguous-name prompt.
- **Freeze fix, pivoted twice:** (a) first moved Codex logs *out* of the vault + a redirect
  guard; (b) then realized **rotation makes a huge file impossible**, so **reverted** — both
  engines back in the vault, guard removed, cap lowered to 1 MB. Existing oversized
  transcripts were split into ≤1 MB parts in-place.
- `web/` — React/Vite static app: multi-file drop, session grouping, sidebar TOC w/ jump,
  react-virtuoso stream; Netlify-ready.
- Repo moved: `miridih-jmyou` → **`znehraks`** (new private repo, remote switched, all commit
  authors rewritten to znehraks, force-pushed).

## 6. Repository map

```
SKILL.md                       skill manifest + agent operating manual
HANDOFF.md                     this file
PROMPT-EVAL-RUBRIC.md          v3 qualitative eval rubric + report filename convention
netlify.toml                   root config (base=web) for one-click Netlify deploy
scripts/
  install.py / export.py       thin CLI wrappers
  claude_logger.py             Claude hook entrypoint + redaction + rotation + USAGE
  render_html.py               transcript.md → static HTML (per file / --recursive)
  codex_session_exporter/
    exporter.py                Codex hook entrypoint + backfill + redaction + rotation + USAGE
    install_hooks.py           installs both Codex + Claude hooks
    install_launch_agent.py    vault auto-detect + legacy cleanup
    tests/                     pytest suite (14 tests)
viewer.html                    single-file viewer (no build, double-click)
web/                           React (Vite + TS) static app — multi-file, sidebar, virtualized
examples/sample-transcript.md  demo transcript (every KIND)
```

## 7. How to run / use / deploy

```bash
# (re)install or repair hooks — both engines, vault auto-detect
python3 scripts/install.py --no-trust

# tests
cd scripts && python3 -m pytest codex_session_exporter/tests/ -q

# single-file viewer
open viewer.html                       # drop a transcript.md or a *.eval.md

# React app (local)
cd web && npm install && npm run dev    # http://localhost:5173 ; drop a whole log folder

# React app (build / Netlify)
cd web && npm run build                 # → web/dist
# Netlify: import the repo (login as znehraks), root netlify.toml sets base=web automatically
```
Files dropped into either viewer are read locally (FileReader) — **never uploaded**.

## 8. Open items / pending decisions

- **Rotation filename scheme (A vs B) — UNDECIDED.** Current = A: `transcript.md` is the
  active/newest; rotated parts `transcript.001.md` (oldest) … ascending. It already sorts
  chronologically in Finder. User considered B: zero-padded all-numbered (`transcript.000.md`
  = first … highest = newest, no bare `transcript.md`) for explicit ordering — would touch
  both loggers + `render_html.py` glob + viewer `partRank` + re-split existing. Not done.
- **Netlify**: connect `znehraks/agent-conversation-logger` (login as znehraks; private repo →
  grant repo access). Root `netlify.toml` handles build settings.
- **Eval automation**: the v3 report is currently produced ad-hoc by a model. Could become
  `evaluate_prompts.py` (Anthropic API) or an "AI 평가" button in `web/` (needs a serverless
  proxy to hide the key) — not built.
- **Cross-session insights dashboard** (folder-level aggregates: per-project, time-of-day,
  error trends) — discussed, not built.
- **viewer.html large-file guard** — largely moot now that rotation caps files, but a
  dropped *external* huge md could still bog the single-file viewer (the React app
  virtualizes and is safe).

## 9. Related context

- Memory: `feedback-logger-both-engines` (every change applies to BOTH loggers + keep the
  shared schema), `project-agent-logger-codex-split` (both-in-vault + 1MB rotation policy).
- Example eval report: `<vault>/agent-logs/prompt-evals/2026-05-31-7d1b0ef0-researcher.eval.md`.
- Git author note: all commits are authored as `znehraks <znehraks@gmail.com>` (the original
  author's commits were re-attributed when the repo moved to the personal account).
