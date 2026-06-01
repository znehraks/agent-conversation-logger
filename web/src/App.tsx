import { useEffect, useRef, useState } from "react";
import { VirtuosoHandle } from "react-virtuoso";
import { Launcher } from "./components/Launcher";
import { Sidebar, Selection } from "./components/Sidebar";
import { ChatView } from "./components/ChatView";
import { InsightsView } from "./components/InsightsView";
import { DocumentView } from "./components/DocumentView";
import { ProgressView, ProgressItem } from "./components/ProgressView";
import { buildLibrary, loadFile, Library, LoadedFile } from "./lib/classify";
import { isJsonl, parseJsonlFile, ParseHandle } from "./lib/jsonlClient";
import {
  SCHEMA_VERSION,
  clearLibrary,
  loadLibrary,
  notifyChange,
  onStoreChange,
  saveLibrary,
} from "./lib/store";

type RestoreNotice =
  | { type: "restored"; saved_at: number }
  | { type: "schema-mismatch"; saved_at: number; stored_version: number };

export default function App() {
  const [library, setLibrary] = useState<Library | null>(null);
  const [loadedFiles, setLoadedFiles] = useState<LoadedFile[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [tab, setTab] = useState<"chat" | "insights">("chat");
  const [progress, setProgress] = useState<ProgressItem[] | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [restoreNotice, setRestoreNotice] = useState<RestoreNotice | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const handlesRef = useRef<ParseHandle[]>([]);
  const dragDepthRef = useRef(0);
  const virtuoso = useRef<VirtuosoHandle>(null);

  // -- mount: try to restore previous session ----------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const res = await loadLibrary();
      if (cancelled) return;
      if (!res || !res.data.length) { setHydrated(true); return; }
      // Best-effort restore even when schema versions differ: our LoadedFile
      // shape is forward-tolerant (extra fields ignored, missing ones default).
      // If buildLibrary actually throws we drop back to the launcher cleanly.
      try {
        const lib = buildLibrary(res.data);
        setLoadedFiles(res.data);
        setLibrary(lib);
        if (lib.sessions.length) setSelection({ type: "session", id: lib.sessions[0].id });
        else if (lib.docs.length) setSelection({ type: "doc", id: lib.docs[0].name });
        setRestoreNotice(res.schema_match
          ? { type: "restored", saved_at: res.saved_at }
          : { type: "schema-mismatch", saved_at: res.saved_at, stored_version: res.stored_version });
      } catch {
        // Stored data is too incompatible to render — show the mismatch
        // warning and leave the user on the launcher.
        setRestoreNotice({ type: "schema-mismatch", saved_at: res.saved_at, stored_version: res.stored_version });
      } finally {
        setHydrated(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // -- save on change (debounced 500ms) ----------------------------------
  useEffect(() => {
    if (!hydrated) return; // never overwrite stored data with an empty initial state
    const t = setTimeout(() => {
      saveLibrary(loadedFiles)
        .then((info) => {
          if (info.trimmed > 0) {
            // user-visible heads-up that we had to evict to fit quota
            console.warn(`[store] LRU trimmed ${info.trimmed} file(s) to fit storage quota`);
          }
          notifyChange("save");
        })
        .catch((err) => console.warn("[store] save failed:", err));
    }, 500);
    return () => clearTimeout(t);
  }, [loadedFiles, hydrated]);

  // -- cross-tab sync ----------------------------------------------------
  useEffect(() => {
    return onStoreChange((kind) => {
      if (kind === "clear") {
        // another tab nuked the store — drop our view too so we don't
        // immediately overwrite the empty state on next save.
        setLibrary(null); setLoadedFiles([]); setSelection(null); setRestoreNotice(null);
      } else if (kind === "save") {
        loadLibrary().then((res) => {
          if (!res || !res.data.length) return;
          try {
            const lib = buildLibrary(res.data);
            setLoadedFiles(res.data);
            setLibrary(lib);
          } catch { /* ignore: other tab wrote incompatible data */ }
        });
      }
    });
  }, []);

  async function onFiles(files: FileList | File[]) {
    const arr = Array.from(files).filter((f) => /\.(md|markdown|txt|jsonl)$/i.test(f.name));
    if (!arr.length) return;

    const items: ProgressItem[] = arr.map((f) => ({
      name: f.name,
      size: f.size,
      bytes: 0,
      rows: 0,
      events: 0,
      status: "queued",
    }));
    setProgress(items);
    handlesRef.current = [];

    const update = (idx: number, patch: Partial<ProgressItem>) => {
      setProgress((prev) => {
        if (!prev) return prev;
        const next = prev.slice();
        next[idx] = { ...next[idx], ...patch };
        return next;
      });
    };

    const loaders = arr.map(async (file, idx): Promise<LoadedFile | null> => {
      try {
        if (isJsonl(file)) {
          update(idx, { status: "parsing" });
          const handle = parseJsonlFile(file, (p) => {
            update(idx, {
              status: "parsing",
              bytes: p.bytes,
              rows: p.rows,
              events: p.events,
            });
          });
          handlesRef.current.push(handle);
          const res = await handle.promise;
          update(idx, {
            status: "done",
            bytes: file.size,
            rows: res.rows,
            events: res.events.length,
          });
          return { name: res.name, fm: res.fm, body: "", events: res.events, type: "transcript" };
        }
        update(idx, { status: "parsing" });
        const text = await file.text();
        const lf = loadFile(file.name, text);
        update(idx, {
          status: "done",
          bytes: file.size,
          rows: 0,
          events: lf.events.length,
        });
        return lf;
      } catch (err: any) {
        update(idx, { status: "error", message: String(err?.message || err) });
        return null;
      }
    });

    const newlyLoaded = (await Promise.all(loaders)).filter(
      (f): f is LoadedFile => !!f && (f.body.trim().length > 0 || f.events.length > 0)
    );
    if (!newlyLoaded.length) return;

    const merged = [...loadedFiles, ...newlyLoaded];
    setLoadedFiles(merged);
    const lib = buildLibrary(merged);
    setLibrary(lib);
    setProgress(null);
    handlesRef.current = [];

    // A fresh drop is an explicit action — clear the "restored" notice so the
    // topbar isn't lying about where the visible data came from.
    setRestoreNotice(null);

    const newIds = new Set(
      newlyLoaded.map((f) => (f.fm.session_id as string) || f.name).filter(Boolean)
    );
    const newSession = lib.sessions.find((s) => newIds.has(s.id));
    const newDoc = lib.docs.find((d) => newIds.has(d.name));
    if (newSession) setSelection({ type: "session", id: newSession.id });
    else if (newDoc) setSelection({ type: "doc", id: newDoc.name });
    else if (!selection && lib.sessions.length) setSelection({ type: "session", id: lib.sessions[0].id });
    else if (!selection && lib.docs.length) setSelection({ type: "doc", id: lib.docs[0].name });
    setTab("chat");
  }

  function cancelParsing() {
    for (const h of handlesRef.current) { try { h.cancel(); } catch { /* */ } }
    handlesRef.current = [];
    setProgress(null);
  }

  function resetToLauncher() {
    setLibrary(null);
    setLoadedFiles([]);
    setSelection(null);
    setProgress(null);
    setRestoreNotice(null);
  }

  async function clearStored() {
    await clearLibrary();
    notifyChange("clear");
    resetToLauncher();
  }

  const shellDragHandlers = {
    onDragEnter: (e: React.DragEvent) => {
      if (!e.dataTransfer?.types?.includes("Files")) return;
      e.preventDefault();
      dragDepthRef.current += 1;
      setDragOver(true);
    },
    onDragOver: (e: React.DragEvent) => {
      if (!e.dataTransfer?.types?.includes("Files")) return;
      e.preventDefault();
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!e.dataTransfer?.types?.includes("Files")) return;
      e.preventDefault();
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) setDragOver(false);
    },
    onDrop: (e: React.DragEvent) => {
      if (!e.dataTransfer?.files?.length) return;
      e.preventDefault();
      dragDepthRef.current = 0;
      setDragOver(false);
      onFiles(e.dataTransfer.files);
    },
  };

  if (progress && !library) return <ProgressView items={progress} onCancel={cancelParsing} />;
  if (!library) return <Launcher onFiles={onFiles} />;

  const session = selection?.type === "session" ? library.sessions.find((s) => s.id === selection.id) : null;
  const doc = selection?.type === "doc" ? library.docs.find((d) => d.name === selection.id) : null;

  return (
    <div className="shell" {...shellDragHandlers}>
      <div className="topbar">
        <span className="brand">💬 Transcript Viewer</span>
        <span className="count">{library.sessions.length} sessions · {library.docs.length} docs</span>
        {restoreNotice && (
          <RestoreChip notice={restoreNotice} onClear={clearStored} onDismiss={() => setRestoreNotice(null)} />
        )}
        <span className="grow" />
        <span className="topbar-hint">파일을 추가로 끌어다 놓으면 사이드바에 추가됩니다</span>
        <button onClick={resetToLauncher}>← 새로 열기</button>
      </div>
      <div className="body">
        <Sidebar
          library={library}
          selection={selection}
          onSelect={(s) => { setSelection(s); setTab("chat"); }}
          onJumpPart={(index) => virtuoso.current?.scrollToIndex({ index, align: "start" })}
        />
        <main className="main">
          {session && (
            <>
              <div className="tabs">
                <button className={`tab${tab === "chat" ? " active" : ""}`} onClick={() => setTab("chat")}>💬 대화</button>
                <button className={`tab${tab === "insights" ? " active" : ""}`} onClick={() => setTab("insights")}>📊 인사이트</button>
              </div>
              {session.files.length > 1 && tab === "chat" && (
                <div className="banner">{session.files.length}개 파트가 이어져 있습니다 — 좌측 목차로 점프</div>
              )}
              {tab === "chat"
                ? <ChatView key={session.id} ref={virtuoso} events={session.events} />
                : <InsightsView id={session.id} agent={session.agent} events={session.events} />}
            </>
          )}
          {doc && <DocumentView body={doc.body} />}
          {!session && !doc && <div className="insights">선택된 항목이 없습니다.</div>}
        </main>
      </div>

      {dragOver && (
        <div className="drop-overlay">
          <div className="drop-overlay-card">📥 여기에 놓으면 추가됩니다</div>
        </div>
      )}

      {progress && (
        <div className="progress-modal">
          <ProgressView items={progress} onCancel={cancelParsing} />
        </div>
      )}
    </div>
  );
}

function RestoreChip({
  notice,
  onClear,
  onDismiss,
}: { notice: RestoreNotice; onClear: () => void; onDismiss: () => void }) {
  if (notice.type === "schema-mismatch") {
    return (
      <span className="restore-chip warn" title="저장된 데이터가 옛 schema라 일부 항목이 어색할 수 있습니다.">
        ⚠️ 이전 버전 (v{notice.stored_version} → v{SCHEMA_VERSION})
        <button onClick={onClear}>지우기</button>
        <button onClick={onDismiss}>닫기</button>
      </span>
    );
  }
  return (
    <span className="restore-chip" title={new Date(notice.saved_at).toLocaleString()}>
      지난번 데이터 · {fmtAgo(notice.saved_at)}
      <button onClick={onClear}>지우기</button>
      <button onClick={onDismiss}>닫기</button>
    </span>
  );
}

function fmtAgo(ts: number): string {
  if (!ts) return "이전";
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return "방금";
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}
