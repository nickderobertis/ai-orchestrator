import type { DagNodeState } from "@ai-orchestrator/dag-layout";
import { Badge, cn } from "@oneharness/ui";

/**
 * A run or node state, in the orchestrator's own words and the design system's own
 * semantic colours.
 *
 * The package ships `StatusBadge`, and it is the right component wherever the state
 * really is one of the four it knows — a conversation's, for one. A run or a node is
 * not: the ledger settles a node as `done` and a run as `complete`, holds a human
 * action at `waiting`, and abandons work as `cancelled`. `StatusBadge` renders an
 * unrecognized state with no tone at all, so passing these through would leave a
 * finished run and an abandoned one looking alike, and relabelling them to fit its
 * vocabulary would replace the word the ledger actually recorded. Mapping instead
 * keeps both: the recorded word, and a colour that means something.
 */
export function StateBadge({
  className,
  state,
}: {
  readonly className?: string;
  readonly state: string;
}) {
  return (
    <Badge
      className={cn(
        "gap-1.5 tracking-[.03em] uppercase",
        TONE[state],
        className,
      )}
      variant="outline"
    >
      <span
        aria-hidden="true"
        className={cn(
          "size-1.5 rounded-full bg-current",
          state === "running" && "animate-pulse",
        )}
      />
      {state}
    </Badge>
  );
}

const SETTLED = "border-success bg-success-surface text-success";
const LOST = "border-destructive bg-destructive-surface text-destructive";
const DEPENDENCY_DECIDED = "border-warning bg-warning-surface text-warning";

/** Dependency-decided states warn; unfinished settled work reads as a lost outcome. */
const NODE_TONE: Readonly<Record<DagNodeState, string | undefined>> = {
  blocked: DEPENDENCY_DECIDED,
  cancelled: LOST,
  done: SETTLED,
  failed: LOST,
  "not-completed": LOST,
  // Parked is a planner decision to idle a node whose work is preserved, so it is
  // neither lost nor decided by a dependency — it reads as held, like `waiting`.
  parked: undefined,
  pending: undefined,
  running: "border-info bg-info-surface text-info",
  skipped: DEPENDENCY_DECIDED,
  unknown: undefined,
  waiting: undefined,
};

/** Run- and node-level `blocked` differ, but both use the same held-state tone. */
const TONE: Readonly<Record<string, string | undefined>> = {
  ...NODE_TONE,
  complete: NODE_TONE.done,
};
