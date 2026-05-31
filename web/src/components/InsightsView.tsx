import { useMemo } from "react";
import { Ev, fmtTime } from "../lib/parse";
import { computeInsights, fmtDur, fmtNum } from "../lib/insights";

export function InsightsView({ id, agent, events }: { id: string; agent: string; events: Ev[] }) {
  const ins = useMemo(() => computeInsights(events), [events]);
  const maxTool = ins.tools.length ? ins.tools[0][1] : 0;
  const u = ins.usage;
  const hitRate = u.in + u.cache_read ? Math.round((u.cache_read / (u.in + u.cache_read)) * 100) : 0;
  const errRate = ins.toolOutput ? Math.round((ins.errors.length / ins.toolOutput) * 100) : 0;

  // numeric cards compact-format + keep the exact value in a tooltip; string cards (duration, %) pass through.
  const numCard = (lbl: string, n: number, alert = false) => (
    <div className={`ins-card${alert ? " alert" : ""}`}><div className="num" title={n.toLocaleString()}>{fmtNum(n)}</div><div className="lbl">{lbl}</div></div>
  );
  const strCard = (lbl: string, s: React.ReactNode) => (
    <div className="ins-card"><div className="num">{s}</div><div className="lbl">{lbl}</div></div>
  );

  return (
    <div className="insights">
      <div className="ins-head">
        <h2>📊 {id}</h2>
        <div className="ins-sub">{agent}{ins.firstTs ? ` · ${ins.firstTs} → ${ins.lastTs}` : ""}</div>
      </div>

      <div className="ins-cards">
        {numCard("전체 이벤트", ins.total)}
        {strCard("소요 시간", fmtDur(ins.durationMs))}
        {numCard("유저 턴", ins.user)}
        {numCard("어시스턴트 턴", ins.assistant)}
        {numCard("툴 호출", ins.toolCall)}
        {numCard("에러", ins.errors.length, ins.errors.length > 0)}
        {ins.thinking > 0 && numCard("사고(thinking)", ins.thinking)}
      </div>

      {ins.usageCount > 0 && (
        <div className="ins-section">
          <h3>토큰 사용량 (USAGE {ins.usageCount}블록 합산)</h3>
          <div className="ins-cards">
            {numCard("입력 토큰", u.in)}
            {numCard("출력 토큰", u.out)}
            {numCard("캐시 read", u.cache_read)}
            {u.cache_write > 0 && numCard("캐시 write", u.cache_write)}
            {u.reasoning > 0 && numCard("추론 토큰", u.reasoning)}
            {numCard("총 토큰", u.total)}
            {strCard("캐시 적중률", `${hitRate}%`)}
          </div>
        </div>
      )}

      <div className="ins-section">
        <h3>툴 사용 분포</h3>
        {ins.tools.length === 0 ? <div className="ins-sub">툴 호출 없음</div> :
          ins.tools.map(([n, c]) => (
            <div className="bar-row" key={n}>
              <span className="bar-label">{n}</span>
              <span className="bar-track"><span className="bar-fill" style={{ width: `${maxTool ? (c / maxTool) * 100 : 0}%` }} /></span>
              <span className="bar-val">{c}</span>
            </div>
          ))}
      </div>

      <div className="ins-section">
        <h3>에러 / 실패 — {ins.errors.length}건 (툴 출력 대비 {errRate}%)</h3>
        {ins.errors.length === 0 ? <div className="ins-ok">✓ 에러/실패 없음</div> :
          ins.errors.slice(0, 100).map((e, i) => (
            <div className="err-item" key={i}>
              <span className="err-tool">{e.tool}</span>
              <span className="err-code">{e.code !== null ? `exit ${e.code}` : "error"}</span>
              <span className="err-ts">{fmtTime(e.ts)}</span>
            </div>
          ))}
        {ins.errors.length > 100 && <div className="ins-sub">…외 {ins.errors.length - 100}건</div>}
      </div>
    </div>
  );
}
