import { Ev, Frontmatter, inferAgent, parseEvents, parseFrontmatter } from "./parse";

export type FileType = "transcript" | "document" | "ambiguous";

export interface LoadedFile {
  name: string;
  fm: Frontmatter;
  body: string;
  events: Ev[];
  type: FileType;
}

export interface Session {
  id: string;
  agent: string;
  files: LoadedFile[];        // ordered oldest -> newest part
  events: Ev[];               // concatenated across parts
  partStarts: { name: string; index: number; count: number }[];
}

export interface Library {
  sessions: Session[];
  docs: LoadedFile[];
}

// Filename-first classification (matches the loggers + vanilla viewer).
export function classifyFile(filename: string, fm: Frontmatter): FileType {
  const base = (filename || "").trim().toLowerCase();
  if (/^transcript(\.\d+)?\.md$/.test(base)) return "transcript";
  const tags = fm.tags;
  if (Array.isArray(tags) && tags.some((t) => /-live-log$/.test(String(t)))) return "transcript";
  if (/\.eval\.md$/.test(base)) return "document";
  return "ambiguous";
}

// transcript.md is the active/newest part; transcript.NNN.md are older (001 oldest).
export function partRank(filename: string): number {
  const base = (filename || "").toLowerCase();
  if (base === "transcript.md") return Number.MAX_SAFE_INTEGER;
  const m = /^transcript\.(\d+)\.md$/.exec(base);
  if (m) return parseInt(m[1], 10);
  return 0;
}

export function loadFile(name: string, text: string): LoadedFile {
  const [fm, body] = parseFrontmatter(text);
  const events = parseEvents(body);
  let type = classifyFile(name, fm);
  if (type === "ambiguous") type = events.length > 0 ? "transcript" : "document";
  return { name, fm, body, events, type };
}

export function buildLibrary(files: LoadedFile[]): Library {
  const transcripts = files.filter((f) => f.type === "transcript");
  const docs = files.filter((f) => f.type !== "transcript");

  const groups = new Map<string, LoadedFile[]>();
  for (const f of transcripts) {
    const key = (f.fm.session_id as string) || f.name;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(f);
  }

  const sessions: Session[] = [];
  for (const [id, parts] of groups) {
    parts.sort((a, b) => partRank(a.name) - partRank(b.name));
    const events: Ev[] = [];
    const partStarts: Session["partStarts"] = [];
    for (const p of parts) {
      partStarts.push({ name: p.name, index: events.length, count: p.events.length });
      events.push(...p.events);
    }
    const agent = inferAgent(parts[0].fm) || "agent";
    sessions.push({ id, agent, files: parts, events, partStarts });
  }
  // Most events first (likely the session of interest).
  sessions.sort((a, b) => b.events.length - a.events.length);
  return { sessions, docs };
}
