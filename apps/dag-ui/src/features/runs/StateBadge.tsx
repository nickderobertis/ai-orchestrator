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

/**
 * What each node state means, in the package's semantic utilities. Keying it by the
 * contract's own `DagNodeState` is the drift gate: `DAG_NODE_STATES` is reconciled
 * with `orchestrator/projection.py` by `scripts/check-dag-state-contract.py`, so a
 * state added there reaches this record and fails to compile until it is given a
 * meaning, rather than quietly rendering as a badge that says nothing. `pending` and
 * `waiting` are `undefined` deliberately: work that has not started has no outcome
 * to report, and neutral is the honest reading of that.
 */
const NODE_TONE: Readonly<Record<DagNodeState, string | undefined>> = {
  cancelled: LOST,
  done: SETTLED,
  failed: LOST,
  pending: undefined,
  running: "border-info bg-info-surface text-info",
  waiting: undefined,
};

/**
 * The same table, plus the one word a run carries that a node does not: a run settles
 * as `complete` where a node settles as `done`, so it takes that state's meaning
 * rather than restating it. The read contract types a run's state as an open string —
 * it can also report `stopped`, `parked`, `blocked` or `unknown` — and anything not
 * named here falls through to the neutral badge.
 */
const TONE: Readonly<Record<string, string | undefined>> = {
  ...NODE_TONE,
  complete: NODE_TONE.done,
};
