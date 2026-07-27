import { Activity, Bot, ChevronRight, History } from "lucide-react";
import type { RunGroup } from "../runs/run-model";

export function RunNavigation({
  groups,
  selectedRunId,
  liveRunIds,
  onSelect,
}: {
  readonly groups: readonly RunGroup[];
  readonly selectedRunId?: string;
  readonly liveRunIds: ReadonlySet<string>;
  readonly onSelect: (runId: string) => void;
}) {
  return (
    <nav className="run-nav" aria-label="DAG runs">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          <Activity size={20} />
        </div>
        <div>
          <p className="eyebrow">Local orchestration</p>
          <h1>DAG Observatory</h1>
        </div>
      </div>
      <div className="run-groups">
        {groups.map((group) => (
          <section key={group.id} aria-labelledby={`group-${group.id}`}>
            <h2 id={`group-${group.id}`}>
              <Bot size={14} aria-hidden="true" />
              {group.label}
            </h2>
            {group.runs.map((run) => {
              const active = selectedRunId === run.run_id;
              const isLive = liveRunIds.has(run.run_id);
              return (
                <button
                  className="run-link"
                  data-active={active}
                  key={run.run_id}
                  type="button"
                  aria-current={active ? "page" : undefined}
                  onClick={() => onSelect(run.run_id)}
                >
                  <span className="run-link-main">
                    {isLive ? (
                      <span className="live-dot" title="Live" />
                    ) : (
                      <History size={13} aria-label="Historical" />
                    )}
                    <span>{run.run_id}</span>
                  </span>
                  <span className={`status status-${run.state}`}>
                    {run.state}
                  </span>
                  <ChevronRight size={14} aria-hidden="true" />
                </button>
              );
            })}
          </section>
        ))}
      </div>
    </nav>
  );
}
