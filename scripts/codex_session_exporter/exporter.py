from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
import time
import re
from pathlib import Path
from typing import Any, Iterable


def env_path(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else fallback


DEFAULT_CODEX_HOME = env_path("CODEX_HOME", Path.home() / ".codex")
DEFAULT_OUTPUT_ROOT = env_path(
    "CODEX_SESSION_EXPORTER_OUTPUT_ROOT",
    DEFAULT_CODEX_HOME / "codex-session-exporter" / "obsidian-output",
)

SECRET_PATTERNS = [
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*)[^\s'\"`]+"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{6,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
]

FILE_PATTERN = re.compile(
    r"(?<![\w-])(?:/[^ \n\t,;:'\"`<>]+|[A-Za-z0-9_@.()가-힣-]+(?:/[A-Za-z0-9_@.()가-힣-]+)+)"
    r"\.(?:ts|tsx|js|jsx|mjs|cjs|py|md|mdx|json|jsonl|toml|ya?ml|html|css|scss|sql|sh|txt|tsx?)"
)

VERIFICATION_COMMAND_PATTERN = re.compile(
    r"\b("
    r"npm\s+(?:run\s+)?(?:test|build|lint|typecheck)|"
    r"pnpm\s+(?:run\s+)?(?:test|build|lint|typecheck)|"
    r"yarn\s+(?:run\s+)?(?:test|build|lint|typecheck)|"
    r"pytest|unittest|vitest|jest|playwright|tsc|eslint|ruff|mypy|cargo\s+test|go\s+test"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExportConfig:
    output_root: Path = DEFAULT_OUTPUT_ROOT
    include_developer_prompts: bool = False
    force: bool = False
    max_text_chars: int = 12_000
    excerpt_chars: int = 1_200


@dataclass(frozen=True)
class ParsedSession:
    source_path: Path
    session: dict[str, Any]
    turns: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    markdown: str


@dataclass(frozen=True)
class ExportResult:
    session_id: str
    markdown_path: Path
    exported: bool
    skipped_reason: str | None = None


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.groups() else "[REDACTED]", redacted)
    return redacted


def parse_session_file(path: Path, include_developer_prompts: bool = False) -> ParsedSession:
    rows = read_jsonl(path)
    session_meta = first_payload(rows, "session_meta")
    turn_contexts = [row.get("payload", {}) for row in rows if row.get("type") == "turn_context"]
    response_items = [row for row in rows if row.get("type") == "response_item"]
    events = [row.get("payload", {}) for row in rows if row.get("type") == "event_msg"]

    session_id = str(session_meta.get("id") or infer_session_id_from_path(path))
    started_at = session_meta.get("timestamp") or timestamp_at(rows, 0)
    ended_at = timestamp_at(rows, -1)
    turn_context = turn_contexts[-1] if turn_contexts else {}
    cwd = session_meta.get("cwd") or turn_context.get("cwd")

    messages = parse_messages(response_items, session_id, include_developer_prompts)
    user_prompts = [turn["content"] for turn in messages if turn["role"] == "user" and not is_environment_context(turn["content"])]
    all_user_text = "\n\n".join(user_prompts)
    final_answer = next((turn["content"] for turn in reversed(messages) if turn["role"] == "assistant"), "")
    tool_calls = parse_tool_calls(response_items, session_id)

    mentioned_files = sorted(extract_files(all_user_text))
    verification_commands = [
        str(call["command"])
        for call in tool_calls
        if call.get("is_verification") and call.get("exit_code") == 0 and call.get("command")
    ]
    failed_tool_calls = [call for call in tool_calls if call.get("exit_code") not in (None, 0)]
    task_type = infer_task_type(all_user_text)
    requested_mode = infer_requested_mode(all_user_text)
    developer_text = collect_developer_text(session_meta, messages)
    developer_hash = sha256_text(developer_text) if developer_text else None

    session = {
        "schema_version": 1,
        "session_id": session_id,
        "source_path": str(path),
        "thread_name": None,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds(started_at, ended_at),
        "cwd": cwd,
        "originator": session_meta.get("originator"),
        "cli_version": session_meta.get("cli_version"),
        "model_provider": session_meta.get("model_provider"),
        "model": turn_context.get("model"),
        "reasoning_effort": turn_context.get("effort"),
        "approval_policy": turn_context.get("approval_policy"),
        "sandbox_policy": turn_context.get("sandbox_policy"),
        "timezone": turn_context.get("timezone"),
        "git": session_meta.get("git") or {},
        "task_type": task_type,
        "requested_mode": requested_mode,
        "completion_status": infer_completion_status(events, final_answer),
        "user_intent": summarize_intent(user_prompts),
        "final_outcome_summary": summarize_intent([final_answer]),
        "prompt_anatomy": {
            "raw_user_prompt": redact_and_limit(user_prompts[0] if user_prompts else "", 4000),
            "normalized_intent": summarize_intent(user_prompts),
            "explicit_constraints": extract_constraints(all_user_text),
            "acceptance_criteria": extract_acceptance_criteria(all_user_text),
            "mentioned_files": mentioned_files,
            "mentioned_tools": extract_mentioned_tools(all_user_text),
            "ambiguities": [],
            "missing_context": [],
            "risk_level": infer_risk_level(all_user_text, cwd),
        },
        "context_packet": {
            "source_types": sorted(infer_context_source_types(tool_calls)),
            "files_read": sorted(extract_files_from_tool_commands(tool_calls)),
            "memory_used": any("memory" in str(call.get("name", "")).lower() for call in tool_calls),
            "external_sources_used": sorted(infer_external_sources(tool_calls)),
            "source_freshness_risk": infer_source_freshness_risk(tool_calls),
        },
        "agent_behavior": {
            "tools_used": sorted({str(call.get("name")) for call in tool_calls if call.get("name")}),
            "commands_run": [call["command"] for call in tool_calls if call.get("command")],
            "errors_encountered": [
                {
                    "name": call.get("name"),
                    "command": call.get("command"),
                    "exit_code": call.get("exit_code"),
                    "output_excerpt": call.get("output_excerpt"),
                }
                for call in failed_tool_calls
            ],
            "verification_attempts": [
                {
                    "command": call.get("command"),
                    "exit_code": call.get("exit_code"),
                }
                for call in tool_calls
                if call.get("is_verification")
            ],
        },
        "quality_labels": {
            "prompt_clarity": None,
            "context_sufficiency": None,
            "constraint_compliance": None,
            "outcome_quality": None,
            "verification_quality": None,
            "rework_needed": None,
            "reviewer_notes": "",
            "better_prompt": "",
            "better_context_packet": "",
        },
        "developer_prompt_present": bool(developer_text),
        "developer_prompt_sha256": developer_hash,
        "developer_prompt_chars": len(developer_text),
        "developer_prompt_exported": include_developer_prompts,
        "message_count": len(messages),
        "tool_call_count": len(tool_calls),
        "verification_commands": verification_commands,
        "mentioned_files": mentioned_files,
    }
    markdown = build_markdown(session, messages, tool_calls, include_developer_prompts)
    return ParsedSession(source_path=path, session=session, turns=messages, tool_calls=tool_calls, markdown=markdown)


def export_session_file(path: Path, config: ExportConfig) -> ExportResult:
    root = Path(config.output_root)
    state_path = root / "state" / "processed_sessions.json"
    state = read_state(state_path)
    source_stat = path.stat()
    source_fingerprint = {
        "source_mtime_ns": source_stat.st_mtime_ns,
        "source_size": source_stat.st_size,
    }
    session_id = infer_session_id_from_path(path)
    processed_session_id = session_id if session_id in state["processed"] else find_processed_session_id_by_source(state, path)

    if processed_session_id and not config.force:
        previous_record = state["processed"][processed_session_id]
        if (
            previous_record.get("source_mtime_ns") == source_fingerprint["source_mtime_ns"]
            and previous_record.get("source_size") == source_fingerprint["source_size"]
        ):
            previous = previous_record.get("markdown_path")
            return ExportResult(
                session_id=processed_session_id,
                markdown_path=Path(previous) if previous else Path(config.output_root),
                exported=False,
                skipped_reason="already_processed",
            )

    parsed = parse_session_file(path, include_developer_prompts=config.include_developer_prompts)
    session_id = parsed.session["session_id"]
    markdown_path = note_path(root, parsed.session)

    ensure_output_dirs(root)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(parsed.markdown, encoding="utf-8")

    upsert_jsonl(root / "data" / "codex_sessions.jsonl", [parsed.session], session_id=session_id)
    upsert_jsonl(root / "data" / "codex_turns.jsonl", parsed.turns, session_id=session_id)
    upsert_jsonl(root / "data" / "codex_tool_calls.jsonl", parsed.tool_calls, session_id=session_id)

    state["processed"][session_id] = {
        "source_path": str(path),
        "markdown_path": str(markdown_path),
        "exported_at": now_utc(),
        **source_fingerprint,
    }
    write_state(state_path, state)
    return ExportResult(session_id=session_id, markdown_path=markdown_path, exported=True)


def find_session_files(codex_home: Path, include_active: bool = False) -> list[Path]:
    roots = [codex_home / "archived_sessions"]
    if include_active:
        roots.append(codex_home / "sessions")
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(path for path in root.rglob("*.jsonl") if path.is_file())
    return sorted(files, key=lambda path: path.stat().st_mtime)


def export_new_sessions(
    codex_home: Path,
    config: ExportConfig,
    include_active: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
) -> list[ExportResult]:
    files = find_session_files(codex_home, include_active=include_active)
    if limit is not None:
        files = files[-limit:]
    results: list[ExportResult] = []
    for path in files:
        if dry_run:
            parsed = parse_session_file(path, include_developer_prompts=config.include_developer_prompts)
            results.append(ExportResult(parsed.session["session_id"], note_path(config.output_root, parsed.session), True))
            continue
        results.append(export_session_file(path, config))
    return results


def append_live_session_file(path: Path, output_root: Path, excerpt_chars: int = 2_000) -> dict[str, Any]:
    root = Path(output_root)
    state_path = root / "state" / "live_append_state.json"
    state = read_live_append_state(state_path)
    source_key = str(path)
    source_record = state["sources"].get(source_key, {})
    current_size = path.stat().st_size
    offset = int(source_record.get("offset", 0) or 0)
    if offset > current_size:
        offset = 0

    session_id = str(source_record.get("session_id") or infer_session_id_from_path(path))
    started_at = source_record.get("started_at")
    with path.open("rb") as file:
        file.seek(offset)
        raw = file.read()
    new_rows, new_offset = parse_complete_jsonl_rows(raw, offset)
    for row in new_rows:
        if row.get("type") == "session_meta":
            payload = row.get("payload") or {}
            session_id = str(payload.get("id") or session_id)
            started_at = payload.get("timestamp") or row.get("timestamp") or started_at

    markdown_path = Path(source_record.get("markdown_path") or live_note_path(root, session_id, started_at))
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    if not markdown_path.exists():
        markdown_path.write_text(build_live_header(session_id, path, started_at), encoding="utf-8")

    events = []
    markdown_chunks = []
    for row in new_rows:
        event = row_to_live_event(row, session_id, path, excerpt_chars)
        if not event:
            continue
        events.append(event)
        markdown_chunks.append(live_event_to_markdown(event))

    if markdown_chunks:
        with markdown_path.open("a", encoding="utf-8") as file:
            file.write("\n".join(markdown_chunks))
            file.write("\n")
        append_jsonl(root / "data" / "codex_live_events.jsonl", events)

    state["sources"][source_key] = {
        "session_id": session_id,
        "started_at": started_at,
        "offset": new_offset,
        "source_size": current_size,
        "markdown_path": str(markdown_path),
        "updated_at": now_utc(),
    }
    write_live_append_state(state_path, state)
    return {
        "session_id": session_id,
        "source_path": str(path),
        "markdown_path": str(markdown_path),
        "appended_events": len(events),
        "offset": new_offset,
    }


def parse_complete_jsonl_rows(raw: bytes, offset: int) -> tuple[list[dict[str, Any]], int]:
    if not raw:
        return [], offset
    if raw.endswith(b"\n"):
        complete = raw
        unconsumed = 0
    else:
        last_newline = raw.rfind(b"\n")
        if last_newline == -1:
            return [], offset
        complete = raw[: last_newline + 1]
        unconsumed = len(raw) - len(complete)

    rows: list[dict[str, Any]] = []
    for line in complete.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(row)
    return rows, offset + len(raw) - unconsumed


def append_from_hook_input(hook_input: dict[str, Any], output_root: Path, hook_log_path: Path | None = None) -> dict[str, Any]:
    event_name = str(hook_input.get("hook_event_name") or "unknown")
    transcript_path = hook_input.get("transcript_path")
    result: dict[str, Any] = {
        "event_name": event_name,
        "session_id": hook_input.get("session_id"),
        "turn_id": hook_input.get("turn_id"),
        "transcript_path": transcript_path,
        "appended": False,
    }
    if not transcript_path:
        log_hook_status(hook_log_path, {**result, "reason": "missing_transcript_path"})
        return result
    source_path = Path(str(transcript_path))
    if not source_path.exists():
        log_hook_status(hook_log_path, {**result, "reason": "transcript_path_not_found"})
        return result
    append_result = append_live_session_file(source_path, output_root)
    result.update(append_result)
    result["appended"] = append_result.get("appended_events", 0) > 0
    result["reason"] = "ok"
    log_hook_status(hook_log_path, result)
    return result


def append_from_hook_stdin(output_root: Path, hook_log_path: Path | None = None) -> dict[str, Any]:
    raw = sys.stdin.read()
    try:
        hook_input = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as error:
        result = {"event_name": "unknown", "appended": False, "reason": f"invalid_hook_json: {error}"}
        log_hook_status(hook_log_path, result)
        return result
    try:
        return append_from_hook_input(hook_input, output_root, hook_log_path)
    except Exception as error:  # Hooks must never block the Codex turn because logging failed.
        result = {
            "event_name": hook_input.get("hook_event_name"),
            "session_id": hook_input.get("session_id"),
            "turn_id": hook_input.get("turn_id"),
            "transcript_path": hook_input.get("transcript_path"),
            "appended": False,
            "reason": f"{type(error).__name__}: {error}",
        }
        log_hook_status(hook_log_path, result)
        return result


def log_hook_status(path: Path | None, record: dict[str, Any]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl(path, [{**record, "logged_at": now_utc()}])


def append_live_sessions(
    codex_home: Path,
    output_root: Path,
    limit: int | None = None,
    active_within_hours: float | None = 24,
    max_active_mb: float | None = 10,
) -> list[dict[str, Any]]:
    active_root = codex_home / "sessions"
    cutoff = None if active_within_hours is None else time.time() - active_within_hours * 60 * 60
    max_bytes = None if max_active_mb is None else int(max_active_mb * 1024 * 1024)
    files = sorted(
        [
            path
            for path in active_root.rglob("*.jsonl")
            if path.is_file()
            and (cutoff is None or path.stat().st_mtime >= cutoff)
            and (max_bytes is None or path.stat().st_size <= max_bytes)
        ]
        if active_root.exists()
        else [],
        key=lambda path: path.stat().st_mtime,
    )
    if limit is not None:
        files = files[-limit:]
    return [append_live_session_file(path, output_root) for path in files]


def row_to_live_event(row: dict[str, Any], session_id: str, source_path: Path, excerpt_chars: int) -> dict[str, Any] | None:
    payload = row.get("payload") or {}
    row_type = row.get("type")
    timestamp = row.get("timestamp")
    base = {
        "schema_version": 1,
        "session_id": session_id,
        "source_path": str(source_path),
        "timestamp": timestamp,
    }
    if row_type == "response_item" and payload.get("type") == "message":
        role = str(payload.get("role") or "unknown")
        if role in {"developer", "system"}:
            return None
        text = extract_content_text(payload.get("content"))
        if is_environment_context(text):
            return None
        return {
            **base,
            "kind": "message",
            "role": role,
            "text": redact_and_limit(text, excerpt_chars),
            "content_chars": len(text),
        }
    if row_type == "response_item" and payload.get("type") == "function_call":
        args = parse_arguments(payload.get("arguments"))
        command = args.get("cmd") if isinstance(args, dict) else None
        return {
            **base,
            "kind": "tool_call",
            "name": payload.get("name"),
            "call_id": payload.get("call_id"),
            "command": command,
            "arguments": redact_and_limit(json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(payload.get("arguments")), excerpt_chars),
        }
    if row_type == "response_item" and payload.get("type") == "function_call_output":
        output = str(payload.get("output") or "")
        return {
            **base,
            "kind": "tool_output",
            "call_id": payload.get("call_id"),
            "exit_code": extract_exit_code(output),
            "output_excerpt": redact_and_limit(output, excerpt_chars),
        }
    return None


def live_event_to_markdown(event: dict[str, Any]) -> str:
    timestamp = event.get("timestamp") or "unknown-time"
    kind = event.get("kind")
    if kind == "message":
        role = str(event.get("role") or "unknown").upper()
        return f"\n## {timestamp} - {role}\n\n```text\n{event.get('text') or ''}\n```\n"
    if kind == "tool_call":
        name = event.get("name") or "tool"
        command = event.get("command")
        body = f"- call_id: `{event.get('call_id')}`\n"
        if command:
            body += f"- command: `{command}`\n"
        else:
            body += f"- arguments: `{event.get('arguments')}`\n"
        return f"\n## {timestamp} - TOOL CALL `{name}`\n\n{body}"
    if kind == "tool_output":
        exit_code = event.get("exit_code")
        exit_text = "-" if exit_code is None else str(exit_code)
        return f"\n## {timestamp} - TOOL OUTPUT `{event.get('call_id')}`\n\n- exit_code: `{exit_text}`\n\n```text\n{event.get('output_excerpt') or ''}\n```\n"
    return ""


def build_live_header(session_id: str, source_path: Path, started_at: str | None) -> str:
    return "\n".join(
        [
            "---",
            yaml_frontmatter(
                {
                    "session_id": session_id,
                    "started_at": started_at,
                    "source_path": str(source_path),
                    "tags": ["codex-live-log"],
                }
            ),
            "---",
            "",
            f"# Codex Live Log - {session_id}",
            "",
            "> Append-only refined log. Existing sections are not rewritten.",
            "",
        ]
    )


def live_note_path(root: Path, session_id: str, started_at: str | None) -> Path:
    return root / "codex-logs" / session_id / "transcript.md"


def read_live_append_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sources": {}}
    with path.open(encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state.get("sources"), dict):
        return {"sources": {}}
    return state


def write_live_append_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSONL row") from error
    return rows


def first_payload(rows: list[dict[str, Any]], row_type: str) -> dict[str, Any]:
    for row in rows:
        if row.get("type") == row_type:
            payload = row.get("payload")
            return payload if isinstance(payload, dict) else {}
    return {}


def parse_messages(
    response_items: list[dict[str, Any]], session_id: str, include_developer_prompts: bool
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for index, row in enumerate(response_items):
        payload = row.get("payload") or {}
        if payload.get("type") != "message":
            continue
        role = str(payload.get("role") or "unknown")
        raw_text = extract_content_text(payload.get("content"))
        text = redact_and_limit(raw_text, 12_000)
        omitted = role in {"developer", "system"} and not include_developer_prompts
        messages.append(
            {
                "schema_version": 1,
                "session_id": session_id,
                "turn_index": len(messages),
                "response_item_index": index,
                "timestamp": row.get("timestamp"),
                "role": role,
                "content": "[omitted: developer/system prompt]" if omitted else text,
                "content_chars": len(raw_text),
                "content_sha256": sha256_text(raw_text) if raw_text else None,
                "omitted": omitted,
                "is_environment_context": is_environment_context(raw_text),
            }
        )
    return messages


def parse_tool_calls(response_items: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    outputs_by_call_id = {
        (row.get("payload") or {}).get("call_id"): row
        for row in response_items
        if (row.get("payload") or {}).get("type") in {"function_call_output", "tool_search_output"}
    }
    calls: list[dict[str, Any]] = []
    for index, row in enumerate(response_items):
        payload = row.get("payload") or {}
        payload_type = payload.get("type")
        if payload_type not in {"function_call", "tool_search_call"}:
            continue
        call_id = payload.get("call_id")
        output_payload = (outputs_by_call_id.get(call_id) or {}).get("payload") or {}
        args_raw = payload.get("arguments")
        args = parse_arguments(args_raw)
        output_text = str(output_payload.get("output") or output_payload.get("tools") or "")
        command = args.get("cmd") if isinstance(args, dict) else None
        exit_code = extract_exit_code(output_text)
        name = payload.get("name") or ("tool_search" if payload_type == "tool_search_call" else None)
        calls.append(
            {
                "schema_version": 1,
                "session_id": session_id,
                "call_index": len(calls),
                "response_item_index": index,
                "timestamp": row.get("timestamp"),
                "type": payload_type,
                "name": name,
                "call_id": call_id,
                "arguments": redact_and_limit(json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args_raw), 6000),
                "command": command,
                "workdir": args.get("workdir") if isinstance(args, dict) else None,
                "exit_code": exit_code,
                "is_verification": bool(command and VERIFICATION_COMMAND_PATTERN.search(command)),
                "output_excerpt": redact_and_limit(output_text, 2000),
            }
        )
    return calls


def parse_arguments(arguments: Any) -> Any:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return arguments
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return arguments


def extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            value = item.get("text") or item.get("content")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def collect_developer_text(session_meta: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    base = session_meta.get("base_instructions")
    if isinstance(base, dict) and isinstance(base.get("text"), str):
        parts.append(base["text"])
    for message in messages:
        if message.get("role") in {"developer", "system"} and message.get("content_sha256"):
            parts.append(str(message.get("content_sha256")))
    return "\n".join(parts)


def extract_exit_code(output: str) -> int | None:
    match = re.search(r"(?:Process exited with code|Exit code:)\s*(-?\d+)", output)
    if not match:
        return None
    return int(match.group(1))


def extract_files(text: str) -> set[str]:
    return {match.group(0).rstrip(".,:;") for match in FILE_PATTERN.finditer(text)}


def extract_files_from_tool_commands(tool_calls: list[dict[str, Any]]) -> set[str]:
    files: set[str] = set()
    read_command = re.compile(r"\b(?:cat|sed|nl|rg|find|ls|head|tail|git\s+show)\b")
    for call in tool_calls:
        command = str(call.get("command") or "")
        if read_command.search(command):
            files.update(extract_files(command))
    return files


def extract_mentioned_tools(text: str) -> list[str]:
    tools = []
    for tool in ["npm", "pnpm", "yarn", "pytest", "playwright", "slack", "confluence", "jira", "github", "obsidian"]:
        if re.search(rf"\b{re.escape(tool)}\b", text, re.IGNORECASE):
            tools.append(tool)
    return tools


def extract_constraints(text: str) -> list[str]:
    candidates = []
    patterns = [
        r"[^.\n]*(?:하지마|말고|없이|반드시|꼭|최대한|only|must|never|without)[^.\n]*",
        r"[^.\n]*(?:수정하지|분석만|검수만|한국어|영어)[^.\n]*",
    ]
    for pattern in patterns:
        candidates.extend(match.group(0).strip() for match in re.finditer(pattern, text, re.IGNORECASE))
    return compact_unique(candidates, limit=12)


def extract_acceptance_criteria(text: str) -> list[str]:
    candidates = []
    for line in text.splitlines():
        stripped = line.strip(" -\t")
        if re.search(r"(검증|확인|테스트|빌드|lint|build|test|완료|성공)", stripped, re.IGNORECASE):
            candidates.append(stripped)
    return compact_unique(candidates, limit=12)


def infer_task_type(text: str) -> str:
    lowered = text.lower()
    if re.search(r"^\s*Automation:|^\s*Automation ID:|^\s*자동화\s*ID\s*:", text, re.IGNORECASE | re.MULTILINE):
        return "automation"
    if re.search(r"코드\s*리뷰|코드리뷰|review", text, re.IGNORECASE):
        return "code_review"
    if re.search(r"자동화|automation|반복|스케줄|schedule|reminder", text, re.IGNORECASE):
        return "automation"
    if re.search(r"조사|리서치|research|분석", text, re.IGNORECASE):
        return "research"
    if re.search(r"figma|ui|ux|디자인|design", lowered, re.IGNORECASE):
        return "design"
    if re.search(r"작성|문서|요약|write|draft|summarize", text, re.IGNORECASE):
        return "writing"
    if re.search(r"버그|고쳐|수정|에러|오류|bug|fix|lint|build|test", text, re.IGNORECASE):
        return "bugfix"
    return "general"


def infer_requested_mode(text: str) -> str:
    if re.search(r"분석만|검수만|수정하지|read[- ]?only|analy[sz]e only", text, re.IGNORECASE):
        return "analyze_only"
    if re.search(r"코드\s*리뷰|코드리뷰|검수|review", text, re.IGNORECASE):
        return "review"
    if re.search(r"자동화|스케줄|반복|automation|schedule", text, re.IGNORECASE):
        return "automate"
    if re.search(r"고쳐|수정|구현|만들|완료|착수|fix|implement|create|build", text, re.IGNORECASE):
        return "implement"
    if re.search(r"설명|알려|어떤|what|how|explain", text, re.IGNORECASE):
        return "explain"
    return "unspecified"


def infer_completion_status(events: list[dict[str, Any]], final_answer: str) -> str:
    if re.search(r"blocked|막혔|불가능|못 했|실패", final_answer, re.IGNORECASE):
        return "blocked"
    if any(event.get("type") == "task_complete" for event in events):
        return "complete"
    if final_answer:
        return "answered"
    return "unknown"


def infer_context_source_types(tool_calls: list[dict[str, Any]]) -> set[str]:
    source_types: set[str] = set()
    for call in tool_calls:
        name = str(call.get("name") or "")
        command = str(call.get("command") or "")
        if name in {"exec_command", "write_stdin"}:
            source_types.add("terminal")
        if re.search(r"\b(?:cat|sed|nl|rg|find|ls|git\s+(?:show|diff|status))\b", command):
            source_types.add("file_or_git")
        if "slack" in name or name.startswith("conversations_"):
            source_types.add("slack")
        if "confluence" in name:
            source_types.add("confluence")
        if "jira" in name:
            source_types.add("jira")
        if "github" in name or command.startswith("gh "):
            source_types.add("github")
        if call.get("type") == "tool_search_call":
            source_types.add("tool_discovery")
    return source_types


def infer_external_sources(tool_calls: list[dict[str, Any]]) -> set[str]:
    return {
        source
        for source in infer_context_source_types(tool_calls)
        if source in {"slack", "confluence", "jira", "github", "tool_discovery"}
    }


def infer_source_freshness_risk(tool_calls: list[dict[str, Any]]) -> str:
    source_types = infer_context_source_types(tool_calls)
    if {"slack", "confluence", "jira", "github"} & source_types:
        return "live_source_used"
    if source_types:
        return "local_source_only"
    return "unverified_from_prompt_only"


def infer_risk_level(text: str, cwd: str | None) -> str:
    if re.search(r"배포|production|prod|secret|token|권한|법률|금융|의료|delete|삭제", text, re.IGNORECASE):
        return "high"
    if cwd and "/workspace/miridih/" in cwd:
        return "medium"
    return "low"


def summarize_intent(texts: list[str]) -> str:
    combined = " ".join(text.strip() for text in texts if text.strip())
    combined = re.sub(r"\s+", " ", combined)
    return redact_and_limit(combined, 500)


def build_markdown(
    session: dict[str, Any], turns: list[dict[str, Any]], tool_calls: list[dict[str, Any]], include_developer_prompts: bool
) -> str:
    frontmatter = {
        "session_id": session["session_id"],
        "started_at": session.get("started_at"),
        "cwd": session.get("cwd"),
        "task_type": session.get("task_type"),
        "requested_mode": session.get("requested_mode"),
        "completion_status": session.get("completion_status"),
        "tags": ["codex-session", f"task/{session.get('task_type')}"],
    }
    prompt = session["prompt_anatomy"]
    context = session["context_packet"]
    behavior = session["agent_behavior"]
    user_turns = [turn for turn in turns if turn["role"] == "user" and not turn["is_environment_context"]]
    assistant_turns = [turn for turn in turns if turn["role"] == "assistant"]

    return "\n".join(
        [
            "---",
            yaml_frontmatter(frontmatter),
            "---",
            "",
            f"# Codex Session - {session['session_id']}",
            "",
            "## User Intent",
            "",
            session.get("user_intent") or "",
            "",
            "## Prompt Anatomy",
            "",
            bullet("Task type", session.get("task_type")),
            bullet("Requested mode", session.get("requested_mode")),
            bullet("Risk level", prompt.get("risk_level")),
            bullet("Mentioned files", ", ".join(prompt.get("mentioned_files") or []) or "-"),
            bullet("Mentioned tools", ", ".join(prompt.get("mentioned_tools") or []) or "-"),
            "",
            "### Explicit Constraints",
            lines_or_dash(prompt.get("explicit_constraints")),
            "",
            "### Acceptance Criteria",
            lines_or_dash(prompt.get("acceptance_criteria")),
            "",
            "## Context Used",
            "",
            bullet("Source types", ", ".join(context.get("source_types") or []) or "-"),
            bullet("External sources", ", ".join(context.get("external_sources_used") or []) or "-"),
            bullet("Freshness", context.get("source_freshness_risk")),
            bullet("Files read", ", ".join(context.get("files_read") or []) or "-"),
            "",
            "## Agent Actions",
            "",
            bullet("Tools used", ", ".join(behavior.get("tools_used") or []) or "-"),
            bullet("Tool calls", session.get("tool_call_count")),
            bullet("Verification commands", ", ".join(session.get("verification_commands") or []) or "-"),
            "",
            "## Outcome",
            "",
            bullet("Completion status", session.get("completion_status")),
            bullet("Final answer", session.get("final_outcome_summary") or "-"),
            "",
            "## Review",
            "",
            "- Prompt clarity:",
            "- Context sufficiency:",
            "- Constraint compliance:",
            "- Outcome quality:",
            "- Verification quality:",
            "- Rework needed:",
            "- Better prompt:",
            "- Better context packet:",
            "- Reviewer notes:",
            "",
            "## Prompt Excerpts",
            "",
            text_block("User prompt", user_turns[0]["content"] if user_turns else ""),
            text_block("Final answer", assistant_turns[-1]["content"] if assistant_turns else ""),
            developer_prompt_note(include_developer_prompts, session),
            "",
            "## Raw Pointers",
            "",
            bullet("Source JSONL", session.get("source_path")),
            bullet("Developer prompt SHA-256", session.get("developer_prompt_sha256") or "-"),
            "",
        ]
    )


def developer_prompt_note(include_developer_prompts: bool, session: dict[str, Any]) -> str:
    if not session.get("developer_prompt_present"):
        return ""
    if include_developer_prompts:
        return "> Developer/system prompt content was exported in the turn JSONL."
    return "> Developer/system prompt content was omitted by default; SHA-256 and character count were retained."


def yaml_frontmatter(values: dict[str, Any]) -> str:
    lines = []
    for key, value in values.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {json.dumps(item, ensure_ascii=False)}" for item in value)
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines)


def bullet(label: str, value: Any) -> str:
    return f"- {label}: {value if value is not None else '-'}"


def lines_or_dash(values: Iterable[str] | None) -> str:
    items = [value for value in values or [] if value]
    if not items:
        return "-"
    return "\n".join(f"- {item}" for item in items)


def text_block(label: str, text: str) -> str:
    return f"### {label}\n\n```text\n{redact_and_limit(text, 3000)}\n```"


def note_path(root: Path, session: dict[str, Any]) -> Path:
    date = safe_date(session.get("started_at"))
    session_id = str(session["session_id"])
    slug = slugify(session.get("user_intent") or session_id)
    return Path(root) / "sessions" / date / f"{date}-{session_id[:8]}-{slug}.md"


def ensure_output_dirs(root: Path) -> None:
    for relative in ["data", "state", "sessions"]:
        (root / relative).mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def upsert_jsonl(path: Path, rows: list[dict[str, Any]], session_id: str) -> None:
    existing_rows: list[dict[str, Any]] = []
    if path.exists():
        existing_rows = [row for row in read_jsonl(path) if row.get("session_id") != session_id]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in existing_rows + rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed": {}}
    with path.open(encoding="utf-8") as file:
        state = json.load(file)
    if "processed" not in state or not isinstance(state["processed"], dict):
        return {"processed": {}}
    return state


def find_processed_session_id_by_source(state: dict[str, Any], path: Path) -> str | None:
    source_path = str(path)
    for session_id, record in state.get("processed", {}).items():
        if isinstance(record, dict) and record.get("source_path") == source_path:
            return str(session_id)
    return None


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def redact_and_limit(text: str, limit: int) -> str:
    redacted = redact_secrets(text)
    if len(redacted) <= limit:
        return redacted
    return redacted[:limit].rstrip() + f"\n...[truncated {len(redacted) - limit} chars]"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compact_unique(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(redact_and_limit(normalized, 500))
        if len(result) >= limit:
            break
    return result


def is_environment_context(text: str) -> bool:
    return text.strip().startswith("<environment_context>")


def infer_session_id_from_path(path: Path) -> str:
    match = re.search(r"(019[a-z0-9-]{20,})", path.name, re.IGNORECASE)
    return match.group(1) if match else path.stem


def timestamp_at(rows: list[dict[str, Any]], index: int) -> str | None:
    if not rows:
        return None
    try:
        return rows[index].get("timestamp")
    except IndexError:
        return None


def duration_seconds(started_at: str | None, ended_at: str | None) -> int | None:
    start = parse_datetime(started_at)
    end = parse_datetime(ended_at)
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def safe_date(value: str | None) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return "unknown-date"
    return parsed.date().isoformat()


def slugify(text: str) -> str:
    normalized = re.sub(r"\s+", "-", text.lower()).strip("-")
    normalized = re.sub(r"[^a-z0-9가-힣._-]+", "", normalized)
    if not normalized:
        return "session"
    return normalized[:60].strip("-._") or "session"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Codex JSONL sessions to Obsidian-ready Markdown and analysis JSONL.")
    parser.add_argument("paths", nargs="*", type=Path, help="Specific Codex session JSONL files to export.")
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--include-active", action="store_true", help="Also scan ~/.codex/sessions in addition to archived_sessions.")
    parser.add_argument("--include-developer-prompts", action="store_true", help="Export redacted developer/system prompt text.")
    parser.add_argument("--force", action="store_true", help="Re-export sessions even if state says they were processed.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the most recent N discovered session files.")
    parser.add_argument("--dry-run", action="store_true", help="Parse sessions and print planned output paths without writing files.")
    parser.add_argument("--append-live", action="store_true", help="Append only newly added active-session rows to live Markdown logs.")
    parser.add_argument("--active-within-hours", type=float, default=24, help="For --append-live, only scan active JSONL files modified within this many hours.")
    parser.add_argument("--max-active-mb", type=float, default=10, help="For --append-live, skip active JSONL files larger than this size.")
    parser.add_argument("--from-hook-stdin", action="store_true", help="Read Codex hook JSON from stdin and append that transcript only.")
    parser.add_argument(
        "--hook-log",
        type=Path,
        default=DEFAULT_CODEX_HOME / "codex-session-exporter" / "hook.log.jsonl",
        help="Append hook-mode diagnostics here. Use an empty value to disable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config = ExportConfig(
        output_root=args.output_root,
        include_developer_prompts=args.include_developer_prompts,
        force=args.force,
    )
    if args.from_hook_stdin:
        hook_log_path = args.hook_log if str(args.hook_log) else None
        append_from_hook_stdin(args.output_root, hook_log_path)
        return 0
    if args.append_live:
        paths = args.paths
        if paths:
            results = [append_live_session_file(path, args.output_root) for path in paths]
        else:
            results = append_live_sessions(
                args.codex_home,
                args.output_root,
                limit=args.limit,
                active_within_hours=args.active_within_hours,
                max_active_mb=args.max_active_mb,
            )
        print(json.dumps({"processed": len(results), "results": results}, ensure_ascii=False, indent=2))
        return 0

    if args.paths:
        results = []
        for path in args.paths:
            if args.dry_run:
                parsed = parse_session_file(path, include_developer_prompts=config.include_developer_prompts)
                results.append(ExportResult(parsed.session["session_id"], note_path(config.output_root, parsed.session), True))
            else:
                results.append(export_session_file(path, config))
    else:
        results = export_new_sessions(
            codex_home=args.codex_home,
            config=config,
            include_active=args.include_active,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    print(
        json.dumps(
            {
                "exported": sum(1 for result in results if result.exported),
                "skipped": sum(1 for result in results if not result.exported),
                "results": [
                    {
                        "session_id": result.session_id,
                        "markdown_path": str(result.markdown_path),
                        "exported": result.exported,
                        "skipped_reason": result.skipped_reason,
                    }
                    for result in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
