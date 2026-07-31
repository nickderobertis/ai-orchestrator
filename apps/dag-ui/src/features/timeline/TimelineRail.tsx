import {
  Badge,
  Button,
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  cn,
  ScrollArea,
} from "@oneharness/ui";
import { ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import {
  formatDuration,
  formatTime,
  pathTo,
  type TimelineRow,
} from "./timeline-model";

/** How many siblings one expanded row shows before the rest are asked for. */
export const PAGE_SIZE = 25;

/** The top level's key in the maps keyed by parent row id; no row has an empty id. */
const ROOT = "";

/**
 * The node's recorded work, in order, as a rail of collapsed rows.
 *
 * Nothing here grows with the size of the run: every row starts collapsed, long runs
 * of like siblings arrive already grouped, and each expanded level hands out a page
 * at a time. A node that recorded hundreds of sessions is as scannable as one that
 * recorded three.
 */
export function TimelineRail({
  rows,
  selectedId,
  onSelect,
}: {
  readonly rows: readonly TimelineRow[];
  readonly selectedId?: string;
  readonly onSelect: (id: string) => void;
}) {
  const [openRows, setOpenRows] = useState<Record<string, boolean>>({});
  const [pages, setPages] = useState<Record<string, number>>({});
  // Whatever the address bar selects has to be on screen, however deep it sits: its
  // ancestors open, and every level pages far enough to reach it.
  const path = useMemo(
    () => (selectedId === undefined ? [] : pathTo(rows, selectedId)),
    [rows, selectedId],
  );
  const revealed = useMemo(() => {
    const required = new Map<string, number>([]);
    let level = rows;
    let parent = ROOT;
    for (const row of path) {
      const index = level.findIndex(({ id }) => id === row.id);
      if (index >= 0) required.set(parent, index + 1);
      level = row.children;
      parent = row.id;
    }
    return required;
  }, [rows, path]);
  const ancestors = useMemo(
    () => new Set(path.slice(0, -1).map(({ id }) => id)),
    [path],
  );

  const renderLevel = (
    level: readonly TimelineRow[],
    parent: string,
    depth: number,
  ) => {
    const visible = Math.max(
      pages[parent] ?? PAGE_SIZE,
      revealed.get(parent) ?? 0,
    );
    const hidden = level.length - visible;
    return (
      <ol className="rail-list">
        {level.slice(0, visible).map((row) => {
          const open = openRows[row.id] ?? ancestors.has(row.id);
          return (
            <li key={row.id}>
              <Collapsible
                onOpenChange={(next) =>
                  setOpenRows((current) => ({ ...current, [row.id]: next }))
                }
                open={open}
              >
                <RailRow
                  depth={depth}
                  onSelect={onSelect}
                  open={open}
                  row={row}
                  selected={row.id === selectedId}
                />
                <CollapsibleContent>
                  {renderLevel(row.children, row.id, depth + 1)}
                </CollapsibleContent>
              </Collapsible>
            </li>
          );
        })}
        {hidden > 0 && (
          <li>
            <Button
              className="rail-more"
              onClick={() =>
                setPages((current) => ({
                  ...current,
                  [parent]: visible + PAGE_SIZE,
                }))
              }
              size="sm"
              type="button"
              variant="ghost"
            >
              Show {Math.min(hidden, PAGE_SIZE)} more of {level.length}
            </Button>
          </li>
        )}
      </ol>
    );
  };

  return (
    <section aria-label="Node timeline" className="timeline-rail">
      <ScrollArea className="h-full">
        <div className="p-2.5">{renderLevel(rows, ROOT, 0)}</div>
      </ScrollArea>
    </section>
  );
}

function RailRow({
  row,
  depth,
  open,
  selected,
  onSelect,
}: {
  readonly row: TimelineRow;
  readonly depth: number;
  readonly open: boolean;
  readonly selected: boolean;
  readonly onSelect: (id: string) => void;
}) {
  const expandable = row.children.length > 0;
  const summary = (
    <span className="rail-summary">
      {/* A group's own label already names the kind it stands for. */}
      {row.rowKind !== "group" && <span className="rail-kind">{row.kind}</span>}
      {row.label && <span className="rail-label">{row.label}</span>}
      {/* An aggregate stands in for records it does not list, so it says how many. */}
      {row.rowKind === "span" && row.span.count !== undefined && (
        <span className="rail-count">×{row.span.count}</span>
      )}
      <span className="rail-time">{formatTime(row.startedAt)}</span>
      {row.status && (
        <Badge className="rail-status" variant="outline">
          {row.status}
        </Badge>
      )}
      <span className="rail-duration">
        {row.durationMs === null
          ? row.rowKind === "event"
            ? "—"
            : "running"
          : formatDuration(row.durationMs)}
      </span>
    </span>
  );
  // One control per row: it opens the row's detail and discloses what it contains,
  // the way a tree row does — so a row is never two adjacent controls reading the
  // same words, and `aria-expanded` says what pressing it will do. A group is an
  // assembly of rows rather than a recorded item, so it only discloses.
  const control = (
    <button
      aria-current={selected ? "true" : undefined}
      className="rail-open"
      data-selected={selected}
      onClick={row.rowKind === "group" ? undefined : () => onSelect(row.id)}
      type="button"
    >
      <ChevronRight
        aria-hidden="true"
        className={cn(
          "rail-chevron",
          open && "rotate-90",
          !expandable && "invisible",
        )}
        size={13}
      />
      {summary}
    </button>
  );
  return (
    <div className="rail-row" style={{ paddingLeft: `${depth * 13}px` }}>
      {expandable ? (
        <CollapsibleTrigger asChild>{control}</CollapsibleTrigger>
      ) : (
        control
      )}
    </div>
  );
}
