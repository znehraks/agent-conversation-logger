import { useRef, useState } from "react";
import { VirtuosoHandle } from "react-virtuoso";
import { Launcher } from "./components/Launcher";
import { Sidebar, Selection } from "./components/Sidebar";
import { ChatView } from "./components/ChatView";
import { InsightsView } from "./components/InsightsView";
import { DocumentView } from "./components/DocumentView";
import { ProgressView, ProgressItem } from "./components/ProgressView";
import { buildLibrary, loadFile, Library, LoadedFile } from "./lib/classify";
import { isJsonl, parseJsonlFile, ParseHandle } from "./lib/jsonlClient";

export default function App() {
  const [library, setLibrary] = useState<Library | null>(null);
  const [loadedFiles, setLoadedFiles] = useState<LoadedFile[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [tab, setTab] = useState<"chat" | "insights">("chat");
  const [progress, setProgress] = useState<ProgressItem[] | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const handlesRef = useRef<ParseHandle[]>([]);
  const dragDepthRef = useRef(0);
  const virtuoso = useRef<VirtuosoHandle>(null);

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
        // markdown / txt: small, fine to read on main thread.
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

    if (!newlyLoaded.length) {
      // Leave the progress view up so the user can read errors and retry.
      return;
    }

    // Accumulate: re-building over all loaded files keeps sidebar grouping right
    // (same session_id transcripts merge across drops; jsonls with different sids
    // appear as additional sessions).
    const merged = [...loadedFiles, ...newlyLoaded];
    setLoadedFiles(merged);
    const lib = buildLibrary(merged);
    setLibrary(lib);
    setProgress(null);
    handlesRef.current = [];

    // Auto-select something the user just dropped, so the second drop actually
    // surfaces in the main pane (and doesn't silently land in the sidebar).
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
    for (const h of handlesRef.current) {
      try { h.cancel(); } catch { /* noop */ }
    }
    handlesRef.current = [];
    setProgress(null);
  }

  function resetToLauncher() {
    setLibrary(null);
    setLoadedFiles([]);
    setSelection(null);
    setProgress(null);
  }

  // Shell-level drag handlers — let the user drop more files onto the viewer
  // itself. dragenter/dragleave can fire repeatedly as the cursor moves over
  // child nodes, so we count depth instead of toggling on a single event.
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

  // Progress shown full-screen on the *initial* parse, but as a small floating
  // modal once a library is already in view, so the viewer stays visible while
  // additional files load.
  if (progress && !library) return <ProgressView items={progress} onCancel={cancelParsing} />;
  if (!library) return <Launcher onFiles={onFiles} />;

  const session = selection?.type === "session" ? library.sessions.find((s) => s.id === selection.id) : null;
  const doc = selection?.type === "doc" ? library.docs.find((d) => d.name === selection.id) : null;

  return (
    <div className="shell" {...shellDragHandlers}>
      <div className="topbar">
        <span className="brand">💬 Transcript Viewer</span>
        <span className="count">{library.sessions.length} sessions · {library.docs.length} docs</span>
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
