import { useRef, useState } from "react";
import { VirtuosoHandle } from "react-virtuoso";
import { Launcher } from "./components/Launcher";
import { Sidebar, Selection } from "./components/Sidebar";
import { ChatView } from "./components/ChatView";
import { InsightsView } from "./components/InsightsView";
import { DocumentView } from "./components/DocumentView";
import { buildLibrary, loadFile, Library, LoadedFile } from "./lib/classify";

export default function App() {
  const [library, setLibrary] = useState<Library | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [tab, setTab] = useState<"chat" | "insights">("chat");
  const virtuoso = useRef<VirtuosoHandle>(null);

  async function onFiles(files: FileList | File[]) {
    const arr = Array.from(files).filter((f) => /\.(md|markdown|txt)$/i.test(f.name));
    const loaded: LoadedFile[] = await Promise.all(
      arr.map((f) => new Promise<LoadedFile>((res) => {
        const r = new FileReader();
        r.onload = () => res(loadFile(f.name, String(r.result)));
        r.onerror = () => res(loadFile(f.name, ""));
        r.readAsText(f, "utf-8");
      }))
    );
    const lib = buildLibrary(loaded.filter((f) => f.body.trim() || f.events.length));
    setLibrary(lib);
    if (lib.sessions.length) setSelection({ type: "session", id: lib.sessions[0].id });
    else if (lib.docs.length) setSelection({ type: "doc", id: lib.docs[0].name });
    else setSelection(null);
    setTab("chat");
  }

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
