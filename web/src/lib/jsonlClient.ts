// Main-thread helper for the streaming jsonl worker.
//
// Spawns one worker per parseFile() call. The worker terminates itself when
// the page closes, but we explicitly terminate after each job so we never
// hold onto memory longer than needed (jsonl events arrays can be tens of MB).

import JsonlWorker from "./jsonlWorker?worker";
import type { Ev, Frontmatter } from "./parse";

export interface JsonlProgress {
  name: string;
  rows: number;
  events: number;
  bytes: number;
  total: number;
  format: "claude-jsonl" | "codex-jsonl" | "unknown";
}

export interface JsonlResult {
  name: string;
  events: Ev[];
  fm: Frontmatter;
  format: "claude-jsonl" | "codex-jsonl";
  rows: number;
  bytes: number;
}

export interface ParseHandle {
  promise: Promise<JsonlResult>;
  cancel: () => void;
}

let counter = 0;

export function parseJsonlFile(
  file: File,
  onProgress?: (p: JsonlProgress) => void,
): ParseHandle {
  const worker = new JsonlWorker();
  const id = `j${++counter}`;
  let cancelled = false;

  const promise = new Promise<JsonlResult>((resolve, reject) => {
    worker.onmessage = (e: MessageEvent) => {
      const data = e.data || {};
      if (data.id !== id) return;
      if (data.type === "progress") {
        onProgress?.({
          name: data.name ?? file.name,
          rows: data.rows ?? 0,
          events: data.events ?? 0,
          bytes: data.bytes ?? 0,
          total: data.total ?? file.size,
          format: data.format ?? "unknown",
        });
      } else if (data.type === "done") {
        resolve({
          name: data.name ?? file.name,
          events: data.events ?? [],
          fm: data.fm ?? {},
          format: data.format,
          rows: data.rows ?? 0,
          bytes: data.bytes ?? 0,
        });
        worker.terminate();
      } else if (data.type === "error") {
        reject(new Error(data.message || "parser error"));
        worker.terminate();
      }
    };
    worker.onerror = (err) => {
      reject(new Error(err.message || "worker error"));
      worker.terminate();
    };
    worker.postMessage({ id, file, name: file.name });
  });

  return {
    promise,
    cancel: () => {
      if (cancelled) return;
      cancelled = true;
      try { worker.postMessage({ type: "cancel", id }); } catch {/* */}
      try { worker.terminate(); } catch {/* */}
    },
  };
}

export function isJsonl(file: File): boolean {
  return /\.jsonl$/i.test(file.name);
}
