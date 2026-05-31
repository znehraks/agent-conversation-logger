import { forwardRef } from "react";
import { Virtuoso, VirtuosoHandle } from "react-virtuoso";
import { Ev } from "../lib/parse";
import { EventRow } from "./EventRow";

// Virtualized event stream — only visible rows are in the DOM, so even a 16k-event
// session (or many concatenated rotation parts) scrolls smoothly without freezing.
export const ChatView = forwardRef<VirtuosoHandle, { events: Ev[] }>(({ events }, ref) => {
  return (
    <Virtuoso
      ref={ref}
      className="stream"
      totalCount={events.length}
      overscan={600}
      itemContent={(index) => {
        const ev = events[index];
        const prev = index > 0 ? events[index - 1] : null;
        const date = ev.ts?.slice(0, 10) || "";
        const prevDate = prev?.ts?.slice(0, 10) || "";
        const showDivider = date && date !== prevDate;
        return (
          <div className="stream-inner">
            {showDivider && <div className="day-divider"><span>{date}</span></div>}
            <EventRow ev={ev} />
          </div>
        );
      }}
    />
  );
});
ChatView.displayName = "ChatView";
