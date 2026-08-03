import type {
  DagNodeState,
  StatusStyleToken,
} from "@ai-orchestrator/dag-layout";
import { Badge, cn } from "@oneharness/ui";
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { Check, CircleDashed, Clock3, X } from "lucide-react";

export interface DagNodeData extends Record<string, unknown> {
  readonly label: string;
  readonly kind: string;
  readonly state: DagNodeState;
  readonly style: StatusStyleToken;
  readonly selected: boolean;
  /**
   * Why this node is not making progress, when it is not. One line: the card is
   * 200×72 pixels, and the node view is one click away for the rest of it. Absent
   * for a node with nothing to report.
   */
  readonly reason?: string;
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
      className={cn("dag-node", `state-${data.state}`, `token-${data.style}`)}
      data-selected={data.selected}
    >
      <Handle type="target" position={Position.Left} />
      <div className="node-heading">
        <Badge
          className="px-1.5 py-0 text-[9px] tracking-[.1em] uppercase"
          variant="outline"
        >
          {data.kind}
        </Badge>
        <Icon className={cn(data.style === "active" && "spin")} size={17} />
      </div>
      <strong>{data.label}</strong>
      <span className="node-status">{data.state}</span>
      {/* The state word alone tells an operator that something is wrong and nothing
          about what; the reason is what turns a red graph into a diagnosis without
          opening every card in it. `title` carries the untruncated text. */}
      {data.reason !== undefined && (
        <span className="node-reason" title={data.reason}>
          {data.reason}
        </span>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
