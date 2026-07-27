import {
  ExternalLink,
  FileText,
  GitPullRequest,
  ScrollText,
} from "lucide-react";
import type { NodeView } from "../runs/run-model";
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
    <aside className="detail-panel" aria-label={`Details for ${node.label}`}>
      <header className="detail-title">
        <div>
          <p className="eyebrow">{node.kind} node</p>
          <h2>{node.label}</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose}>
          Close <span aria-hidden="true">×</span>
        </button>
      </header>

      <section className="progress-card">
        <span className={`state-orb state-${node.state}`} aria-hidden="true" />
        <div>
          <p className="eyebrow">Progress</p>
          <strong>{node.state}</strong>
          <p>
            {node.telemetry?.turns ?? 0} turns · {node.telemetry?.lint ?? 0}{" "}
            lint turns
          </p>
        </div>
      </section>

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

      <DetailSection icon={<GitPullRequest size={16} />} title="Pull request">
        {typeof pr === "string" ? (
          <a href={pr} target="_blank" rel="noreferrer">
            {pr} <ExternalLink size={13} />
          </a>
        ) : (
          <p>{formatValue(pr)}</p>
        )}
      </DetailSection>

      <DetailSection icon={<ScrollText size={16} />} title="Logs">
        <pre>{formatValue(logs)}</pre>
      </DetailSection>

      <section className="detail-section transcript-section">
        <div className="section-heading">
          <ScrollText size={16} />
          <h3>Agent history</h3>
        </div>
        <TranscriptPanel conversations={node.conversations} />
      </section>
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
    <section className="detail-section">
      <div className="section-heading">
        {icon}
        <h3>{title}</h3>
      </div>
      {children}
    </section>
  );
}

function formatValue(value: unknown): string {
  if (value === undefined || value === null || value === "")
    return "Not recorded";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}
