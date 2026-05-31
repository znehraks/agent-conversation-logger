import { Library } from "../lib/classify";

export interface Selection { type: "session" | "doc"; id: string }

export function Sidebar({
  library, selection, onSelect, onJumpPart,
}: {
  library: Library;
  selection: Selection | null;
  onSelect: (s: Selection) => void;
  onJumpPart: (index: number) => void;
}) {
  const { sessions, docs } = library;
  return (
    <aside className="sidebar">
      {sessions.length > 0 && <h4>세션 ({sessions.length})</h4>}
      {sessions.map((s) => {
        const active = selection?.type === "session" && selection.id === s.id;
        return (
          <div key={s.id}>
            <div className={`side-item${active ? " active" : ""}`} onClick={() => onSelect({ type: "session", id: s.id })}>
              <div className="title">{s.id}</div>
              <div className="sub">{s.agent} · {s.events.length} events · {s.files.length} part{s.files.length > 1 ? "s" : ""}</div>
            </div>
            {active && s.files.length > 1 && (
              <div className="side-parts">
                {s.partStarts.map((p) => (
                  <div className="side-part" key={p.name} onClick={() => onJumpPart(p.index)}>
                    {p.name} ({p.count})
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {docs.length > 0 && <h4>문서 ({docs.length})</h4>}
      {docs.map((d) => {
        const active = selection?.type === "doc" && selection.id === d.name;
        return (
          <div key={d.name} className={`side-item${active ? " active" : ""}`} onClick={() => onSelect({ type: "doc", id: d.name })}>
            <div className="title">{d.name}</div>
            <div className="sub">document</div>
          </div>
        );
      })}
    </aside>
  );
}
