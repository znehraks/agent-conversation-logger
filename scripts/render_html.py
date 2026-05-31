#!/usr/bin/env python3
"""Render an agent conversation transcript.md as a self-contained HTML viewer.

Reads the common transcript schema produced by both the Codex exporter and the
Claude Code logger:

    ---
    <YAML frontmatter>
    ---

    # <Title>

    > <optional blockquote>

    ## <ISO8601 ts> - <KIND>[ `<identifier>`]

    [- <key>: <value>]   ← 0+ metadata bullets
    [<blank line>]
    [```<lang>            ← 0/1 fenced code block
    <body>
    ```]

KIND ∈ {USER, ASSISTANT, SYSTEM, TOOL CALL, TOOL OUTPUT}.

Outputs a single HTML file with all CSS/JS inlined — open it in any browser,
no server needed. Messenger-style bubbles for messages; collapsible cards for
tool calls and outputs.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------- parsing ----------

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
SECTION_HEADER_RE = re.compile(
    r"^## (?P<ts>\S+)\s*-\s*(?P<kind>USER|ASSISTANT|SYSTEM|THINKING|TOOL CALL|TOOL OUTPUT|USAGE)"
    r"(?:\s+`(?P<ident>[^`]+)`)?\s*$"
)
BULLET_RE = re.compile(r"^- (?P<key>[^:]+):\s*(?P<value>.*)$")
CODE_FENCE_RE = re.compile(r"^```(?P<lang>\w*)\s*$")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse the YAML-ish frontmatter our loggers emit.

    Handles flat scalars and one level of list items (``  - "value"``).
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fm: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in match.group(1).split("\n"):
        if not line.strip():
            current_list_key = None
            continue
        if line.startswith("  - ") and current_list_key:
            fm.setdefault(current_list_key, []).append(_strip_quotes(line[4:].strip()))
            continue
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if not value:
                fm[key] = []
                current_list_key = key
            else:
                fm[key] = _strip_quotes(value)
                current_list_key = None
    return fm, text[match.end():]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_events(body: str) -> list[dict[str, Any]]:
    """Split the post-frontmatter body into a list of event dicts."""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    code_open = False
    code_lang = ""
    code_lines: list[str] = []
    meta_phase = True  # bullets allowed only before first code block

    for line in body.split("\n"):
        if code_open:
            if line.strip() == "```":
                if current is not None:
                    current.setdefault("blocks", []).append(
                        {"lang": code_lang, "text": "\n".join(code_lines)}
                    )
                code_open = False
                code_lang = ""
                code_lines = []
                meta_phase = False
            else:
                code_lines.append(line)
            continue

        header = SECTION_HEADER_RE.match(line)
        if header:
            if current is not None:
                events.append(current)
            current = {
                "ts": header.group("ts"),
                "kind": header.group("kind"),
                "ident": header.group("ident"),
                "meta": [],
                "blocks": [],
            }
            meta_phase = True
            continue

        if current is None:
            continue  # title / blockquote / blank above the first section

        if meta_phase:
            bullet = BULLET_RE.match(line)
            if bullet:
                current["meta"].append(
                    {"key": bullet.group("key").strip(), "value": bullet.group("value").strip()}
                )
                continue

        fence = CODE_FENCE_RE.match(line)
        if fence:
            code_open = True
            code_lang = fence.group("lang") or "text"
            code_lines = []
            continue
        # ignore other lines (blank lines, stray text)

    if current is not None:
        events.append(current)
    return events


# ---------- rendering ----------


def render_html_document(frontmatter: dict[str, Any], events: list[dict[str, Any]], source: Path) -> str:
    session_id = frontmatter.get("session_id") or source.stem
    agent_label = frontmatter.get("agent") or _infer_agent_from_tags(frontmatter.get("tags"))
    title = f"{agent_label or 'agent'} · {session_id}"

    stats = _compute_stats(events)
    header_html = _render_header(frontmatter, stats, session_id)
    body_html = _render_event_stream(events)

    return _DOC_TEMPLATE.format(
        title=html.escape(title),
        css=_CSS,
        js=_JS,
        header=header_html,
        body=body_html,
        session_id=html.escape(session_id),
        agent=html.escape(agent_label or ""),
    )


def _infer_agent_from_tags(tags: Any) -> str | None:
    if isinstance(tags, list):
        for tag in tags:
            if "codex" in str(tag).lower():
                return "codex"
            if "claude" in str(tag).lower():
                return "claude-code"
    return None


def _compute_stats(events: list[dict[str, Any]]) -> dict[str, int]:
    stats = {"user": 0, "assistant": 0, "system": 0, "thinking": 0, "tool_call": 0, "tool_output": 0}
    for ev in events:
        k = ev["kind"]
        if k == "USER":
            stats["user"] += 1
        elif k == "ASSISTANT":
            stats["assistant"] += 1
        elif k == "SYSTEM":
            stats["system"] += 1
        elif k == "THINKING":
            stats["thinking"] += 1
        elif k == "TOOL CALL":
            stats["tool_call"] += 1
        elif k == "TOOL OUTPUT":
            stats["tool_output"] += 1
    return stats


def _render_header(frontmatter: dict[str, Any], stats: dict[str, int], session_id: str) -> str:
    rows = []
    for key in ("session_id", "agent", "started_at", "cwd", "source_path"):
        if key in frontmatter and frontmatter[key]:
            rows.append(
                f'<dt>{html.escape(key)}</dt><dd>{html.escape(str(frontmatter[key]))}</dd>'
            )
    if isinstance(frontmatter.get("tags"), list) and frontmatter["tags"]:
        tag_html = " ".join(
            f'<span class="tag">{html.escape(str(t))}</span>' for t in frontmatter["tags"]
        )
        rows.append(f'<dt>tags</dt><dd>{tag_html}</dd>')

    chip = lambda label, value: (
        f'<span class="chip"><span class="chip-num">{value}</span> {html.escape(label)}</span>'
    )
    chips = [
        chip("user", stats["user"]),
        chip("assistant", stats["assistant"]),
        chip("tool calls", stats["tool_call"]),
        chip("tool outputs", stats["tool_output"]),
    ]
    if stats.get("thinking"):
        chips.append(chip("thinking", stats["thinking"]))
    stat_html = "".join(chips)

    return f"""
<header class="session-header">
  <div class="session-title">
    <h1>{html.escape(session_id)}</h1>
    <div class="stats">{stat_html}</div>
  </div>
  <dl class="session-meta">
    {''.join(rows)}
  </dl>
  <div class="toolbar">
    <label class="search">
      <input id="search" type="search" placeholder="Filter messages and tool events…" />
    </label>
    <button id="toggle-tools" type="button" data-state="closed">Expand all tools</button>
    <button id="copy-link" type="button">Copy link</button>
  </div>
</header>
"""


def _render_event_stream(events: list[dict[str, Any]]) -> str:
    out: list[str] = []
    last_date: str | None = None
    for ev in events:
        ts = ev.get("ts") or ""
        date_part = ts[:10] if len(ts) >= 10 else ""
        if date_part and date_part != last_date:
            out.append(f'<div class="day-divider"><span>{html.escape(date_part)}</span></div>')
            last_date = date_part
        out.append(_render_one(ev))
    return "\n".join(out)


def _render_one(ev: dict[str, Any]) -> str:
    kind = ev["kind"]
    time_part = _format_time(ev.get("ts") or "")
    safe_ts = html.escape(ev.get("ts") or "")
    if kind in ("USER", "ASSISTANT", "SYSTEM"):
        role = kind.lower()
        text = ev["blocks"][0]["text"] if ev.get("blocks") else ""
        bubble_class = f"message {role}"
        rendered_text = _render_message_text(text)
        return f"""
<div class="event {bubble_class}" data-kind="{role}" data-ts="{safe_ts}">
  <div class="bubble">{rendered_text}</div>
  <div class="ts">{html.escape(time_part)}</div>
</div>"""

    if kind == "THINKING":
        text = ev["blocks"][0]["text"] if ev.get("blocks") else ""
        return f"""
<div class="event thinking-event" data-kind="thinking" data-ts="{safe_ts}">
  <details>
    <summary>
      <span class="caret"></span>
      <span class="icon">💭</span>
      <span class="label">Thinking</span>
      <span class="ts">{html.escape(time_part)}</span>
    </summary>
    <div class="thinking-body"><pre class="codeblock"><code>{html.escape(text)}</code></pre></div>
  </details>
</div>"""

    if kind == "USAGE":
        label = {"in": "in", "out": "out", "cache_read": "cache", "cache_write": "cache+", "reasoning": "reason", "total": "total"}
        vals = {m["key"]: m["value"].strip("` ") for m in (ev.get("meta") or [])}
        chips = []
        for k in ("in", "out", "cache_read", "cache_write", "reasoning", "total"):
            if k in vals:
                try:
                    chips.append(f"{label[k]} {int(vals[k]):,}")
                except ValueError:
                    chips.append(f"{label[k]} {vals[k]}")
        text = " · ".join(chips)
        return f'<div class="event usage-line" data-kind="usage" data-ts="{safe_ts}"><span>🪙 {html.escape(text)}</span></div>'

    if kind in ("TOOL CALL", "TOOL OUTPUT"):
        is_output = kind == "TOOL OUTPUT"
        css_kind = "tool-output" if is_output else "tool-call"
        icon = "▼" if is_output else "▶"
        title = "Tool Output" if is_output else "Tool Call"
        ident = ev.get("ident") or ""
        meta_html = _render_meta(ev.get("meta") or [])
        blocks_html = _render_blocks(ev.get("blocks") or [])
        return f"""
<div class="event tool-event {css_kind}" data-kind="{css_kind}" data-ts="{safe_ts}">
  <details>
    <summary>
      <span class="caret"></span>
      <span class="icon">{icon}</span>
      <span class="label">{title}</span>
      <span class="ident">{html.escape(ident)}</span>
      <span class="ts">{html.escape(time_part)}</span>
    </summary>
    <div class="tool-body">
      {meta_html}
      {blocks_html}
    </div>
  </details>
</div>"""

    return ""


def _format_time(ts: str) -> str:
    if len(ts) >= 19:
        return ts[11:19]
    return ts


def _render_message_text(text: str) -> str:
    # Preserve newlines; escape HTML.
    return html.escape(text).replace("\n", "<br>")


def _render_meta(meta: list[dict[str, str]]) -> str:
    if not meta:
        return ""
    rows = []
    for entry in meta:
        rows.append(
            f"<div class=\"meta-row\"><span class=\"meta-key\">{html.escape(entry['key'])}</span>"
            f"<span class=\"meta-value\">{html.escape(entry['value'])}</span></div>"
        )
    return f'<div class="meta">{"".join(rows)}</div>'


def _render_blocks(blocks: list[dict[str, str]]) -> str:
    if not blocks:
        return ""
    parts = []
    for block in blocks:
        lang = block.get("lang") or "text"
        text = block.get("text") or ""
        if lang == "json":
            text = _pretty_json(text)
        parts.append(
            f'<pre class="codeblock" data-lang="{html.escape(lang)}">'
            f'<code>{html.escape(text)}</code></pre>'
        )
    return "".join(parts)


def _pretty_json(text: str) -> str:
    try:
        data = json.loads(text)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return text


# ---------- styles & scripts ----------

_CSS = """
:root {
  --bg: #f2f3f5;
  --surface: #ffffff;
  --text: #1a1a1a;
  --muted: #6e7480;
  --border: #e3e5e8;
  --user-bg: #007aff;
  --user-fg: #ffffff;
  --assistant-bg: #ffffff;
  --assistant-fg: #1a1a1a;
  --system-bg: #fff8e1;
  --system-fg: #5d4037;
  --tool-bg: #f0f4ff;
  --tool-border: #c5cae9;
  --tool-fg: #1a237e;
  --output-bg: #ecf7ed;
  --output-border: #c8e6c9;
  --output-fg: #1b5e20;
  --code-bg: #1d1f24;
  --code-fg: #e8e8e8;
  --chip-bg: #eef0f3;
  --chip-fg: #404654;
  --accent: #007aff;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181c;
    --surface: #1f2227;
    --text: #ecedee;
    --muted: #9aa0a6;
    --border: #2a2d33;
    --user-bg: #0a84ff;
    --user-fg: #ffffff;
    --assistant-bg: #2a2d33;
    --assistant-fg: #ecedee;
    --system-bg: #3a2f15;
    --system-fg: #ffd591;
    --tool-bg: #1f2640;
    --tool-border: #344675;
    --tool-fg: #adb6ff;
    --output-bg: #16321f;
    --output-border: #2e6f3b;
    --output-fg: #b4eabd;
    --code-bg: #0f1115;
    --code-fg: #e8e8e8;
    --chip-bg: #2a2d33;
    --chip-fg: #c0c4cc;
    --accent: #4dabff;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", system-ui, "Noto Sans KR", sans-serif;
  font-size: 15px;
  line-height: 1.5;
}
.container {
  max-width: 880px;
  margin: 0 auto;
  padding: 24px 16px 96px;
}
.session-header {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px 20px;
  margin-bottom: 28px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.session-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 12px 16px;
  margin-bottom: 12px;
}
.session-title h1 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  word-break: break-all;
}
.stats { display: flex; gap: 6px; flex-wrap: wrap; }
.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--chip-bg);
  color: var(--chip-fg);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  font-weight: 500;
}
.chip-num { font-weight: 700; }
.session-meta {
  margin: 0;
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 14px;
  font-size: 13px;
}
.session-meta dt {
  color: var(--muted);
  font-weight: 500;
}
.session-meta dd {
  margin: 0;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  word-break: break-all;
}
.tag {
  display: inline-block;
  background: var(--chip-bg);
  color: var(--chip-fg);
  border-radius: 6px;
  padding: 1px 8px;
  margin-right: 4px;
  font-size: 11px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.toolbar input[type=search] {
  flex: 1 1 220px;
  min-width: 160px;
  padding: 7px 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
}
.toolbar input[type=search]:focus {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.toolbar button {
  padding: 7px 14px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.toolbar button:hover { background: var(--chip-bg); }
.day-divider {
  text-align: center;
  margin: 28px 0 16px;
  position: relative;
}
.day-divider span {
  display: inline-block;
  background: var(--chip-bg);
  color: var(--muted);
  font-size: 12px;
  padding: 3px 12px;
  border-radius: 999px;
}
.event { margin: 6px 0; }
.event.hidden { display: none; }
.message {
  display: flex;
  flex-direction: column;
  margin: 8px 0;
}
.message.user { align-items: flex-end; }
.message.assistant, .message.system { align-items: flex-start; }
.bubble {
  max-width: 75%;
  padding: 10px 14px;
  border-radius: 18px;
  font-size: 15px;
  line-height: 1.5;
  word-wrap: break-word;
  white-space: pre-wrap;
  box-shadow: 0 1px 1px rgba(0,0,0,0.04);
}
.message.user .bubble {
  background: var(--user-bg);
  color: var(--user-fg);
  border-bottom-right-radius: 5px;
}
.message.assistant .bubble {
  background: var(--assistant-bg);
  color: var(--assistant-fg);
  border: 1px solid var(--border);
  border-bottom-left-radius: 5px;
}
.message.system .bubble {
  background: var(--system-bg);
  color: var(--system-fg);
  font-size: 13px;
  border-radius: 10px;
  max-width: 100%;
}
.message .ts {
  font-size: 11px;
  color: var(--muted);
  margin: 2px 6px 0;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.message.user .ts { text-align: right; }
.thinking-event { margin: 10px 0; }
.thinking-event details {
  background: transparent;
  border: 1px dashed var(--border);
  border-radius: 10px;
  overflow: hidden;
  opacity: 0.85;
}
.thinking-event summary {
  cursor: pointer;
  list-style: none;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  user-select: none;
}
.thinking-event summary::-webkit-details-marker { display: none; }
.thinking-event .label { font-weight: 500; }
.thinking-event .icon { opacity: 0.7; }
.thinking-event .ts {
  margin-left: auto;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.thinking-event .thinking-body { border-top: 1px dashed var(--border); }
.thinking-event .codeblock { background: var(--bg); color: var(--muted); border-top: none; font-style: italic; }
.tool-event { margin: 12px 0; }
.tool-event details {
  background: var(--tool-bg);
  border: 1px solid var(--tool-border);
  border-radius: 12px;
  overflow: hidden;
}
.tool-event.tool-output details {
  background: var(--output-bg);
  border-color: var(--output-border);
}
.tool-event summary {
  cursor: pointer;
  list-style: none;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  user-select: none;
}
.tool-event summary::-webkit-details-marker { display: none; }
.caret {
  display: inline-block;
  width: 0;
  height: 0;
  border-left: 5px solid currentColor;
  border-top: 4px solid transparent;
  border-bottom: 4px solid transparent;
  margin-right: 2px;
  transition: transform 0.15s ease;
  opacity: 0.6;
}
.tool-event details[open] .caret { transform: rotate(90deg); }
.tool-event .icon { font-weight: 600; opacity: 0.65; }
.tool-event .label { font-weight: 600; color: var(--tool-fg); }
.tool-event.tool-output .label { color: var(--output-fg); }
.tool-event .ident {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 12px;
  background: rgba(255,255,255,0.45);
  padding: 1px 8px;
  border-radius: 999px;
  color: var(--text);
}
@media (prefers-color-scheme: dark) {
  .tool-event .ident { background: rgba(255,255,255,0.08); }
}
.tool-event .ts {
  margin-left: auto;
  font-size: 11px;
  color: var(--muted);
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.tool-body {
  border-top: 1px solid var(--tool-border);
  background: var(--surface);
}
.tool-event.tool-output .tool-body { border-top-color: var(--output-border); }
.meta {
  padding: 10px 14px;
  font-size: 12px;
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 12px;
}
.meta-key {
  color: var(--muted);
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.meta-value {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  word-break: break-all;
}
.codeblock {
  margin: 0;
  padding: 12px 14px;
  background: var(--code-bg);
  color: var(--code-fg);
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 12px;
  line-height: 1.55;
  overflow-x: auto;
  border-top: 1px solid var(--border);
}
.codeblock code {
  white-space: pre;
  font: inherit;
}
.empty {
  padding: 32px;
  text-align: center;
  color: var(--muted);
}
.usage-line { text-align: center; margin: 4px 0; }
.usage-line span {
  display: inline-block;
  background: var(--chip-bg);
  color: var(--muted);
  font-size: 11px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  padding: 2px 10px;
  border-radius: 999px;
}
"""

_JS = """
(function () {
  const events = Array.from(document.querySelectorAll('.event'));
  const search = document.getElementById('search');
  const toggleBtn = document.getElementById('toggle-tools');
  const copyBtn = document.getElementById('copy-link');

  if (search) {
    search.addEventListener('input', () => {
      const q = search.value.trim().toLowerCase();
      events.forEach((el) => {
        if (!q) { el.classList.remove('hidden'); return; }
        const text = el.innerText.toLowerCase();
        el.classList.toggle('hidden', !text.includes(q));
      });
    });
  }

  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const open = toggleBtn.dataset.state !== 'open';
      document.querySelectorAll('.tool-event details').forEach((d) => { d.open = open; });
      toggleBtn.dataset.state = open ? 'open' : 'closed';
      toggleBtn.textContent = open ? 'Collapse all tools' : 'Expand all tools';
    });
  }

  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(window.location.href);
        copyBtn.textContent = 'Copied!';
        setTimeout(() => { copyBtn.textContent = 'Copy link'; }, 1500);
      } catch (e) {
        copyBtn.textContent = 'Copy failed';
      }
    });
  }
})();
"""

_DOC_TEMPLATE = """<!doctype html>
<html lang="ko" data-session="{session_id}" data-agent="{agent}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
{header}
<main id="stream">
{body}
</main>
</div>
<script>{js}</script>
</body>
</html>
"""


# ---------- CLI ----------


def render_file(input_path: Path, output_path: Path) -> dict[str, Any]:
    text = input_path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    events = parse_events(body)
    html_out = render_html_document(frontmatter, events, input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_out, encoding="utf-8")
    return {
        "input": str(input_path),
        "output": str(output_path),
        "event_count": len(events),
        "bytes": len(html_out),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render transcript.md into messenger-style HTML viewer."
    )
    parser.add_argument("input", type=Path, help="Path to transcript.md, or a directory containing transcript.md files.")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output HTML path (single-file mode only).")
    parser.add_argument("--recursive", action="store_true", help="When INPUT is a directory, render every transcript.md inside it.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    input_path = args.input
    if not input_path.exists():
        print(f"error: {input_path} does not exist", file=sys.stderr)
        return 2

    if input_path.is_file():
        output_path = args.output or input_path.with_suffix(".html")
        result = render_file(input_path, output_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if input_path.is_dir():
        if not args.recursive:
            print("error: input is a directory; pass --recursive to render every transcript.md inside.", file=sys.stderr)
            return 2
        results = []
        for path in sorted(input_path.rglob("transcript.md")):
            results.append(render_file(path, path.with_suffix(".html")))
        print(json.dumps({"rendered": len(results), "files": results}, ensure_ascii=False, indent=2))
        return 0

    print(f"error: {input_path} is neither a file nor a directory", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
