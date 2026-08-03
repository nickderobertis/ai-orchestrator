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
const HELD = "border-warning bg-warning-surface text-warning";

/**
 * What each node status means, in the package's semantic utilities. Keying it by the
 * contract's own `DagNodeState` is the drift gate: `DAG_NODE_STATES` is reconciled
 * with `orchestrator.projection.NodeStatus` by `scripts/check-dag-state-contract.py`,
 * so a status added there reaches this record and fails to compile until it is given
 * a meaning, rather than quietly rendering as a badge that says nothing.
 *
 * Three readings, deliberately:
 *
 * - `blocked` and `skipped` are held work — something outside the node has to move
 *   before it can. Neutral would read as "nothing to report", which is the opposite
 *   of what they mean, so they take the warning tone the graph card already gives
 *   them. `waiting` keeps a neutral badge beside its warning card: a human action is
 *   the graph's own normal shape, and the card is where that is said.
 * - `not-completed` is a settled node whose work is unfinished, which is a lost
 *   outcome rather than a pause, so it reads with `failed` and `cancelled`.
 * - `pending` and `unknown` are `undefined` on purpose: work that has not started has
 *   no outcome to report, and a status this vocabulary does not recognize has none
 *   either. Borrowing a colour for them would be inventing the reading a neutral
 *   badge honestly declines to give.
 */
const NODE_TONE: Readonly<Record<DagNodeState, string | undefined>> = {
  blocked: HELD,
  cancelled: LOST,
  done: SETTLED,
  failed: LOST,
  "not-completed": LOST,
  pending: undefined,
  running: "border-info bg-info-surface text-info",
  skipped: HELD,
  unknown: undefined,
  waiting: undefined,
};

/**
 * The same table, plus the one word a run carries that a node does not: a run settles
 * as `complete` where a node settles as `done`, so it takes that state's meaning
 * rather than restating it. The read contract types a run's state as an open string —
 * it can also report `stopped`, `parked`, `blocked` or `unknown` — and anything not
 * named here falls through to the neutral badge.
 *
 * A run's `blocked` is not a node's: a run is blocked on a *planner* reply, a node on
 * a dependency. They share the reading this table gives them — held, awaiting
 * something outside — and they can never share a field, since a run's word comes from
 * `RunSummary.state` and a node's from `Round.node_status`. Which of the two a reader
 * is looking at is said by the surface: a run row names its run, and a node view names
 * its node and states what is holding it.
 */
const TONE: Readonly<Record<string, string | undefined>> = {
  ...NODE_TONE,
  complete: NODE_TONE.done,
};
