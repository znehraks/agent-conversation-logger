// Transcript parsing — ported from the original render_html.py / viewer.html.
// The schema both loggers emit:
//   ---<frontmatter>---  then  ## <ts> - <KIND>[ `ident`]  + bullets + fenced block

export type Kind =
  | "USER" | "ASSISTANT" | "SYSTEM" | "THINKING" | "TOOL CALL" | "TOOL OUTPUT" | "USAGE";

export interface Block { lang: string; text: string }
export interface Meta { key: string; value: string }
export interface Ev {
  ts: string;
  kind: Kind;
  ident: string | null;
  meta: Meta[];
  blocks: Block[];
}
export type Frontmatter = Record<string, string | string[]>;

const SECTION_RE =
  /^## (\S+)\s*-\s*(USER|ASSISTANT|SYSTEM|THINKING|TOOL CALL|TOOL OUTPUT|USAGE)(?:\s+`([^`]+)`)?\s*$/;
const BULLET_RE = /^- ([^:]+):\s*(.*)$/;
const FENCE_RE = /^```(\w*)\s*$/;

function stripQuotes(v: string): string {
  if (v.length >= 2 && v[0] === v[v.length - 1] && (v[0] === "'" || v[0] === '"')) {
    return v.slice(1, -1);
  }
  return v;
}

export function parseFrontmatter(text: string): [Frontmatter, string] {
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(text);
  if (!m) return [{}, text];
  const fm: Frontmatter = {};
  let curList: string | null = null;
  for (const line of m[1].split("\n")) {
    if (!line.trim()) { curList = null; continue; }
    if (line.startsWith("  - ") && curList) {
      (fm[curList] as string[]).push(stripQuotes(line.slice(4).trim()));
      continue;
    }
    if (line.includes(":") && !line.startsWith(" ")) {
      const idx = line.indexOf(":");
      const key = line.slice(0, idx).trim();
      const value = line.slice(idx + 1).trim();
      if (!value) { fm[key] = []; curList = key; }
      else { fm[key] = stripQuotes(value); curList = null; }
    }
  }
  return [fm, text.slice(m.index + m[0].length)];
}

export function parseEvents(body: string): Ev[] {
  const events: Ev[] = [];
  let current: Ev | null = null;
  let codeOpen = false, codeLang = "", codeLines: string[] = [], metaPhase = true;
  for (const line of body.split("\n")) {
    if (codeOpen) {
      if (line.trim() === "```") {
        if (current) current.blocks.push({ lang: codeLang, text: codeLines.join("\n") });
        codeOpen = false; codeLang = ""; codeLines = []; metaPhase = false;
      } else codeLines.push(line);
      continue;
    }
    const h = SECTION_RE.exec(line);
    if (h) {
      if (current) events.push(current);
      current = { ts: h[1], kind: h[2] as Kind, ident: h[3] || null, meta: [], blocks: [] };
      metaPhase = true;
      continue;
    }
    if (!current) continue;
    if (metaPhase) {
      const b = BULLET_RE.exec(line);
      if (b) { current.meta.push({ key: b[1].trim(), value: b[2].trim() }); continue; }
    }
    const f = FENCE_RE.exec(line);
    if (f) { codeOpen = true; codeLang = f[1] || "text"; codeLines = []; continue; }
  }
  if (current) events.push(current);
  return events;
}

export function fmtTime(ts: string): string {
  ts = ts || "";
  return ts.length >= 19 ? ts.slice(11, 19) : ts;
}

export function inferAgent(fm: Frontmatter): string | null {
  if (typeof fm.agent === "string") return fm.agent;
  const tags = fm.tags;
  if (Array.isArray(tags)) {
    for (const t of tags) {
      const s = String(t).toLowerCase();
      if (s.includes("codex")) return "codex";
      if (s.includes("claude")) return "claude-code";
    }
  }
  return null;
}
