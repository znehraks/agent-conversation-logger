# Codex Session Exporter

Exports Codex JSONL session logs into an Obsidian-friendly review note plus analysis JSONL files.

Default sources:

- `${CODEX_HOME:-~/.codex}/archived_sessions/*.jsonl`
- Optional: `${CODEX_HOME:-~/.codex}/sessions/**/*.jsonl`

Default destination:

- Runtime target: `${CODEX_SESSION_EXPORTER_OUTPUT_ROOT:-${CODEX_HOME:-~/.codex}/codex-session-exporter/obsidian-output}`
- Optional Obsidian symlink: pass `--obsidian-link`, set `CODEX_SESSION_EXPORTER_OBSIDIAN_LINK`, or set `OBSIDIAN_VAULT`.
- Installers create the symlink only when a link target is provided or safely auto-detected, so the exporter works on machines without Obsidian.

## Output Layout

```text
개발/codex-logs/
  sessions/YYYY-MM-DD/<session>.md
  data/codex_sessions.jsonl
  data/codex_turns.jsonl
  data/codex_tool_calls.jsonl
  data/codex_live_events.jsonl
  live/YYYY-MM-DD/<session_id>.md
  state/processed_sessions.json
  state/live_append_state.json
```

## Extracted Data

- Session metadata: id, source path, cwd, model, approval/sandbox policy, git metadata, duration
- Prompt anatomy: user intent, task type, requested mode, constraints, acceptance criteria, mentioned files/tools, risk level
- Context packet: local file/git usage, external source usage, freshness risk
- Agent behavior: tools used, shell commands, verification commands, failed tool calls
- Review labels: empty fields for later teammate prompt review
- Developer/system prompts: omitted by default; SHA-256 and character count are retained

## Commands

Run a dry run against recent archived sessions:

```bash
python3 codex_session_exporter/exporter.py --limit 5 --dry-run
```

Export recent archived sessions:

```bash
python3 codex_session_exporter/exporter.py --limit 50
```

Also export redacted developer/system prompt text:

```bash
python3 codex_session_exporter/exporter.py --limit 50 --include-developer-prompts
```

Install Codex lifecycle hooks:

```bash
python3 -m codex_session_exporter.install_hooks
```

The hook installer writes `${CODEX_HOME:-~/.codex}/hooks.json`, tries to trust the installed hook commands through the Codex app-server, and removes the old LaunchAgent. `UserPromptSubmit`, `PostToolUse`, and `Stop` all call the exporter with `--from-hook-stdin`, so each hook invocation appends only the new rows from Codex's `transcript_path`.

Install with an explicit Obsidian link:

```bash
python3 -m codex_session_exporter.install_hooks --obsidian-link "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/DesignC/개발/codex-logs"
```

Run one append-only pass manually:

```bash
python3 codex_session_exporter/exporter.py --append-live --limit 50
```

Legacy macOS polling fallback:

```bash
python3 -m codex_session_exporter.install_launch_agent
```

The LaunchAgent path is macOS-only and is kept as a fallback. Hooks are preferred.

Uninstall the LaunchAgent:

```bash
python3 -m codex_session_exporter.install_launch_agent --uninstall
```
