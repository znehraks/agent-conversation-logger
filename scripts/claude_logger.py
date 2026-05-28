#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*)[^\s'\"`]+"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{6,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.groups() else "[REDACTED]", redacted)
    return redacted


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_complete_jsonl_rows(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    with path.open("rb") as file:
        file.seek(offset)
        data = file.read()
    if not data:
        return rows, offset
    complete_len = len(data)
    if not data.endswith(b"\n"):
        complete_len = data.rfind(b"\n") + 1
        if complete_len <= 0:
            return rows, offset
    for raw_line in data[:complete_len].splitlines():
        if not raw_line.strip():
            continue
        try:
            rows.append(json.loads(raw_line.decode("utf-8")))
        except Exception:
            continue
    return rows, offset + complete_len


def wait_for_stable_file(path: Path, *, timeout_seconds: float = 2.0, quiet_seconds: float = 0.25) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_size = -1
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except OSError:
            return
        now = time.monotonic()
        if size != last_size:
            last_size = size
            stable_since = now
        elif now - stable_since >= quiet_seconds:
            return
        time.sleep(0.05)


def text_from_content(content: Any) -> str:
    """Flatten a list-of-parts content into plain text. Skips tool_use/tool_result
    parts — those are emitted as separate events by row_to_events()."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                # tool_use / tool_result are handled at the event level, not inlined here.
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content) if content is not None else ""


def row_to_events(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand one raw jsonl row into 0+ structured events.

    Anthropic message content is a list of typed parts; we emit one event per
    semantically distinct part so the transcript carries TOOL CALL / TOOL OUTPUT
    sections instead of burying tool calls inside an assistant text block.
    """
    row_type = row.get("type")
    if row_type not in ("user", "assistant"):
        return []
    if row_type == "user" and row.get("isMeta"):
        return []
    timestamp = str(row.get("timestamp") or now_utc())
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else row.get("content")

    if not isinstance(content, list):
        text = text_from_content(content)
        if not text:
            return []
        return [{"timestamp": timestamp, "kind": "message", "role": row_type, "text": redact(text)}]

    events: list[dict[str, Any]] = []
    text_buffer: list[str] = []

    def _flush_text() -> None:
        if not text_buffer:
            return
        text = "\n".join(part for part in text_buffer if part).strip()
        text_buffer.clear()
        if text:
            events.append(
                {"timestamp": timestamp, "kind": "message", "role": row_type, "text": redact(text)}
            )

    for item in content:
        if not isinstance(item, dict):
            text_buffer.append(str(item))
            continue
        item_type = item.get("type")
        if item_type == "text":
            text_buffer.append(str(item.get("text", "")))
        elif item_type == "tool_use":
            _flush_text()
            events.append(
                {
                    "timestamp": timestamp,
                    "kind": "tool_call",
                    "name": str(item.get("name") or ""),
                    "tool_use_id": str(item.get("id") or ""),
                    "text": redact(json.dumps(item.get("input", {}), ensure_ascii=False)),
                }
            )
        elif item_type == "tool_result":
            _flush_text()
            inner = item.get("content")
            output_text = text_from_content(inner) if not isinstance(inner, str) else inner
            events.append(
                {
                    "timestamp": timestamp,
                    "kind": "tool_output",
                    "tool_use_id": str(item.get("tool_use_id") or ""),
                    "is_error": bool(item.get("is_error")),
                    "text": redact(str(output_text or "")),
                }
            )
        else:
            text_buffer.append(json.dumps(item, ensure_ascii=False))

    _flush_text()
    return events


def markdown_path(output_root: Path, session_id: str) -> Path:
    return output_root / "claude-logs" / session_id / "transcript.md"


def ensure_markdown(path: Path, *, session_id: str, source_path: Path, hook_input: dict[str, Any]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    started_at = hook_input.get("timestamp") or now_utc()
    cwd = hook_input.get("cwd")
    path.write_text(
        "---\n"
        f'session_id: "{session_id}"\n'
        f'source_path: "{source_path}"\n'
        f'started_at: "{started_at}"\n'
        'agent: "claude-code"\n'
        f'cwd: "{cwd or ""}"\n'
        "tags:\n"
        '  - "claude-code-live-log"\n'
        "---\n\n"
        f"# Claude Code Live Log - {session_id}\n\n"
        "> Append-only refined log. Existing sections are not rewritten.\n\n",
        encoding="utf-8",
    )


def append_events(markdown: Path, event_jsonl: Path, *, session_id: str, source_path: Path, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    event_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with markdown.open("a", encoding="utf-8") as md, event_jsonl.open("a", encoding="utf-8") as js:
        for event in events:
            event = {"schema_version": 1, "session_id": session_id, "source_path": str(source_path), **event}
            js.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            kind = event["kind"]
            timestamp = event["timestamp"]
            if kind == "message":
                md.write(f"## {timestamp} - {str(event.get('role', '')).upper()}\n\n")
                md.write("```text\n")
                md.write(str(event.get("text", ""))[:12000])
                md.write("\n```\n\n")
            elif kind == "tool_call":
                tool_use_id = str(event.get("tool_use_id") or "")
                md.write(f"## {timestamp} - TOOL CALL `{event.get('name', '')}`\n\n")
                if tool_use_id:
                    md.write(f"- tool_use_id: `{tool_use_id}`\n\n")
                md.write("```json\n")
                md.write(str(event.get("text", ""))[:12000])
                md.write("\n```\n\n")
            elif kind == "tool_output":
                tool_use_id = str(event.get("tool_use_id") or "")
                identifier = tool_use_id or "result"
                md.write(f"## {timestamp} - TOOL OUTPUT `{identifier}`\n\n")
                if tool_use_id:
                    md.write(f"- tool_use_id: `{tool_use_id}`\n")
                if event.get("is_error"):
                    md.write(f"- is_error: `true`\n")
                md.write("\n")
                md.write("```text\n")
                md.write(str(event.get("text", ""))[:12000])
                md.write("\n```\n\n")


def append_from_hook(hook_input: dict[str, Any], output_root: Path, hook_log: Path | None) -> dict[str, Any]:
    transcript = hook_input.get("transcript_path") or hook_input.get("transcriptPath")
    session_id = str(hook_input.get("session_id") or hook_input.get("sessionId") or "")
    event_name = str(hook_input.get("hook_event_name") or hook_input.get("event_name") or "")
    if not transcript or not session_id:
        result = {"logged_at": now_utc(), "event_name": event_name, "session_id": session_id, "appended": False, "reason": "missing_transcript_or_session"}
        write_hook_log(hook_log, result)
        return result
    source_path = Path(str(transcript)).expanduser()
    if not source_path.exists():
        md_path = markdown_path(output_root, session_id)
        ensure_markdown(md_path, session_id=session_id, source_path=source_path, hook_input=hook_input)
        result = {
            "logged_at": now_utc(),
            "event_name": event_name,
            "session_id": session_id,
            "appended": False,
            "reason": "transcript_missing_markdown_initialized",
            "transcript_path": str(source_path),
            "markdown_path": str(md_path),
        }
        write_hook_log(hook_log, result)
        return result
    if event_name == "Stop":
        time.sleep(0.75)
        wait_for_stable_file(source_path)

    state_path = output_root / "state" / "claude_live_append_state.json"
    state = read_json(state_path, {"offsets": {}})
    offsets = state.setdefault("offsets", {})
    key = str(source_path)
    offset = int(offsets.get(key, 0))
    rows, new_offset = parse_complete_jsonl_rows(source_path, offset)
    events = [event for row in rows for event in row_to_events(row)]
    md_path = markdown_path(output_root, session_id)
    ensure_markdown(md_path, session_id=session_id, source_path=source_path, hook_input=hook_input)
    append_events(md_path, output_root / "data" / "claude_live_events.jsonl", session_id=session_id, source_path=source_path, events=events)
    offsets[key] = new_offset
    write_json(state_path, state)
    result = {
        "logged_at": now_utc(),
        "event_name": event_name,
        "session_id": session_id,
        "transcript_path": str(source_path),
        "markdown_path": str(md_path),
        "appended": bool(events),
        "appended_events": len(events),
        "offset": new_offset,
        "reason": "ok",
    }
    write_hook_log(hook_log, result)
    return result


def write_hook_log(path: Path | None, result: dict[str, Any]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append Claude Code hook transcript rows to Obsidian-ready Markdown.")
    parser.add_argument("--from-hook-stdin", action="store_true")
    parser.add_argument("--output-root", type=Path, default=Path.home() / ".codex" / "codex-session-exporter" / "obsidian-output")
    parser.add_argument("--hook-log", type=Path, default=Path.home() / ".claude" / "agent-conversation-logger" / "hook.log.jsonl")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.from_hook_stdin:
        try:
            hook_input = json.loads(sys.stdin.read() or "{}")
            append_from_hook(hook_input, args.output_root, args.hook_log if str(args.hook_log) else None)
        except Exception as exc:
            write_hook_log(args.hook_log, {"logged_at": now_utc(), "appended": False, "reason": "exception", "error": str(exc)})
        return 0
    print("Use --from-hook-stdin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
