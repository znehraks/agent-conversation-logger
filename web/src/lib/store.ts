// IndexedDB-backed persistence for the loaded LoadedFile[].
//
// One blob per page (single key "current") because the viewer always works on
// one combined library; LRU only matters within that blob when we hit the
// quota. Cross-tab sync uses BroadcastChannel. A schema_version is stored
// alongside the data so a future Ev/Frontmatter shape change can be caught
// and surfaced to the user instead of silently breaking the viewer.

import type { LoadedFile } from "./classify";

const DB_NAME = "acl-store";
const STORE = "library";
const KEY = "current";
const DB_VERSION = 1;

/**
 * Bump this whenever the LoadedFile / Ev / Frontmatter shape changes in an
 * incompatible way. Reading a different version triggers the topbar warning.
 */
export const SCHEMA_VERSION = 2;

interface Stored {
  schema_version: number;
  saved_at: number;
  loaded_files: LoadedFile[];
}

export interface LoadResult {
  data: LoadedFile[];
  saved_at: number;
  schema_match: boolean;
  stored_version: number;
}

let _dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    req.onsuccess = () => {
      const db = req.result;
      db.onversionchange = () => db.close();
      resolve(db);
    };
    req.onerror = () => reject(req.error);
    req.onblocked = () => reject(new Error("indexeddb blocked"));
  });
  return _dbPromise;
}

async function tx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T> | void): Promise<T> {
  const db = await openDb();
  return new Promise<T>((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const s = t.objectStore(STORE);
    const req = run(s);
    if (req) {
      req.onsuccess = () => resolve(req.result as T);
      req.onerror = () => reject(req.error);
    } else {
      t.oncomplete = () => resolve(undefined as unknown as T);
    }
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error || new Error("aborted"));
  });
}

/** Try to persist the whole array; on QuotaExceededError, drop the oldest
 *  files one at a time and retry. Files at the front of the array are treated
 *  as least-recently-used (drop order matches their original drop order).
 *  Returns the number of files actually saved (≤ files.length). */
export async function saveLibrary(files: LoadedFile[]): Promise<{ saved: number; trimmed: number }> {
  let attempt = files.slice();
  while (attempt.length > 0) {
    try {
      const payload: Stored = {
        schema_version: SCHEMA_VERSION,
        saved_at: Date.now(),
        loaded_files: attempt,
      };
      await tx("readwrite", (s) => s.put(payload, KEY));
      return { saved: attempt.length, trimmed: files.length - attempt.length };
    } catch (err: any) {
      const name = err?.name || "";
      if ((name === "QuotaExceededError" || name === "NotEnoughSpaceError") && attempt.length > 1) {
        attempt = attempt.slice(1); // LRU: drop oldest
        continue;
      }
      throw err;
    }
  }
  // empty array
  await tx("readwrite", (s) => s.delete(KEY));
  return { saved: 0, trimmed: files.length };
}

export async function loadLibrary(): Promise<LoadResult | null> {
  try {
    const v = await tx<Stored | undefined>("readonly", (s) => s.get(KEY));
    if (!v || !v.loaded_files) return null;
    return {
      data: v.loaded_files,
      saved_at: typeof v.saved_at === "number" ? v.saved_at : 0,
      schema_match: v.schema_version === SCHEMA_VERSION,
      stored_version: typeof v.schema_version === "number" ? v.schema_version : 0,
    };
  } catch {
    return null;
  }
}

export async function clearLibrary(): Promise<void> {
  try {
    await tx("readwrite", (s) => s.delete(KEY));
  } catch {
    /* swallow — failing to clear is non-fatal */
  }
}

// ---- BroadcastChannel for multi-tab sync ----

const CHANNEL = "acl-store";
let _channel: BroadcastChannel | null = null;

function channel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  if (!_channel) _channel = new BroadcastChannel(CHANNEL);
  return _channel;
}

export type StoreEvent = "save" | "clear";

export function notifyChange(kind: StoreEvent): void {
  channel()?.postMessage({ kind });
}

export function onStoreChange(cb: (kind: StoreEvent) => void): () => void {
  const ch = channel();
  if (!ch) return () => {};
  const handler = (e: MessageEvent) => {
    const k = e.data?.kind;
    if (k === "save" || k === "clear") cb(k);
  };
  ch.addEventListener("message", handler);
  return () => ch.removeEventListener("message", handler);
}
