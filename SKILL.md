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

## Source Repository

Version-controlled at <https://github.com/miridih-jmyou/agent-conversation-logger>. Treat the local skill directory as a working copy of that repo.
