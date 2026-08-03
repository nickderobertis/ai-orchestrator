import { Button, Timeline, type TimelineItem } from "@oneharness/ui";
import { useMemo, useState } from "react";
import { pathTo, type TimelineRow } from "./timeline-model";

/** How many activities are revealed at once from a dense group. */
export const PAGE_SIZE = 25;

/**
 * Duration-encoded visualization of the work inside one node.
 *
 * The upstream timeline owns the legend, bars and point icons, wheel/brush zoom,
 * and compact hover details. Dense rollups stay one item until explicitly opened;
 * this preserves the bounded rendering contract for very large runs.
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
  const [expanded, setExpanded] = useState<Record<string, number>>({});
  const selectedPath = useMemo(
    () => (selectedId === undefined ? [] : pathTo(rows, selectedId)),
    [rows, selectedId],
  );
  const selectedAncestors = new Set(
    selectedPath.slice(0, -1).map(({ id }) => id),
  );
  const rendered = visibleRows(rows, expanded, selectedAncestors, selectedId);
  const items = rendered.map(toTimelineItem);

  return (
    <section aria-label="Node timeline" className="timeline-rail">
      <Timeline
        getFailureExcerpt={({ payload }) => failureExcerpt(payload)}
        items={items}
        label="Node activity timeline"
        onSelect={({ payload }) => onSelect(payload.id)}
      />
      {groups(rows).map((group) => {
        const visible = expanded[group.id] ?? 0;
        const hidden = group.children.length - visible;
        return hidden > 0 ? (
          <Button
            className="timeline-more"
            key={group.id}
            onClick={() =>
              setExpanded((current) => ({
                ...current,
                [group.id]: visible + PAGE_SIZE,
              }))
            }
            size="sm"
            type="button"
            variant="ghost"
          >
            Show {Math.min(hidden, PAGE_SIZE)} more of {group.children.length}{" "}
            {group.displayKind.toLowerCase()} activities
          </Button>
        ) : null;
      })}
    </section>
  );
}

function groups(rows: readonly TimelineRow[]): TimelineRow[] {
  return rows.flatMap((row) => [
    ...(row.rowKind === "group" ? [row] : []),
    ...groups(row.children),
  ]);
}

function visibleRows(
  rows: readonly TimelineRow[],
  expanded: Readonly<Record<string, number>>,
  selectedAncestors: ReadonlySet<string>,
  selectedId?: string,
): TimelineRow[] {
  return rows.flatMap((row) => {
    if (row.rowKind !== "group")
      return [
        row,
        ...visibleRows(row.children, expanded, selectedAncestors, selectedId),
      ];
    const selectedIndex = row.children.findIndex(({ id }) => id === selectedId);
    const count = Math.max(
      expanded[row.id] ?? 0,
      selectedAncestors.has(row.id) && selectedIndex >= 0
        ? selectedIndex + 1
        : 0,
    );
    return [row, ...row.children.slice(0, count)];
  });
}

function toTimelineItem(row: TimelineRow): TimelineItem<TimelineRow> {
  const start = Date.parse(row.startedAt);
  const end = row.endedAt === null ? undefined : Date.parse(row.endedAt);
  return {
    id: row.id,
    kind: row.displayKind,
    label: row.displayLabel,
    parent: row.rowKind === "span" ? row.span.parent_id : undefined,
    payload: row,
    start,
    end: Number.isFinite(end) ? end : undefined,
    duration: row.durationMs,
    status: row.status,
  };
}

function failureExcerpt(row: TimelineRow): string | undefined {
  if (row.rowKind !== "span" || row.status !== "failed") return undefined;
  return row.span.detail?.output_tail;
}
