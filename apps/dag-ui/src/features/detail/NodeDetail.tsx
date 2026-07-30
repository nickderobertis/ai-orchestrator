import { Badge, Button, Card, CardContent, ScrollArea } from "@oneharness/ui";
import {
  ExternalLink,
  FileText,
  GitPullRequest,
  ScrollText,
} from "lucide-react";
import type { NodeView } from "../runs/run-model";
import { StateBadge } from "../runs/StateBadge";
import { TranscriptPanel } from "./TranscriptPanel";

export function NodeDetail({
  node,
  onClose,
}: {
  readonly node: NodeView;
  readonly onClose: () => void;
}) {
  // `task` is required by the read contract, so it always has text; completion
  // criteria are optional — a human action names work for a person, not a bar the
  // harness can check — and the panel says so rather than showing an empty block.
  const task = node.task.task;
  const doneWhen = node.task.done_when ?? "No completion criteria recorded.";
  // Each field is the one the read contract defines for it: the node result carries
  // the published PR and the outcome detail a run writes as its log line, and the
  // gate verdict is the attestation the node's verification recorded.
  const pr = node.result?.pr;
  const gate = node.telemetry?.gate_attestation;
  const logs = node.result?.detail;

  return (
    <aside aria-label={`Details for ${node.label}`} className="detail-panel">
      <ScrollArea className="h-full">
        <div className="p-[22px]">
          <header className="detail-title">
            <div>
              <Badge className="mb-1" variant="outline">
                {node.kind} node
              </Badge>
              <h2>{node.label}</h2>
            </div>
            <Button onClick={onClose} size="sm" type="button" variant="ghost">
              Close <span aria-hidden="true">×</span>
            </Button>
          </header>

          <Card className="my-5 flex-row items-center gap-3 p-3.5">
            <StateBadge state={node.state} />
            <div>
              <p className="eyebrow">Progress</p>
              <p className="m-0 text-[11px] text-muted-foreground">
                {node.telemetry?.turns ?? 0} turns · {node.telemetry?.lint ?? 0}{" "}
                lint turns
              </p>
            </div>
          </Card>

          <DetailSection icon={<FileText size={16} />} title="Task">
            <pre>{task}</pre>
          </DetailSection>
          <DetailSection icon={<FileText size={16} />} title="Done when">
            <pre>{doneWhen}</pre>
          </DetailSection>

          <dl className="facts">
            <div>
              <dt>Dependencies</dt>
              <dd>{node.task.deps?.join(", ") || "None"}</dd>
            </div>
            <div>
              <dt>Gate result</dt>
              <dd>{formatValue(gate)}</dd>
            </div>
          </dl>

          <DetailSection
            icon={<GitPullRequest size={16} />}
            title="Pull request"
          >
            {typeof pr === "string" ? (
              <a href={pr} rel="noreferrer" target="_blank">
                {pr} <ExternalLink size={13} />
              </a>
            ) : (
              <p>{formatValue(pr)}</p>
            )}
          </DetailSection>

          <DetailSection icon={<ScrollText size={16} />} title="Logs">
            <pre>{formatValue(logs)}</pre>
          </DetailSection>

          <DetailSection icon={<ScrollText size={16} />} title="Agent history">
            <TranscriptPanel conversations={node.conversations} />
          </DetailSection>
        </div>
      </ScrollArea>
    </aside>
  );
}

function DetailSection({
  icon,
  title,
  children,
}: {
  readonly icon: React.ReactNode;
  readonly title: string;
  readonly children: React.ReactNode;
}) {
  return (
    <Card className="detail-section mt-4 gap-0 py-[15px]">
      <CardContent className="px-[15px]">
        <div className="section-heading">
          {icon}
          <h3>{title}</h3>
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === "")
    return "Not recorded";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
