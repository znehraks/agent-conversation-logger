import { Ev, fmtTime } from "../lib/parse";

function prettyJson(text: string): string {
  try { return JSON.stringify(JSON.parse(text), null, 2); } catch { return text; }
}

const USAGE_LABEL: Record<string, string> = { in: "in", out: "out", cache_read: "cache", cache_write: "cache+", reasoning: "reason", total: "total" };

export function EventRow({ ev }: { ev: Ev }) {
  const time = fmtTime(ev.ts);

  if (ev.kind === "USER" || ev.kind === "ASSISTANT" || ev.kind === "SYSTEM") {
    const role = ev.kind.toLowerCase();
    return (
      <div className={`event message ${role}`}>
        <div className="bubble">{ev.blocks[0]?.text || ""}</div>
        <div className="ts">{time}</div>
      </div>
    );
  }

  if (ev.kind === "THINKING") {
    return (
      <div className="event thinking-event">
        <details>
          <summary><span className="caret" /><span className="icon">💭</span><span className="label">Thinking</span><span className="ts">{time}</span></summary>
          <div className="thinking-body"><pre className="codeblock"><code>{ev.blocks[0]?.text || ""}</code></pre></div>
        </details>
      </div>
    );
  }

  if (ev.kind === "USAGE") {
    const chips = ["in", "out", "cache_read", "cache_write", "reasoning", "total"].flatMap((k) => {
      const m = ev.meta.find((x) => x.key === k);
      if (!m) return [];
      const v = m.value.replace(/`/g, "");
      const n = parseInt(v, 10);
      return [`${USAGE_LABEL[k]} ${isNaN(n) ? v : n.toLocaleString()}`];
    });
    return <div className="event usage-line"><span>🪙 {chips.join(" · ")}</span></div>;
  }

  const isOut = ev.kind === "TOOL OUTPUT";
  return (
    <div className={`event tool-event ${isOut ? "tool-output" : "tool-call"}`}>
      <details>
        <summary>
          <span className="caret" /><span className="icon">{isOut ? "▼" : "▶"}</span>
          <span className="label">{isOut ? "Tool Output" : "Tool Call"}</span>
          <span className="ident">{ev.ident || ""}</span><span className="ts">{time}</span>
        </summary>
        <div className="tool-body">
          {ev.meta.length > 0 && (
            <div className="meta">
              {ev.meta.map((m, i) => (
                <div className="meta-row" key={i} style={{ display: "contents" }}>
                  <span className="meta-key">{m.key}</span>
                  <span className="meta-value">{m.value.replace(/`/g, "")}</span>
                </div>
              ))}
            </div>
          )}
          {ev.blocks.map((b, i) => (
            <pre className="codeblock" key={i}><code>{b.lang === "json" ? prettyJson(b.text || "") : (b.text || "")}</code></pre>
          ))}
        </div>
      </details>
    </div>
  );
}
