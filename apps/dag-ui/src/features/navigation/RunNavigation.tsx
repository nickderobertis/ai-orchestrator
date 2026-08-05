import {
  ScrollArea,
  Separator,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@oneharness/ui";
import { Activity, Bot, ChevronRight, History } from "lucide-react";
import { nodeCountSummary, type RunGroup } from "../runs/run-model";
import { StateBadge } from "../runs/StateBadge";

export function RunNavigation({
  groups,
  selectedRunId,
  liveRunIds,
  onSelect,
  hasMore,
  onLoadMore,
}: {
  readonly groups: readonly RunGroup[];
  readonly selectedRunId?: string;
  readonly liveRunIds: ReadonlySet<string>;
  readonly onSelect: (runId: string) => void;
  readonly hasMore: boolean;
  readonly onLoadMore: () => Promise<void>;
}) {
  return (
    <nav
      aria-label="DAG runs"
      className="run-nav"
      onScrollCapture={(event) => {
        // React exposes EventTarget here although this handler can only receive a
        // scroll event from an HTMLElement inside the navigation subtree.
        const target = event.target as HTMLElement;
        if (
          hasMore &&
          target.scrollHeight - target.scrollTop <= target.clientHeight + 80
        )
          void onLoadMore();
      }}
    >
      <ScrollArea className="h-full">
        <div className="px-[18px] py-6 max-sm:px-2">
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
                  const counts = nodeCountSummary(run.node_counts);
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
                      {/* This column is the one place a full-size pill would crowd
                          the run id beside it out of the row. */}
                      <StateBadge
                        className="px-[5px] py-px text-[9px]"
                        state={run.state}
                      />
                      <ChevronRight aria-hidden="true" size={14} />
                      {/* The run's own nodes, in the words the graph paints them
                          with. A run row that stated only "running" said nothing
                          about the node inside it that was already blocked. */}
                      {counts !== "" && (
                        <span className="run-link-counts" title={counts}>
                          {counts}
                        </span>
                      )}
                    </button>
                  );
                })}
              </section>
            ))}
          </div>
          {hasMore && (
            <button
              className="sr-only"
              onClick={() => void onLoadMore()}
              type="button"
            >
              Load more runs
            </button>
          )}
        </div>
      </ScrollArea>
    </nav>
  );
}
