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
        <div className="emoji">🗨️</div>
        <h1>Transcript Viewer</h1>
        <p>
          <code>transcript.md</code> (대화) 와 <code>*.eval.md</code> (보고서) 를<br />
          <b>여러 개 한꺼번에</b> 끌어다 놓으세요. 같은 세션의 회전 파트들은 자동으로 이어집니다.
        </p>
        <label className="file-btn">
          파일 선택
          <input ref={fileRef} type="file" accept=".md,.markdown,.txt" multiple hidden
            onChange={(e) => e.target.files && onFiles(e.target.files)} />
        </label>
        <label className="file-btn ghost">
          폴더 선택
          {/* @ts-expect-error non-standard attribute */}
          <input ref={dirRef} type="file" webkitdirectory="" directory="" multiple hidden
            onChange={(e) => e.target.files && onFiles(e.target.files)} />
        </label>
        <p className="hint">로컬에서만 읽힙니다 — 어떤 파일도 업로드되지 않습니다.</p>
      </div>
    </div>
  );
}
