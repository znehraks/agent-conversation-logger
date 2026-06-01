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
  const [selection, setSelection] = useState<Selection | null>(null);
  const [tab, setTab] = useState<"chat" | "insights">("chat");
  const [progress, setProgress] = useState<ProgressItem[] | null>(null);
  const handlesRef = useRef<ParseHandle[]>([]);
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

    const loaded = (await Promise.all(loaders)).filter(
      (f): f is LoadedFile => !!f && (f.body.trim().length > 0 || f.events.length > 0)
    );

    if (!loaded.length) {
      // Leave the progress view up so the user can read errors and retry.
      return;
    }

    const lib = buildLibrary(loaded);
    setLibrary(lib);
    setProgress(null);
    handlesRef.current = [];
    if (lib.sessions.length) setSelection({ type: "session", id: lib.sessions[0].id });
    else if (lib.docs.length) setSelection({ type: "doc", id: lib.docs[0].name });
    else setSelection(null);
    setTab("chat");
  }

  function cancelParsing() {
    for (const h of handlesRef.current) {
      try { h.cancel(); } catch { /* noop */ }
    }
    handlesRef.current = [];
    setProgress(null);
  }

  if (progress) return <ProgressView items={progress} onCancel={cancelParsing} />;
  if (!library) return <Launcher onFiles={onFiles} />;

  const session = selection?.type === "session" ? library.sessions.find((s) => s.id === selection.id) : null;
  const doc = selection?.type === "doc" ? library.docs.find((d) => d.name === selection.id) : null;

  return (
    <div className="shell">
      <div className="topbar">
        <span className="brand">🗨️ Transcript Viewer</span>
        <span className="count">{library.sessions.length} sessions · {library.docs.length} docs</span>
        <span className="grow" />
        <button onClick={() => { setLibrary(null); setSelection(null); }}>← 새로 열기</button>
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
    </div>
  );
}
