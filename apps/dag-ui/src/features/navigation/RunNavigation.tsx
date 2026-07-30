import {
  ScrollArea,
  Separator,
  StatusBadge,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@oneharness/ui";
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
    <nav aria-label="DAG runs" className="run-nav">
      <ScrollArea className="h-full">
        <div className="px-[18px] py-6">
          <div className="brand">
            <div aria-hidden="true" className="brand-mark">
              <Activity size={20} />
            </div>
            <div>
              <p className="eyebrow">Local orchestration</p>
              <h1>DAG Observatory</h1>
            </div>
          </div>
          <Separator className="my-[22px]" />
          <div className="run-groups">
            {groups.map((group) => (
              <section aria-labelledby={`group-${group.id}`} key={group.id}>
                <h2 id={`group-${group.id}`}>
                  <Bot aria-hidden="true" size={14} />
                  {group.label}
                </h2>
                {group.runs.map((run) => {
                  const active = selectedRunId === run.run_id;
                  return (
                    <button
                      aria-current={active ? "page" : undefined}
                      className="run-link"
                      data-active={active}
                      key={run.run_id}
                      onClick={() => onSelect(run.run_id)}
                      type="button"
                    >
                      <span className="run-link-main">
                        {liveRunIds.has(run.run_id) ? (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              {/* The dot is the only marker of a live run, so it
                                  keeps a name of its own rather than relying on the
                                  hover-only tooltip to carry that meaning. */}
                              <span
                                aria-label="Live"
                                className="live-dot"
                                role="img"
                              />
                            </TooltipTrigger>
                            <TooltipContent>Live</TooltipContent>
                          </Tooltip>
                        ) : (
                          <History aria-label="Historical" size={13} />
                        )}
                        <span>{run.run_id}</span>
                      </span>
                      <StatusBadge state={run.state} />
                      <ChevronRight aria-hidden="true" size={14} />
                    </button>
                  );
                })}
              </section>
            ))}
          </div>
        </div>
      </ScrollArea>
    </nav>
  );
}
