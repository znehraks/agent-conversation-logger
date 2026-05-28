---
name: agent-conversation-logger
description: Install, repair, verify, or explain agent conversation/session logging for Codex and Claude Code workflows, including Obsidian live logs, lifecycle hooks, JSONL exports, prompt collection, 대화 수집기, 프롬프트 수집, 세션 로그, or checking whether a conversation was captured.
---

# Agent Conversation Logger

## Purpose

Set up and verify Codex and Claude Code conversation collectors from Claude Code. Keep this as a Claude Code skill separate from the Codex skill because Claude Code uses `~/.claude/skills`, `${CLAUDE_SKILL_DIR}`, and Claude-specific discovery/permission behavior.

## Default Outputs

Codex and Claude Code logs are written to **separate output roots** so each agent's logs live next to its own config directory.

### Codex

- Codex home: `${CODEX_HOME:-$HOME/.codex}`
- Runtime exporter: `${CODEX_HOME:-$HOME/.codex}/codex-session-exporter/exporter.py`
- Hook config: `${CODEX_HOME:-$HOME/.codex}/hooks.json`
- Hook events: `UserPromptSubmit`, `PostToolUse`, `Stop`
- Output root: `${CODEX_SESSION_EXPORTER_OUTPUT_ROOT:-${CODEX_HOME:-$HOME/.codex}/codex-session-exporter/obsidian-output}`
- Live Markdown: `<codex-output>/codex/<session_id>.md`
- Live events: `<codex-output>/data/codex_live_events.jsonl`
- Hook diagnostics: `${CODEX_HOME:-$HOME/.codex}/codex-session-exporter/hook.log.jsonl`
- Optional Obsidian symlink: pass `--obsidian-link`, set `CODEX_SESSION_EXPORTER_OBSIDIAN_LINK`, or set `OBSIDIAN_VAULT` (default link name: `codex-logs`).

### Claude Code

- Runtime logger: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-conversation-logger/claude_logger.py`
- Hook config: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json`
- Hook events: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`
- Output root: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-conversation-logger/output`
- Live Markdown: `<claude-output>/claude-code/<session_id>.md`
- Live events: `<claude-output>/data/claude_live_events.jsonl`
- Hook diagnostics: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-conversation-logger/hook.log.jsonl`
- Optional Obsidian symlink: pass `--claude-obsidian-link`, set `CLAUDE_LOGGER_OBSIDIAN_LINK`, or set `OBSIDIAN_VAULT` (default link name: `claude-logs`).

Live Markdown is the immediate human-readable transcript. `codex_live_events.jsonl` / `claude_live_events.jsonl` are the lightweight event streams. `codex_sessions.jsonl`, `codex_turns.jsonl`, and `codex_tool_calls.jsonl` are Codex analysis tables created by explicit backfill.

## Install Or Repair

Run the bundled installer from this skill directory:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/install.py"
```

The installer copies exporters into runtime directories, installs lifecycle hooks, creates/updates an Obsidian-facing folder when configured or safely auto-detected, and removes the old Codex LaunchAgent polling job by default.

For explicit Obsidian targets (Codex and Claude Code use separate flags):

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/install.py" \
  --obsidian-link "$OBSIDIAN_VAULT/codex-logs" \
  --claude-obsidian-link "$OBSIDIAN_VAULT/claude-logs"
```

To install only one side, use `--no-claude` (Codex only) or `--no-obsidian-link` / `--no-claude-obsidian-link` to skip a specific vault symlink. To target a non-default Claude Code config root: `--claude-config-dir ~/.claude-work`.

## Verify

Check hook support and recent exporter behavior:

```bash
codex features list | rg hooks
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
OUTPUT_ROOT="${CODEX_SESSION_EXPORTER_OUTPUT_ROOT:-$CODEX_HOME/codex-session-exporter/obsidian-output}"
python3 "${CLAUDE_SKILL_DIR}/scripts/export.py" --codex-home "$CODEX_HOME" --output-root "$OUTPUT_ROOT" --include-active --limit 1 --dry-run
tail -n 20 "$CODEX_HOME/codex-session-exporter/hook.log.jsonl"
tail -n 20 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-conversation-logger/hook.log.jsonl"
```

For a specific conversation, search live logs first (both output roots):

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CODEX_OUT="${CODEX_SESSION_EXPORTER_OUTPUT_ROOT:-$CODEX_HOME/codex-session-exporter/obsidian-output}"
CLAUDE_OUT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/agent-conversation-logger/output"
rg -n "<keyword>" "$CODEX_OUT/codex" "$CODEX_OUT/data" "$CLAUDE_OUT/claude-code" "$CLAUDE_OUT/data"
```

Report the session ID, live Markdown path, matching event count, and whether diagnostics include the expected hook events.

## Backfill For Analysis

When the user wants analysis-ready tables, run:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/export.py" \
  --codex-home "${CODEX_HOME:-$HOME/.codex}" \
  --output-root "${CODEX_SESSION_EXPORTER_OUTPUT_ROOT:-${CODEX_HOME:-$HOME/.codex}/codex-session-exporter/obsidian-output}" \
  --include-active
```

Use `--force` only for intentional rebuilds. Do not pass `--include-developer-prompts` unless the user explicitly asks to export developer/system prompt bodies; the default keeps only presence, hash, and character count.

## Explain Clearly

Use this wording when the distinction matters:

- Live log: append-only transcript for immediate review in Obsidian. Codex writes to `<codex-output>/codex/<session>.md`; Claude Code writes to `<claude-output>/claude-code/<session>.md`. The two output roots are independent.
- Live events JSONL: event-level stream for lightweight parsing (`codex_live_events.jsonl`, `claude_live_events.jsonl`).
- Normalized JSONL: Codex analysis tables generated by backfill.

If live logs exist but normalized tables do not, the conversation was captured; only the analysis table refresh is pending.

## Source Repository

This skill is version-controlled at <https://github.com/miridih-jmyou/agent-conversation-logger>. Treat the local skill directory as a working copy of that repo.
