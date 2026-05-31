// Minimal markdown -> HTML for document mode (headings, bold/italic, code, fenced
// blocks, lists, tables, blockquotes, hr, links). Ported from viewer.html.

function esc(s: string): string {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string)
  );
}

function inline(s: string): string {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}

export function mdToHtml(md: string): string {
  const lines = md.replace(/\r/g, "").split("\n");
  const out: string[] = [];
  let i = 0, inCode = false, codeBuf: string[] = [], listType: string | null = null;
  const closeList = () => { if (listType) { out.push(listType === "ul" ? "</ul>" : "</ol>"); listType = null; } };
  while (i < lines.length) {
    const line = lines[i];
    if (inCode) {
      if (line.trim() === "```") { out.push(`<pre class="doc-code"><code>${esc(codeBuf.join("\n"))}</code></pre>`); inCode = false; codeBuf = []; }
      else codeBuf.push(line);
      i++; continue;
    }
    if (/^```(\w*)\s*$/.test(line)) { closeList(); inCode = true; codeBuf = []; i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(lines[i + 1])) {
      closeList();
      const header = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim())); i++;
      }
      let t = '<table class="doc-table"><thead><tr>' + header.map((h) => `<th>${inline(h)}</th>`).join("") + "</tr></thead><tbody>";
      for (const r of rows) t += "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
      out.push(t + "</tbody></table>");
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) { closeList(); const n = h[1].length; out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
    if (/^\s*---+\s*$/.test(line)) { closeList(); out.push("<hr>"); i++; continue; }
    if (/^\s*>\s?/.test(line)) {
      closeList(); const buf: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      out.push(`<blockquote class="doc-quote">${inline(buf.join(" "))}</blockquote>`); continue;
    }
    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    if (ul) { if (listType !== "ul") { closeList(); out.push('<ul class="doc-list">'); listType = "ul"; } out.push(`<li>${inline(ul[1])}</li>`); i++; continue; }
    const ol = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (ol) { if (listType !== "ol") { closeList(); out.push('<ol class="doc-list">'); listType = "ol"; } out.push(`<li>${inline(ol[1])}</li>`); i++; continue; }
    if (!line.trim()) { closeList(); i++; continue; }
    closeList();
    const para = [line]; i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*[-*]\s|\s*\d+\.\s|\s*>|\s*---+\s*$|\s*\|)/.test(lines[i])) { para.push(lines[i]); i++; }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  closeList();
  if (inCode) out.push(`<pre class="doc-code"><code>${esc(codeBuf.join("\n"))}</code></pre>`);
  return out.join("\n");
}
