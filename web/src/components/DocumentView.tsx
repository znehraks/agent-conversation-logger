import { useMemo } from "react";
import { mdToHtml } from "../lib/markdown";

export function DocumentView({ body }: { body: string }) {
  const html = useMemo(() => mdToHtml(body), [body]);
  return <div className="doc" dangerouslySetInnerHTML={{ __html: html }} />;
}
