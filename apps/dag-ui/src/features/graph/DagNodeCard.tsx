import type {
  DagNodeState,
  StatusStyleToken,
} from "@ai-orchestrator/dag-layout";
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { Check, CircleDashed, Clock3, X } from "lucide-react";

export interface DagNodeData extends Record<string, unknown> {
  readonly label: string;
  readonly kind: string;
  readonly state: DagNodeState;
  readonly style: StatusStyleToken;
  readonly selected: boolean;
}

export type DagFlowNode = Node<DagNodeData, "dagNode">;

export function DagNodeCard({ data }: NodeProps<DagFlowNode>) {
  const Icon =
    data.style === "success"
      ? Check
      : data.style === "danger" || data.style === "muted"
        ? X
        : data.style === "active"
          ? CircleDashed
          : Clock3;
  return (
    <div
      className={`dag-node state-${data.state} token-${data.style}`}
      data-selected={data.selected}
    >
      <Handle type="target" position={Position.Left} />
      <div className="node-heading">
        <span className="node-kind">{data.kind}</span>
        <Icon className={data.style === "active" ? "spin" : ""} size={17} />
      </div>
      <strong>{data.label}</strong>
      <span className="node-status">{data.state}</span>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
