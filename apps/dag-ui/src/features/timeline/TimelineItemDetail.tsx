import type {
  DagConversation,
  TimelineReference,
} from "@ai-orchestrator/dag-model";
import type { TelemetryClient } from "@ai-orchestrator/telemetry-client";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  Card,
  CardContent,
  ScrollArea,
  Separator,
  Skeleton,
  StatusBadge,
  TurnCard,
} from "@oneharness/ui";
import { ExternalLink, ListTree, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { NodeView } from "../runs/run-model";
import { PAGE_SIZE } from "./TimelineRail";
import { formatDuration, formatTime, type TimelineRow } from "./timeline-model";
import { useConversation } from "./useConversation";

/**
 * The one timeline item the operator opened, expanded across the working area.
 *
 * Each recorded kind is shown as what it is: a conversation turn through the design
 * system's own `TurnCard`, a verification as the gate attestation it recorded and the
 * log it points at, a publication as the PR and the checks that were observed on it,
 * and anything else as the typed record the timeline served.
 */
export function TimelineItemDetail({
  client,
  runId,
  node,
  row,
}: {
  readonly client: TelemetryClient;
  readonly runId: string;
  readonly node: NodeView;
  readonly row?: TimelineRow;
}) {
  const reference = row === undefined ? undefined : referenceOf(row);
  const transcript = useConversation(
    client,
    runId,
    reference?.kind === "conversation" ? reference.value : undefined,
  );

  return (
    <section aria-label="Timeline item detail" className="timeline-detail">
      <ScrollArea className="h-full">
        <div className="p-[22px]">
          {row === undefined ? (
            <div className="detail-placeholder">
              <ListTree size={30} />
              <p>Select an item in the timeline to read what it recorded.</p>
            </div>
          ) : (
            <>
              <header className="detail-title">
                <div>
                  <Badge className="mb-1" variant="outline">
                    {row.kind}
                  </Badge>
                  <h2>{row.label || row.kind}</h2>
                  <p className="detail-when">
                    {formatTime(row.startedAt)}
                    {row.durationMs !== null &&
                      ` · ${formatDuration(row.durationMs)}`}
                    {row.endedAt === null &&
                      row.rowKind === "span" &&
                      " · still running"}
                  </p>
                </div>
                {row.status && <Badge variant="secondary">{row.status}</Badge>}
              </header>
              <Separator className="my-4" />
              <Body
                node={node}
                reference={reference}
                row={row}
                transcript={transcript}
              />
            </>
          )}
        </div>
      </ScrollArea>
    </section>
  );
}

type Transcript = ReturnType<typeof useConversation>;
type Turn = DagConversation["conversation"]["turns"][number];

function Body({
  row,
  node,
  reference,
  transcript,
}: {
  readonly row: TimelineRow;
  readonly node: NodeView;
  readonly reference?: TimelineReference;
  readonly transcript: Transcript;
}) {
  if (reference?.kind === "conversation")
    return <Session row={row} transcript={transcript} />;
  if (isVerification(row))
    return <Verification node={node} reference={reference} row={row} />;
  if (isPublication(row, reference))
    return <Publication node={node} reference={reference} />;
  return <Recorded reference={reference} row={row} />;
}

/** A dispatched session: the turn that was opened, or the whole conversation. */
function Session({
  row,
  transcript,
}: {
  readonly row: TimelineRow;
  readonly transcript: Transcript;
}) {
  const [visible, setVisible] = useState(PAGE_SIZE);
  if (transcript.loading)
    return (
      <div aria-live="polite" className="loading-inline">
        <Skeleton className="h-2 w-40" />
        <Skeleton className="h-2 w-24" />
        Loading transcript…
      </div>
    );
  // llmlint: ignore[changed_behavior_has_e2e] a conforming server cannot produce this state — it names a transcript in a timeline it just served, so a transcript it then refuses only comes from a peer that raced or broke between the two reads. App.test.tsx proves it against the real telemetry client at its browser boundary.
  if (transcript.error !== undefined || transcript.conversation === undefined)
    return (
      <Alert variant="destructive">
        <TriangleAlert />
        <AlertTitle>Transcript unavailable</AlertTitle>
        <AlertDescription>
          {transcript.error?.message ?? "The server recorded no transcript."}
        </AlertDescription>
      </Alert>
    );
  const { conversation, attribution } = transcript.conversation;
  const turns: readonly Turn[] =
    row.rowKind === "event"
      ? conversation.turns.filter(({ id }) => id === row.event.id)
      : conversation.turns;
  // The turns sit on the design system's own card surface rather than straight on
  // the page: `TurnCard` paints its own bubbles and nothing behind them.
  return (
    <Card className="gap-0 py-3">
      <CardContent className="px-3">
        <header className="transcript-header">
          <div>
            <p className="eyebrow">
              {roleLabel(attribution.agentRole, attribution.transportRole)}
              {attribution.persona ? ` · ${attribution.persona}` : ""}
            </p>
            <h4>{conversation.name}</h4>
          </div>
          <StatusBadge state={conversation.state} />
        </header>
        <Separator className="my-2.5" />
        {/* llmlint: ignore[changed_behavior_has_e2e] the same unproducible state as
            above, from the other side: a served timeline names this turn, so only a
            history store rewritten between the two reads drops it. */}
        {turns.length === 0 ? (
          <p className="detail-note">
            This turn is no longer part of the recorded transcript.
          </p>
        ) : (
          <>
            {turns.slice(0, visible).map((turn) => (
              <TurnCard key={turn.id} turn={turn} />
            ))}
            {turns.length > visible && (
              <Button
                onClick={() => setVisible(visible + PAGE_SIZE)}
                size="sm"
                type="button"
                variant="outline"
              >
                Show more of {turns.length} turns
              </Button>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function Verification({
  row,
  node,
  reference,
}: {
  readonly row: TimelineRow;
  readonly node: NodeView;
  readonly reference?: TimelineReference;
}) {
  const attestation = node.telemetry?.gate_attestation;
  return (
    <>
      <h3 className="detail-heading">Gate attestation</h3>
      {attestation === undefined ? (
        <p className="detail-note">
          This verification recorded no gate attestation.
        </p>
      ) : (
        <dl className="facts">
          {Object.entries(attestation).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <h3 className="detail-heading">Log</h3>
      <Reference fallback="No log was recorded." reference={reference} />
      <p className="detail-note">Verification {row.status ?? "in progress"}.</p>
    </>
  );
}

function Publication({
  node,
  reference,
}: {
  readonly node: NodeView;
  readonly reference?: TimelineReference;
}) {
  const checks = observedChecks(node);
  return (
    <>
      <h3 className="detail-heading">Pull request</h3>
      <Reference
        fallback="No pull request was recorded."
        reference={reference}
      />
      <h3 className="detail-heading">Observed checks</h3>
      {checks.length === 0 ? (
        <p className="detail-note">No checks were observed on this node.</p>
      ) : (
        <dl className="facts">
          {checks.map(([name, state]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{state}</dd>
            </div>
          ))}
        </dl>
      )}
    </>
  );
}

/** Whatever the timeline recorded, in the fields the contract gives it. */
function Recorded({
  row,
  reference,
}: {
  readonly row: TimelineRow;
  readonly reference?: TimelineReference;
}) {
  const located = row.rowKind === "group" ? undefined : row;
  const rollup =
    row.rowKind === "span" && row.span.count !== undefined
      ? `${row.span.count} records`
      : undefined;
  return (
    <>
      <dl className="facts">
        <div>
          <dt>Recorded at</dt>
          <dd>{row.startedAt}</dd>
        </div>
        <div>
          <dt>Ended</dt>
          <dd>{row.endedAt ?? "Still running"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{row.status ?? "Not recorded"}</dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>
            {row.durationMs === null
              ? "Not recorded"
              : formatDuration(row.durationMs)}
          </dd>
        </div>
        {rollup !== undefined && (
          <div>
            <dt>Aggregated</dt>
            <dd>{rollup}</dd>
          </div>
        )}
        <div>
          <dt>Step</dt>
          <dd>{stepOf(located) ?? "None"}</dd>
        </div>
      </dl>
      <h3 className="detail-heading">Reference</h3>
      <Reference
        fallback="This item points at no recorded artifact."
        reference={reference}
      />
    </>
  );
}

function Reference({
  reference,
  fallback,
}: {
  readonly reference?: TimelineReference;
  readonly fallback: string;
}) {
  if (reference === undefined) return <p className="detail-note">{fallback}</p>;
  if (reference.value.startsWith("http"))
    return (
      <p className="detail-link">
        <a href={reference.value} rel="noreferrer" target="_blank">
          {reference.value} <ExternalLink size={13} />
        </a>
      </p>
    );
  return (
    <p className="detail-note">
      <span className="detail-reference-kind">{reference.kind}</span>
      <code>{reference.value}</code>
    </p>
  );
}

function referenceOf(row: TimelineRow): TimelineReference | undefined {
  if (row.rowKind === "span") return row.span.reference;
  if (row.rowKind === "event") return row.event.reference;
  return undefined;
}

function stepOf(row?: TimelineRow): string | undefined {
  if (row?.rowKind === "span") return row.span.step_id;
  if (row?.rowKind === "event") return row.event.step_id;
  return undefined;
}

function isVerification(row: TimelineRow): boolean {
  return row.kind === "verification" || row.kind.startsWith("verification-");
}

function isPublication(
  row: TimelineRow,
  reference?: TimelineReference,
): boolean {
  return (
    reference?.kind === "pr" ||
    row.kind === "publication" ||
    row.kind.startsWith("pr-") ||
    row.kind.startsWith("publication-")
  );
}

/** The checks the node's own result recorded, as observed name/state pairs. */
function observedChecks(node: NodeView): [string, string][] {
  const checks = node.result?.telemetry?.checks;
  if (typeof checks !== "object" || checks === null || Array.isArray(checks))
    return [];
  return Object.entries(checks).map(([name, state]) => [
    name,
    formatValue(state),
  ]);
}

type Attribution = DagConversation["attribution"];
type AgentRole = Attribution["agentRole"];

/**
 * Every semantic role the contract's closed `agentRole` enum admits.
 *
 * Keyed by that enum rather than by `string`: the vocabulary is reconciled across
 * `orchestrator/labels.py`, the `dag-model` enum and the design contract by
 * `scripts/check-dag-state-contract.py`, so a role added there reaches this record
 * and fails to compile until it is given a word — rather than rendering as its own
 * raw identifier.
 */
const ROLE_LABELS: Readonly<Record<AgentRole, string>> = {
  worker: "Worker",
  judge: "Judge",
  "check-in": "Check-in",
  "pr-author": "PR author",
  orchestrator: "Orchestrator",
};

function roleLabel(
  agentRole: AgentRole,
  transportRole: Attribution["transportRole"],
): string {
  // Nested lint work is grouped under its worker, so its transport is what names it.
  return transportRole === "llmlint" ? "Lint" : ROLE_LABELS[agentRole];
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === "")
    return "Not recorded";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
