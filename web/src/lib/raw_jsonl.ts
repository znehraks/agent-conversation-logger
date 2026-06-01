// Raw JSONL → Ev[] (in-browser parsers).
//
// Lets a viewer load the agent's own raw rollout file without first running
// the loggers + render pipeline. We port `row_to_events` (Claude) and
// `row_to_live_event` (Codex) from scripts/, matching the same output schema
// the markdown parser produces (`Ev[]` from ./parse).
//
// Performance: raw jsonl files routinely run 10-100+ MB (Codex sessions can
// exceed 1 GB). We deliberately avoid:
//   - text.split("\n")  → materializes the whole-file line array (2x peak mem)
//   - building an intermediate rows[] array  → another full pass over the data
// Instead, a single forward pass slices lines with indexOf("\n"), JSON.parses
// each, and emits events as it goes. Anthropic / Codex schemas guarantee that
// a tool_use precedes its tool_result, so the call_id → name map can be built
// on the fly without a separate first pass.

import { Ev, Frontmatter } from "./parse";

// ---------- shared helpers ----------

const SECRET_PATTERNS: RegExp[] = [
  /(Authorization:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+/gi,
  /(\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*)[^\s'"`]+/gi,
  /\bsk-(?:proj-)?[A-Za-z0-9_-]{6,}\b/g,
  /\bxox[baprs]-[A-Za-z0-9-]{6,}\b/g,
  /\bgh[pousr]_[A-Za-z0-9_]{12,}\b/g,
];

export function redact(text: string): string {
  // Cheap fast-path: short strings rarely match. Long tool outputs *can*
  // contain Bearer/sk- patterns so we still scan them; but the regex engine
  // is fast for non-matches.
  let out = text;
  for (const re of SECRET_PATTERNS) {
    out = out.replace(re, (_match, prefix) =>
      prefix ? `${prefix}[REDACTED]` : "[REDACTED]"
    );
  }
  return out;
}

/** Iterate JSONL lines from a single in-memory string, in one forward pass. */
export function* iterJsonl(text: string): Generator<any> {
  const n = text.length;
  let start = 0;
  while (start < n) {
    let end = text.indexOf("\n", start);
    if (end < 0) end = n;
    if (end > start) {
      // Trim trailing \r and spaces without allocating a substring twice.
      let lineEnd = end;
      while (lineEnd > start) {
        const c = text.charCodeAt(lineEnd - 1);
        if (c === 13 /* \r */ || c === 32 /* space */ || c === 9 /* tab */) lineEnd--;
        else break;
      }
      let lineStart = start;
      while (lineStart < lineEnd) {
        const c = text.charCodeAt(lineStart);
        if (c === 32 || c === 9) lineStart++;
        else break;
      }
      if (lineStart < lineEnd) {
        const line = text.slice(lineStart, lineEnd);
        // Quick reject for non-JSON lines (the loggers always start with `{`).
        if (line.charCodeAt(0) === 123 /* { */) {
          try {
            yield JSON.parse(line);
          } catch {
            /* skip malformed line */
          }
        }
      }
    }
    start = end + 1;
  }
}

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

// ---------- detection ----------

export type RawFormat = "claude-jsonl" | "codex-jsonl" | "unknown";

export function detectRawJsonl(text: string): RawFormat {
  // Look at first ~30 non-blank lines only (cheap on huge files).
  let inspected = 0;
  for (const obj of iterJsonl(text)) {
    if (!obj || typeof obj !== "object") continue;
    if (obj.payload && typeof obj.payload === "object" && typeof obj.type === "string") {
      return "codex-jsonl";
    }
    if (typeof obj.type === "string") {
      // Claude session jsonl has user/assistant/summary/system/etc at top level.
      // Any of those, or an early `sessionId` field, is a strong signal.
      if (obj.sessionId || obj.session_id || obj.parentUuid || obj.uuid) return "claude-jsonl";
      if (["user", "assistant", "summary", "system", "attachment", "mode",
           "permission-mode", "last-prompt", "file-history-snapshot",
           "queue-operation", "ai-title"].includes(obj.type)) {
        return "claude-jsonl";
      }
    }
    if (++inspected >= 30) break;
  }
  return "unknown";
}

// ---------- Claude raw → Ev[] (single-pass) ----------

interface ClaudeUsage {
  in: number; out: number;
  cache_read: number; cache_write: number;
  total: number;
}

function extractClaudeUsage(message: any): ClaudeUsage | null {
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
        // tool_use/tool_result emitted as separate events; skip here.
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

export interface ParseProgress { rows: number; events: number; bytes: number; }
export type ProgressCb = (p: ParseProgress) => void;

interface ParseOpts { onProgress?: ProgressCb; progressEvery?: number; }

export function parseClaudeJsonl(text: string, opts: ParseOpts = {}): { events: Ev[]; fm: Frontmatter } {
  const { onProgress, progressEvery = 5000 } = opts;
  const events: Ev[] = [];
  const callNames: Record<string, string> = {};
  let sessionId = "claude-session";
  let cwd = "";
  let startedAt = "";
  let rowCount = 0;
  let lastProgressAt = 0;

  for (const row of iterJsonl(text)) {
    rowCount++;
    if (!row || typeof row !== "object") continue;

    if (!startedAt && typeof row.timestamp === "string") startedAt = row.timestamp;
    if (!cwd && typeof row.cwd === "string") cwd = row.cwd;
    if (sessionId === "claude-session") {
      const sid = row.sessionId || row.session_id;
      if (sid) sessionId = String(sid);
    }

    const rowType = row.type;
    if (rowType !== "user" && rowType !== "assistant") continue;
    if (rowType === "user" && row.isMeta) continue;

    const ts = String(row.timestamp || "");
    const message = (row.message && typeof row.message === "object") ? row.message : null;
    const content = message ? message.content : row.content;
    const usage = rowType === "assistant" ? extractClaudeUsage(message) : null;
    const role = rowType === "user" ? "USER" : "ASSISTANT";

    if (!Array.isArray(content)) {
      const text = textFromClaudeContent(content);
      if (text) {
        events.push(ev({
          ts, kind: role,
          blocks: [{ lang: "text", text: redact(text) }],
        }));
      }
      if (usage) events.push(ev({ ts, kind: "USAGE", meta: usageMeta(usage as any) }));
    } else {
      let textBuf = "";
      const flushText = () => {
        const t = textBuf.trim();
        textBuf = "";
        if (t) {
          events.push(ev({
            ts, kind: role,
            blocks: [{ lang: "text", text: redact(t) }],
          }));
        }
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
            if (typeof t === "string" && t) {
              if (textBuf) textBuf += "\n";
              textBuf += t;
            }
            break;
          }
          case "thinking": {
            flushText();
            const tt = (item.thinking ? String(item.thinking) : "").trim();
            if (tt) {
              events.push(ev({
                ts, kind: "THINKING",
                blocks: [{ lang: "text", text: redact(tt) }],
              }));
            }
            break;
          }
          case "tool_use": {
            flushText();
            const name = String(item.name || "");
            const callId = String(item.id || "");
            if (callId && name) callNames[callId] = name;
            const inputJson = JSON.stringify(item.input ?? {});
            events.push(ev({
              ts, kind: "TOOL CALL",
              ident: name || null,
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
            const name = callId ? callNames[callId] : "";
            const meta: { key: string; value: string }[] = [];
            if (callId) meta.push({ key: "call_id", value: `\`${callId}\`` });
            if (name) meta.push({ key: "tool_name", value: `\`${name}\`` });
            if (item.is_error) meta.push({ key: "is_error", value: "`true`" });
            events.push(ev({
              ts, kind: "TOOL OUTPUT",
              ident: name ? `${name} (${callId})` : (callId || null),
              meta,
              blocks: [{ lang: "text", text: redact(String(outputText ?? "")) }],
            }));
            break;
          }
          // unknown content parts → silently skip
        }
      }
      flushText();
      if (usage) events.push(ev({ ts, kind: "USAGE", meta: usageMeta(usage as any) }));
    }

    if (onProgress && events.length - lastProgressAt >= progressEvery) {
      lastProgressAt = events.length;
      onProgress({ rows: rowCount, events: events.length, bytes: 0 });
    }
  }

  if (onProgress) onProgress({ rows: rowCount, events: events.length, bytes: text.length });

  const fm: Frontmatter = {
    agent: "claude-code",
    session_id: sessionId,
    started_at: startedAt,
    cwd,
    source_path: "",
    tags: ["claude-code-live-log"],
  };
  return { events, fm };
}

// ---------- Codex raw → Ev[] (single-pass) ----------

function extractCodexText(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    let acc = "";
    for (let i = 0; i < content.length; i++) {
      const item = content[i];
      if (item && typeof item === "object") {
        const t = item.text ?? item.input_text ?? item.output_text;
        if (typeof t === "string") {
          if (acc) acc += "\n";
          acc += t;
        }
      } else if (item != null) {
        if (acc) acc += "\n";
        acc += String(item);
      }
    }
    return acc;
  }
  return content == null ? "" : String(content);
}

function isEnvContext(text: string): boolean {
  return text.indexOf("<environment_context>") >= 0 || text.indexOf("<user_instructions>") >= 0;
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

export function parseCodexJsonl(text: string, opts: ParseOpts = {}): { events: Ev[]; fm: Frontmatter } {
  const { onProgress, progressEvery = 5000 } = opts;
  const events: Ev[] = [];
  const callNames: Record<string, string> = {};
  let sessionId = "codex-session";
  let cwd = "";
  let startedAt = "";
  let latestTotal: any = null;
  let latestUsageTs = "";
  let rowCount = 0;
  let lastProgressAt = 0;
  // We need to back-patch TOOL OUTPUT idents/meta when the call's name shows up
  // before the output (the usual order); we capture the *event index* per call_id
  // so we don't need a separate second pass.
  const pendingOutputIndices: Record<string, number[]> = {};

  for (const row of iterJsonl(text)) {
    rowCount++;
    if (!row || typeof row !== "object") continue;

    const rowType = row.type;
    const payload = row.payload || {};
    const ts = String(row.timestamp || "");

    if (rowType === "session_meta") {
      if (payload.id) sessionId = String(payload.id);
      if (!startedAt) startedAt = String(payload.timestamp || ts || "");
      if (!cwd && payload.cwd) cwd = String(payload.cwd);
      continue;
    }
    if (rowType === "turn_context") {
      if (!cwd && payload.cwd) cwd = String(payload.cwd);
      continue;
    }
    if (rowType === "event_msg" && payload && payload.type === "token_count") {
      const total = payload.info?.total_token_usage;
      if (total) { latestTotal = total; latestUsageTs = ts || latestUsageTs; }
      continue;
    }
    if (rowType === "event_msg" && payload) {
      // MCP tool calls, apply_patch file edits, and web searches surface as
      // event_msg records that bundle invocation + result in one row. We
      // expand each into a TOOL CALL + TOOL OUTPUT pair (web_search has no
      // separate result).
      if (payload.type === "mcp_tool_call_end") {
        const callId = String(payload.call_id || "");
        const invocation = (payload.invocation && typeof payload.invocation === "object") ? payload.invocation : {};
        const server = invocation.server, tool = invocation.tool;
        const name = (server && tool) ? `mcp:${server}/${tool}` : (tool || "mcp_tool");
        const args = invocation.arguments;
        const argsText = args === undefined ? "" : JSON.stringify(args);
        const result = (payload.result && typeof payload.result === "object") ? payload.result : {};
        const ok = "Ok" in result;
        let outputText = "";
        if (ok) {
          const content = (result as any).Ok?.content;
          if (Array.isArray(content)) {
            outputText = content
              .filter((c: any) => c && typeof c === "object" && c.type === "text")
              .map((c: any) => String(c.text || ""))
              .join("\n");
          } else if (content != null) {
            outputText = JSON.stringify(content);
          }
        } else {
          outputText = JSON.stringify(result);
        }
        if (callId && name) callNames[callId] = name;
        events.push(ev({
          ts, kind: "TOOL CALL", ident: name,
          meta: callId ? [{ key: "call_id", value: `\`${callId}\`` }] : [],
          blocks: [{ lang: "json", text: redact(argsText) }],
        }));
        const meta: { key: string; value: string }[] = [];
        if (callId) meta.push({ key: "call_id", value: `\`${callId}\`` });
        meta.push({ key: "exit_code", value: ok ? "`0`" : "`1`" });
        meta.push({ key: "tool_name", value: `\`${name}\`` });
        if (!ok) meta.push({ key: "is_error", value: "`true`" });
        events.push(ev({
          ts, kind: "TOOL OUTPUT",
          ident: `${name} (${callId})`,
          meta,
          blocks: [{ lang: "text", text: redact(outputText) }],
        }));
        continue;
      }
      if (payload.type === "patch_apply_end") {
        const callId = String(payload.call_id || "");
        const changes = (payload.changes && typeof payload.changes === "object") ? payload.changes : {};
        const success = !!payload.success;
        const stdout = String(payload.stdout || "");
        const stderr = String(payload.stderr || "");
        const lines: string[] = [];
        for (const [p, info] of Object.entries(changes)) {
          const k = (info as any)?.type || "?";
          lines.push(`${k}: ${p}`);
        }
        const callText = lines.length ? lines.join("\n") : JSON.stringify(changes);
        const outputText = stderr ? `${stdout}\n--- stderr ---\n${stderr}` : stdout;
        const name = "apply_patch";
        if (callId) callNames[callId] = name;
        events.push(ev({
          ts, kind: "TOOL CALL", ident: name,
          meta: callId ? [{ key: "call_id", value: `\`${callId}\`` }] : [],
          blocks: [{ lang: "text", text: redact(callText) }],
        }));
        const meta: { key: string; value: string }[] = [];
        if (callId) meta.push({ key: "call_id", value: `\`${callId}\`` });
        meta.push({ key: "exit_code", value: success ? "`0`" : "`1`" });
        meta.push({ key: "tool_name", value: `\`${name}\`` });
        if (!success) meta.push({ key: "is_error", value: "`true`" });
        events.push(ev({
          ts, kind: "TOOL OUTPUT",
          ident: `${name} (${callId})`,
          meta,
          blocks: [{ lang: "text", text: redact(outputText) }],
        }));
        continue;
      }
      if (payload.type === "web_search_end") {
        const callId = String(payload.call_id || "");
        const action = (payload.action && typeof payload.action === "object") ? payload.action : null;
        const queries: any[] | null = action && Array.isArray(action.queries) ? action.queries : null;
        const bodyText = queries && queries.length
          ? queries.map((q: any) => String(q)).join("\n")
          : String(payload.query || "");
        const name = "web_search";
        if (callId) callNames[callId] = name;
        events.push(ev({
          ts, kind: "TOOL CALL", ident: name,
          meta: callId ? [{ key: "call_id", value: `\`${callId}\`` }] : [],
          blocks: [{ lang: "text", text: redact(bodyText) }],
        }));
        continue;
      }
      continue;
    }
    if (rowType !== "response_item") continue;

    if (payload.type === "message") {
      const role = String(payload.role || "unknown");
      if (role === "developer" || role === "system") continue;
      const text = extractCodexText(payload.content);
      if (!text || isEnvContext(text)) continue;
      const kind: Ev["kind"] = role === "user" ? "USER" : role === "assistant" ? "ASSISTANT" : "SYSTEM";
      events.push(ev({
        ts, kind,
        blocks: [{ lang: "text", text: redact(text) }],
      }));
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
        callNames[callId] = name;
        // Back-patch any tool_output emitted earlier (rare but possible).
        const pending = pendingOutputIndices[callId];
        if (pending) {
          for (const idx of pending) {
            const t = events[idx];
            if (!t) continue;
            t.ident = `${name} (${callId})`;
            t.meta.push({ key: "tool_name", value: `\`${name}\`` });
          }
          delete pendingOutputIndices[callId];
        }
      }
      const meta: { key: string; value: string }[] = [];
      if (callId) meta.push({ key: "call_id", value: `\`${callId}\`` });
      const blocks = command
        ? [{
            lang: "text",
            text: redact(Array.isArray(command) ? command.join(" ") : String(command)),
          }]
        : [{ lang: "json", text: redact(argsText) }];
      events.push(ev({
        ts, kind: "TOOL CALL",
        ident: name,
        meta,
        blocks,
      }));
    } else if (payload.type === "function_call_output" || payload.type === "tool_search_output") {
      const output = String(payload.output || "");
      const callId = String(payload.call_id || "");
      const exit = extractExitCode(output);
      const name = callId ? callNames[callId] : "";
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
      events.push(evt);
      if (!name && callId) {
        (pendingOutputIndices[callId] ||= []).push(events.length - 1);
      }
    }

    if (onProgress && events.length - lastProgressAt >= progressEvery) {
      lastProgressAt = events.length;
      onProgress({ rows: rowCount, events: events.length, bytes: 0 });
    }
  }

  const usage = latestTotal ? codexUsageFromTotal(latestTotal) : null;
  if (usage) {
    events.push(ev({
      ts: latestUsageTs || startedAt || "",
      kind: "USAGE",
      meta: usageMeta(usage),
    }));
  }

  if (onProgress) onProgress({ rows: rowCount, events: events.length, bytes: text.length });

  const fm: Frontmatter = {
    agent: "codex",
    session_id: sessionId,
    started_at: startedAt,
    cwd,
    source_path: "",
    tags: ["codex-live-log"],
  };
  return { events, fm };
}

// ---------- public entry ----------

export function parseRawJsonl(text: string, opts: ParseOpts = {}):
  { events: Ev[]; fm: Frontmatter; format: "claude-jsonl" | "codex-jsonl" } | null {
  const format = detectRawJsonl(text);
  if (format === "claude-jsonl") {
    const { events, fm } = parseClaudeJsonl(text, opts);
    return { events, fm, format };
  }
  if (format === "codex-jsonl") {
    const { events, fm } = parseCodexJsonl(text, opts);
    return { events, fm, format };
  }
  return null;
}
