import { Ev } from "./parse";

export interface Usage { in: number; out: number; cache_read: number; cache_write: number; reasoning: number; total: number }
export interface Insights {
  total: number;
  user: number; assistant: number; system: number; thinking: number; toolCall: number; toolOutput: number;
  tools: [string, number][];
  errors: { tool: string; ts: string; code: string | null }[];
  usage: Usage; usageCount: number;
  firstTs: string | null; lastTs: string | null; durationMs: number;
}

function metaVal(ev: Ev, key: string): string | null {
  const e = ev.meta.find((m) => m.key === key);
  return e ? e.value.replace(/`/g, "").trim() : null;
}

export function fmtDur(ms: number): string {
  if (!isFinite(ms) || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}

export function computeInsights(events: Ev[]): Insights {
  const stat = { user: 0, assistant: 0, system: 0, thinking: 0, toolCall: 0, toolOutput: 0 };
  const toolCounts: Record<string, number> = {};
  const errors: Insights["errors"] = [];
  const usage: Usage = { in: 0, out: 0, cache_read: 0, cache_write: 0, reasoning: 0, total: 0 };
  let usageCount = 0, firstTs: string | null = null, lastTs: string | null = null;

  for (const ev of events) {
    if (ev.ts) { if (!firstTs) firstTs = ev.ts; lastTs = ev.ts; }
    switch (ev.kind) {
      case "USER": stat.user++; break;
      case "ASSISTANT": stat.assistant++; break;
      case "SYSTEM": stat.system++; break;
      case "THINKING": stat.thinking++; break;
      case "TOOL CALL": {
        stat.toolCall++;
        const n = ev.ident || "(unknown)";
        toolCounts[n] = (toolCounts[n] || 0) + 1;
        break;
      }
      case "TOOL OUTPUT": {
        stat.toolOutput++;
        const code = metaVal(ev, "exit_code");
        const isErr = metaVal(ev, "is_error");
        if ((isErr && isErr.toLowerCase() === "true") || (code !== null && code !== "" && code !== "0")) {
          const tool = (ev.ident || "").split(" (")[0] || metaVal(ev, "tool_name") || "tool";
          errors.push({ tool, ts: ev.ts, code });
        }
        break;
      }
      case "USAGE": {
        usageCount++;
        for (const m of ev.meta) {
          const n = parseInt(m.value.replace(/`/g, ""), 10);
          if (m.key in usage && !isNaN(n)) (usage as any)[m.key] += n;
        }
        break;
      }
    }
  }
  const durationMs = firstTs && lastTs ? Date.parse(lastTs) - Date.parse(firstTs) : NaN;
  const tools = Object.entries(toolCounts).sort((a, b) => b[1] - a[1]);
  return {
    total: events.length,
    user: stat.user, assistant: stat.assistant, system: stat.system, thinking: stat.thinking,
    toolCall: stat.toolCall, toolOutput: stat.toolOutput,
    tools, errors, usage, usageCount, firstTs, lastTs, durationMs,
  };
}
