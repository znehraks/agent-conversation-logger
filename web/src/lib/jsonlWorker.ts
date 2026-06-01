// Web Worker that parses raw agent JSONL into Ev[] off the main thread.
//
// Why a worker:
//   - Codex sessions can exceed 1 GB. Parsing on the main thread freezes the UI.
//
// Why streaming inside the worker:
//   - `file.stream().pipeThrough(new TextDecoderStream())` reads chunk by chunk,
//     so we never hold the entire decoded text in memory (which would otherwise
//     mean ~2x the file size in JS strings).
//
// Message protocol:
//   main → worker:  { id, file }
//   worker → main:  { id, type: "progress", rows, events, bytes, total, format }
//                   { id, type: "done", events, fm, format, rows, bytes }
//                   { id, type: "error", message }
//
// Cancellation: terminate the worker on the main side.

import type { Ev, Frontmatter } from "./parse";
import { redact } from "./raw_jsonl";

// ----- detection -----

type Format = "claude-jsonl" | "codex-jsonl" | "unknown";

function detectOneRow(obj: any): Format {
  if (!obj || typeof obj !== "object") return "unknown";
  if (obj.payload && typeof obj.payload === "object" && typeof obj.type === "string") return "codex-jsonl";
  if (typeof obj.type === "string") {
    if (obj.sessionId || obj.session_id || obj.parentUuid || obj.uuid) return "claude-jsonl";
    if (["user", "assistant", "summary", "system", "attachment", "mode",
         "permission-mode", "last-prompt", "file-history-snapshot",
         "queue-operation", "ai-title"].includes(obj.type)) return "claude-jsonl";
  }
  return "unknown";
}

// ----- shared event helpers -----

function ev(opts: {
  ts: string;
  kind: Ev["kind"];
  ident?: string | null;
  meta?: { key: string; value: string }[];
  blocks?: { lang: string; text: string }[];
}): Ev {
  return {
    ts: opts.ts,
    kind: opts.kind,
    ident: opts.ident ?? null,
    meta: opts.meta ?? [],
    blocks: opts.blocks ?? [],
  };
}

const USAGE_ORDER = ["in", "out", "cache_read", "cache_write", "reasoning", "total"] as const;
function usageMeta(u: Record<string, number | undefined>): { key: string; value: string }[] {
  const meta: { key: string; value: string }[] = [];
  for (const k of USAGE_ORDER) {
    const v = u[k];
    if (typeof v === "number" && v) meta.push({ key: k, value: `\`${v}\`` });
  }
  return meta;
}

// ----- Claude per-row processing -----

interface ClaudeState {
  events: Ev[];
  callNames: Record<string, string>;
  sessionId: string;
  cwd: string;
  startedAt: string;
}

function newClaudeState(): ClaudeState {
  return { events: [], callNames: {}, sessionId: "claude-session", cwd: "", startedAt: "" };
}

function extractClaudeUsage(message: any): Record<string, number> | null {
  if (!message || typeof message !== "object") return null;
  const u = message.usage;
  if (!u || typeof u !== "object") return null;
  const g = (k: string) => (typeof u[k] === "number" ? u[k] : 0);
  const inp = g("input_tokens"), out = g("output_tokens");
  const cache_read = g("cache_read_input_tokens"), cache_write = g("cache_creation_input_tokens");
  if (!inp && !out && !cache_read && !cache_write) return null;
  return { in: inp, out, cache_read, cache_write, total: inp + out + cache_read + cache_write };
}

function textFromClaudeContent(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    let acc = "";
    for (let i = 0; i < content.length; i++) {
      const item = content[i];
      if (item && typeof item === "object") {
        if (item.type === "text" && typeof item.text === "string") {
          if (acc) acc += "\n";
          acc += item.text;
        }
      } else if (item != null) {
        if (acc) acc += "\n";
        acc += String(item);
      }
    }
    return acc;
  }
  if (content && typeof content === "object") return JSON.stringify(content);
  return content == null ? "" : String(content);
}

function processClaudeRow(row: any, s: ClaudeState): void {
  if (!row || typeof row !== "object") return;
  if (!s.startedAt && typeof row.timestamp === "string") s.startedAt = row.timestamp;
  if (!s.cwd && typeof row.cwd === "string") s.cwd = row.cwd;
  if (s.sessionId === "claude-session") {
    const sid = row.sessionId || row.session_id;
    if (sid) s.sessionId = String(sid);
  }

  const rowType = row.type;
  if (rowType !== "user" && rowType !== "assistant") return;
  if (rowType === "user" && row.isMeta) return;

  const ts = String(row.timestamp || "");
  const message = (row.message && typeof row.message === "object") ? row.message : null;
  const content = message ? message.content : row.content;
  const usage = rowType === "assistant" ? extractClaudeUsage(message) : null;
  const role: Ev["kind"] = rowType === "user" ? "USER" : "ASSISTANT";

  if (!Array.isArray(content)) {
    const text = textFromClaudeContent(content);
    if (text) s.events.push(ev({ ts, kind: role, blocks: [{ lang: "text", text: redact(text) }] }));
    if (usage) s.events.push(ev({ ts, kind: "USAGE", meta: usageMeta(usage) }));
    return;
  }

  let textBuf = "";
  const flushText = () => {
    const t = textBuf.trim();
    textBuf = "";
    if (t) s.events.push(ev({ ts, kind: role, blocks: [{ lang: "text", text: redact(t) }] }));
  };

  for (let i = 0; i < content.length; i++) {
    const item = content[i];
    if (!item || typeof item !== "object") {
      if (item != null) { if (textBuf) textBuf += "\n"; textBuf += String(item); }
      continue;
    }
    switch (item.type) {
      case "text": {
        const t = item.text;
        if (typeof t === "string" && t) { if (textBuf) textBuf += "\n"; textBuf += t; }
        break;
      }
      case "thinking": {
        flushText();
        const tt = (item.thinking ? String(item.thinking) : "").trim();
        if (tt) s.events.push(ev({ ts, kind: "THINKING", blocks: [{ lang: "text", text: redact(tt) }] }));
        break;
      }
      case "tool_use": {
        flushText();
        const name = String(item.name || "");
        const callId = String(item.id || "");
        if (callId && name) s.callNames[callId] = name;
        const inputJson = JSON.stringify(item.input ?? {});
        s.events.push(ev({
          ts, kind: "TOOL CALL", ident: name || null,
          meta: callId ? [{ key: "call_id", value: `\`${callId}\`` }] : [],
          blocks: [{ lang: "json", text: redact(inputJson) }],
        }));
        break;
      }
      case "tool_result": {
        flushText();
        const callId = String(item.tool_use_id || "");
        const inner = item.content;
        const outputText = typeof inner === "string" ? inner : textFromClaudeContent(inner);
        const name = callId ? s.callNames[callId] : "";
        const meta: { key: string; value: string }[] = [];
        if (callId) meta.push({ key: "call_id", value: `\`${callId}\`` });
        if (name) meta.push({ key: "tool_name", value: `\`${name}\`` });
        if (item.is_error) meta.push({ key: "is_error", value: "`true`" });
        s.events.push(ev({
          ts, kind: "TOOL OUTPUT",
          ident: name ? `${name} (${callId})` : (callId || null),
          meta,
          blocks: [{ lang: "text", text: redact(String(outputText ?? "")) }],
        }));
        break;
      }
    }
  }
  flushText();
  if (usage) s.events.push(ev({ ts, kind: "USAGE", meta: usageMeta(usage) }));
}

function claudeFm(s: ClaudeState): Frontmatter {
  return {
    agent: "claude-code",
    session_id: s.sessionId,
    started_at: s.startedAt,
    cwd: s.cwd,
    source_path: "",
    tags: ["claude-code-live-log"],
  };
}

// ----- Codex per-row processing -----

interface CodexState {
  events: Ev[];
  callNames: Record<string, string>;
  pendingOutputIndices: Record<string, number[]>;
  sessionId: string;
  cwd: string;
  startedAt: string;
  latestTotal: any;
  latestUsageTs: string;
}

function newCodexState(): CodexState {
  return {
    events: [], callNames: {}, pendingOutputIndices: {},
    sessionId: "codex-session", cwd: "", startedAt: "",
    latestTotal: null, latestUsageTs: "",
  };
}

function extractCodexText(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    let acc = "";
    for (let i = 0; i < content.length; i++) {
      const item = content[i];
      if (item && typeof item === "object") {
        const t = item.text ?? item.input_text ?? item.output_text;
        if (typeof t === "string") { if (acc) acc += "\n"; acc += t; }
      } else if (item != null) { if (acc) acc += "\n"; acc += String(item); }
    }
    return acc;
  }
  return content == null ? "" : String(content);
}

function extractExitCode(output: string): number | null {
  const m = /Process exited with code (-?\d+)/.exec(output);
  return m ? parseInt(m[1], 10) : null;
}

function codexUsageFromTotal(total: any): Record<string, number> | null {
  if (!total || typeof total !== "object") return null;
  const g = (k: string) => {
    const v = total[k];
    return typeof v === "number" ? v : 0;
  };
  const u = {
    in: g("input_tokens"),
    out: g("output_tokens"),
    cache_read: g("cached_input_tokens"),
    reasoning: g("reasoning_output_tokens"),
    total: g("total_tokens"),
  };
  if (!u.in && !u.out && !u.cache_read && !u.reasoning && !u.total) return null;
  return u;
}

function processCodexRow(row: any, s: CodexState): void {
  if (!row || typeof row !== "object") return;
  const rowType = row.type;
  const payload = row.payload || {};
  const ts = String(row.timestamp || "");

  if (rowType === "session_meta") {
    if (payload.id) s.sessionId = String(payload.id);
    if (!s.startedAt) s.startedAt = String(payload.timestamp || ts || "");
    if (!s.cwd && payload.cwd) s.cwd = String(payload.cwd);
    return;
  }
  if (rowType === "turn_context") {
    if (!s.cwd && payload.cwd) s.cwd = String(payload.cwd);
    return;
  }
  if (rowType === "event_msg" && payload && payload.type === "token_count") {
    const total = payload.info?.total_token_usage;
    if (total) { s.latestTotal = total; s.latestUsageTs = ts || s.latestUsageTs; }
    return;
  }
  if (rowType !== "response_item") return;

  if (payload.type === "message") {
    const role = String(payload.role || "unknown");
    if (role === "developer" || role === "system") return;
    const text = extractCodexText(payload.content);
    if (!text || text.indexOf("<environment_context>") >= 0 || text.indexOf("<user_instructions>") >= 0) return;
    const kind: Ev["kind"] = role === "user" ? "USER" : role === "assistant" ? "ASSISTANT" : "SYSTEM";
    s.events.push(ev({ ts, kind, blocks: [{ lang: "text", text: redact(text) }] }));
  } else if (payload.type === "function_call" || payload.type === "tool_search_call") {
    const argsRaw = payload.arguments;
    let argsObj: any = argsRaw;
    if (typeof argsRaw === "string") {
      try { argsObj = JSON.parse(argsRaw); } catch { argsObj = argsRaw; }
    }
    const command = argsObj && typeof argsObj === "object" ? argsObj.cmd : null;
    const argsText = typeof argsObj === "object" && argsObj !== null
      ? JSON.stringify(argsObj)
      : String(argsRaw ?? "");
    const name = String(payload.name || (payload.type === "tool_search_call" ? "tool_search" : "tool"));
    const callId = String(payload.call_id || "");
    if (callId && name) {
      s.callNames[callId] = name;
      const pending = s.pendingOutputIndices[callId];
      if (pending) {
        for (const idx of pending) {
          const t = s.events[idx];
          if (!t) continue;
          t.ident = `${name} (${callId})`;
          t.meta.push({ key: "tool_name", value: `\`${name}\`` });
        }
        delete s.pendingOutputIndices[callId];
      }
    }
    const meta: { key: string; value: string }[] = [];
    if (callId) meta.push({ key: "call_id", value: `\`${callId}\`` });
    const blocks = command
      ? [{ lang: "text", text: redact(Array.isArray(command) ? command.join(" ") : String(command)) }]
      : [{ lang: "json", text: redact(argsText) }];
    s.events.push(ev({ ts, kind: "TOOL CALL", ident: name, meta, blocks }));
  } else if (payload.type === "function_call_output" || payload.type === "tool_search_output") {
    const output = String(payload.output || "");
    const callId = String(payload.call_id || "");
    const exit = extractExitCode(output);
    const name = callId ? s.callNames[callId] : "";
    const meta: { key: string; value: string }[] = [];
    if (callId) meta.push({ key: "call_id", value: `\`${callId}\`` });
    if (exit !== null) meta.push({ key: "exit_code", value: `\`${exit}\`` });
    if (exit !== null && exit !== 0) meta.push({ key: "is_error", value: "`true`" });
    if (name) meta.push({ key: "tool_name", value: `\`${name}\`` });
    const evt = ev({
      ts, kind: "TOOL OUTPUT",
      ident: name ? `${name} (${callId})` : (callId || null),
      meta,
      blocks: [{ lang: "text", text: redact(output) }],
    });
    s.events.push(evt);
    if (!name && callId) {
      (s.pendingOutputIndices[callId] ||= []).push(s.events.length - 1);
    }
  }
}

function codexFm(s: CodexState): Frontmatter {
  return {
    agent: "codex",
    session_id: s.sessionId,
    started_at: s.startedAt,
    cwd: s.cwd,
    source_path: "",
    tags: ["codex-live-log"],
  };
}

function finalizeCodex(s: CodexState): void {
  if (s.latestTotal) {
    const u = codexUsageFromTotal(s.latestTotal);
    if (u) s.events.push(ev({ ts: s.latestUsageTs || s.startedAt || "", kind: "USAGE", meta: usageMeta(u) }));
  }
}

// ----- main file driver (streaming) -----

interface ProgressPayload { rows: number; events: number; bytes: number; total: number; format: Format; }

async function parseFile(file: File, post: (p: ProgressPayload) => void, signal?: { aborted: boolean }):
  Promise<{ events: Ev[]; fm: Frontmatter; format: Format; rows: number; bytes: number }> {
  const total = file.size;
  const stream = (file as any).stream
    ? (file as any).stream().pipeThrough(new TextDecoderStream())
    : null;

  // Fallback for environments without File.stream() (very old browsers).
  // Cheap and complete: read whole file as text and split.
  if (!stream) {
    const text = await file.text();
    return parseWholeText(text, total, post, signal);
  }

  const reader = stream.getReader();
  let buf = "";
  let bytes = 0;
  let rows = 0;
  let format: Format = "unknown";
  const earlyRows: any[] = [];
  let detectDone = false;
  const claudeState = newClaudeState();
  const codexState = newCodexState();
  let lastReport = 0;

  const PROGRESS_EVERY_ROWS = 2000;

  try {
    while (true) {
      if (signal?.aborted) throw new Error("aborted");
      const { done, value } = await reader.read();
      if (done) break;
      bytes += (value as string).length;
      buf += value;
      let nl: number;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const lineRaw = buf.substring(0, nl);
        buf = buf.substring(nl + 1);
        const line = lineRaw.trim();
        if (!line || line.charCodeAt(0) !== 123 /* { */) continue;
        let obj: any;
        try { obj = JSON.parse(line); } catch { continue; }
        rows++;

        if (!detectDone) {
          format = detectOneRow(obj);
          earlyRows.push(obj);
          if (format !== "unknown") {
            detectDone = true;
            const handler = format === "claude-jsonl" ? processClaudeRow : processCodexRow;
            const state: any = format === "claude-jsonl" ? claudeState : codexState;
            for (const r of earlyRows) handler(r, state);
            earlyRows.length = 0;
          } else if (earlyRows.length > 30) {
            throw new Error("Could not detect file format (not Claude or Codex jsonl).");
          }
        } else if (format === "claude-jsonl") {
          processClaudeRow(obj, claudeState);
        } else {
          processCodexRow(obj, codexState);
        }

        if (rows - lastReport >= PROGRESS_EVERY_ROWS) {
          lastReport = rows;
          const eventsLen = format === "claude-jsonl" ? claudeState.events.length : codexState.events.length;
          post({ rows, events: eventsLen, bytes, total, format });
        }
      }
    }
    // trailing line w/o newline
    const tail = buf.trim();
    if (tail && tail.charCodeAt(0) === 123) {
      try {
        const obj = JSON.parse(tail);
        rows++;
        if (!detectDone) {
          format = detectOneRow(obj);
          earlyRows.push(obj);
          if (format !== "unknown") {
            detectDone = true;
            const handler = format === "claude-jsonl" ? processClaudeRow : processCodexRow;
            const state: any = format === "claude-jsonl" ? claudeState : codexState;
            for (const r of earlyRows) handler(r, state);
          }
        } else if (format === "claude-jsonl") {
          processClaudeRow(obj, claudeState);
        } else {
          processCodexRow(obj, codexState);
        }
      } catch { /* skip */ }
    }
  } finally {
    try { reader.releaseLock(); } catch {/* */}
  }

  if (format === "unknown") {
    throw new Error("Could not detect file format (not Claude or Codex jsonl).");
  }

  if (format === "codex-jsonl") finalizeCodex(codexState);

  const events = format === "claude-jsonl" ? claudeState.events : codexState.events;
  const fm = format === "claude-jsonl" ? claudeFm(claudeState) : codexFm(codexState);
  post({ rows, events: events.length, bytes, total, format });
  return { events, fm, format, rows, bytes };
}

async function parseWholeText(
  text: string, total: number,
  post: (p: ProgressPayload) => void,
  signal?: { aborted: boolean },
): Promise<{ events: Ev[]; fm: Frontmatter; format: Format; rows: number; bytes: number }> {
  // Fallback path: behaves like the streaming one but over a single string.
  const lines = text.split("\n");
  let rows = 0;
  let format: Format = "unknown";
  const earlyRows: any[] = [];
  let detectDone = false;
  const claudeState = newClaudeState();
  const codexState = newCodexState();
  let lastReport = 0;
  for (let i = 0; i < lines.length; i++) {
    if (signal?.aborted) throw new Error("aborted");
    const line = lines[i].trim();
    if (!line || line.charCodeAt(0) !== 123) continue;
    let obj: any;
    try { obj = JSON.parse(line); } catch { continue; }
    rows++;
    if (!detectDone) {
      format = detectOneRow(obj);
      earlyRows.push(obj);
      if (format !== "unknown") {
        detectDone = true;
        const handler = format === "claude-jsonl" ? processClaudeRow : processCodexRow;
        const state: any = format === "claude-jsonl" ? claudeState : codexState;
        for (const r of earlyRows) handler(r, state);
      } else if (earlyRows.length > 30) {
        throw new Error("Could not detect file format.");
      }
    } else if (format === "claude-jsonl") processClaudeRow(obj, claudeState);
    else processCodexRow(obj, codexState);

    if (rows - lastReport >= 2000) {
      lastReport = rows;
      const len = format === "claude-jsonl" ? claudeState.events.length : codexState.events.length;
      post({ rows, events: len, bytes: 0, total, format });
    }
  }
  if (format === "unknown") throw new Error("Could not detect file format.");
  if (format === "codex-jsonl") finalizeCodex(codexState);
  const events = format === "claude-jsonl" ? claudeState.events : codexState.events;
  const fm = format === "claude-jsonl" ? claudeFm(claudeState) : codexFm(codexState);
  post({ rows, events: events.length, bytes: total, total, format });
  return { events, fm, format, rows, bytes: total };
}

// ----- worker entry -----

const ctx: any = self;
const aborts = new Map<string, { aborted: boolean }>();

ctx.onmessage = async (e: MessageEvent) => {
  const data = e.data || {};
  if (data.type === "cancel") {
    const a = aborts.get(data.id);
    if (a) a.aborted = true;
    return;
  }
  const { id, file, name } = data;
  if (!id || !file) return;
  const signal = { aborted: false };
  aborts.set(id, signal);
  try {
    const result = await parseFile(file as File, (p) => {
      ctx.postMessage({ id, type: "progress", ...p, name });
    }, signal);
    ctx.postMessage({ id, type: "done", name, ...result });
  } catch (err: any) {
    ctx.postMessage({ id, type: "error", name, message: String(err?.message || err) });
  } finally {
    aborts.delete(id);
  }
};
