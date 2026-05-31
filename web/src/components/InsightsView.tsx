import { useMemo } from "react";
import { Ev, fmtTime } from "../lib/parse";
import { computeInsights, fmtDur } from "../lib/insights";

export function InsightsView({ id, agent, events }: { id: string; agent: string; events: Ev[] }) {
  const ins = useMemo(() => computeInsights(events), [events]);
  const maxTool = ins.tools.length ? ins.tools[0][1] : 0;
  const u = ins.usage;
  const hitRate = u.in + u.cache_read ? Math.round((u.cache_read / (u.in + u.cache_read)) * 100) : 0;
  const errRate = ins.toolOutput ? Math.round((ins.errors.length / ins.toolOutput) * 100) : 0;

  const card = (lbl: string, val: React.ReactNode, alert = false) => (
    <div className={`ins-card${alert ? " alert" : ""}`}><div className="num">{val}</div><div className="lbl">{lbl}</div></div>
  );

  return (
    <div className="insights">
      <div className="ins-head">
        <h2>📊 {id}</h2>
        <div className="ins-sub">{agent}{ins.firstTs ? ` · ${ins.firstTs} → ${ins.lastTs}` : ""}</div>
      </div>

      <div className="ins-cards">
        {card("전체 이벤트", ins.total)}
        {card("소요 시간", fmtDur(ins.durationMs))}
        {card("유저 턴", ins.user)}
        {card("어시스턴트 턴", ins.assistant)}
        {card("툴 호출", ins.toolCall)}
        {card("에러", ins.errors.length, ins.errors.length > 0)}
        {ins.thinking > 0 && card("사고(thinking)", ins.thinking)}
      </div>

      {ins.usageCount > 0 && (
        <div className="ins-section">
          <h3>토큰 사용량 (USAGE {ins.usageCount}블록 합산)</h3>
          <div className="ins-cards">
            {card("입력 토큰", u.in.toLocaleString())}
            {card("출력 토큰", u.out.toLocaleString())}
            {card("캐시 read", u.cache_read.toLocaleString())}
            {u.cache_write > 0 && card("캐시 write", u.cache_write.toLocaleString())}
            {u.reasoning > 0 && card("추론 토큰", u.reasoning.toLocaleString())}
            {card("총 토큰", u.total.toLocaleString())}
            {card("캐시 적중률", `${hitRate}%`)}
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
