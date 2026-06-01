// Visible-during-parse UI: keeps the screen responsive and informative when
// raw jsonl files (which can be 100 MB+) are being streamed through the
// Web Worker parser.

export interface ProgressItem {
  name: string;
  size: number;
  bytes: number;
  rows: number;
  events: number;
  status: "queued" | "parsing" | "done" | "error";
  message?: string;
}

function fmtSize(b: number): string {
  if (!b) return "—";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`;
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function ProgressView({
  items,
  onCancel,
}: {
  items: ProgressItem[];
  onCancel: () => void;
}) {
  const anyParsing = items.some((i) => i.status === "parsing" || i.status === "queued");
  const totalRows = items.reduce((a, b) => a + b.rows, 0);
  const totalEvents = items.reduce((a, b) => a + b.events, 0);

  return (
    <div className="launcher">
      <div className="progress-card">
        <div className="emoji">{anyParsing ? "⏳" : "✅"}</div>
        <h2>{anyParsing ? "파일 분석 중…" : "거의 다 됐어요"}</h2>
        <p className="sub">
          전체 {items.length}개 파일 · 누적 {totalRows.toLocaleString()} 행 · {totalEvents.toLocaleString()} 이벤트
        </p>
        <ul className="prog-list">
          {items.map((it, i) => {
            const pct = it.size > 0 ? Math.min(100, Math.floor((it.bytes / it.size) * 100)) : (it.status === "done" ? 100 : 0);
            const barClass =
              it.status === "error" ? "bar err" :
              it.status === "done" ? "bar done" :
              "bar";
            return (
              <li key={i} className={`prog-row ${it.status}`}>
                <div className="prog-row-head">
                  <span className="prog-name" title={it.name}>{it.name}</span>
                  <span className="prog-stat">
                    {it.status === "error" ? "오류"
                      : it.status === "done" ? `${it.events.toLocaleString()} 이벤트`
                      : `${pct}%`}
                  </span>
                </div>
                <div className="prog-bar"><div className={barClass} style={{ width: `${pct}%` }} /></div>
                <div className="prog-row-meta">
                  <span>{fmtSize(it.bytes)} / {fmtSize(it.size)}</span>
                  <span>{it.rows.toLocaleString()} 행</span>
                  <span>{it.events.toLocaleString()} 이벤트</span>
                </div>
                {it.message && <div className="prog-err">{it.message}</div>}
              </li>
            );
          })}
        </ul>
        {anyParsing && (
          <button className="file-btn ghost" onClick={onCancel}>취소</button>
        )}
        <p className="hint">파싱은 Web Worker에서 돌아가요 — 이 페이지는 멈추지 않습니다.</p>
      </div>
    </div>
  );
}
