import { useRef, useState } from "react";

export function Launcher({ onFiles }: { onFiles: (files: FileList | File[]) => void }) {
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const dirRef = useRef<HTMLInputElement>(null);

  return (
    <div
      className="launcher"
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault(); setDrag(false);
        if (e.dataTransfer.files?.length) onFiles(e.dataTransfer.files);
      }}
    >
      <div className={`drop-card${drag ? " drag" : ""}`}>
        <div className="emoji">💬</div>
        <h1>Agent Conversation Viewer</h1>
        <p className="lead">
          Codex · Claude Code 대화 로그를 메신저 UI로.
          <br />
          <b>파일을 끌어다 놓기만</b> 하면 됩니다.
        </p>

        <div className="modes">
          <div className="mode mode-primary">
            <div className="mode-head">
              <span className="mode-badge">설치 없이</span>
              <h3>에이전트가 만든 원본 <code>.jsonl</code> 그대로</h3>
            </div>
            <p className="mode-desc">로거를 안 깔아도 됩니다. 다음 경로의 파일을 그대로 드롭하세요.</p>
            <dl className="paths">
              <dt>Claude Code</dt>
              <dd><code>~/.claude/projects/&lt;프로젝트&gt;/&lt;sid&gt;.jsonl</code></dd>
              <dt>Codex</dt>
              <dd><code>~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl</code></dd>
            </dl>
          </div>

          <div className="mode">
            <div className="mode-head">
              <span className="mode-badge ghost">이미 적재 중</span>
              <h3>로거가 만든 정제 파일</h3>
            </div>
            <p className="mode-desc">
              <code>transcript.md</code> · <code>*.eval.md</code> 그대로. 분할된{" "}
              <code>transcript.NNN.md</code>는 함께 드롭하면 자동 연결.
            </p>
          </div>
        </div>

        <div className="cta">
          <label className="file-btn">
            파일 선택
            <input ref={fileRef} type="file" accept=".md,.markdown,.txt,.jsonl" multiple hidden
              onChange={(e) => e.target.files && onFiles(e.target.files)} />
          </label>
          <label className="file-btn ghost">
            폴더 선택
            {/* @ts-expect-error non-standard attribute */}
            <input ref={dirRef} type="file" webkitdirectory="" directory="" multiple hidden
              onChange={(e) => e.target.files && onFiles(e.target.files)} />
          </label>
        </div>

        <p className="hint">
          🔒 로컬에서만 처리 · 로거 세팅은{" "}
          <a href="https://github.com/znehraks/agent-conversation-logger" target="_blank" rel="noreferrer">
            GitHub →
          </a>
        </p>
      </div>
    </div>
  );
}
