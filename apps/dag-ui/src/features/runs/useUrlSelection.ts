import { useCallback, useSyncExternalStore } from "react";

/**
 * The whole drill-down, held in the query string: which run, which node, which view,
 * and which moment of that node's recorded execution. One mechanism, so every one of
 * them is bookmarkable and every one of them survives a back button.
 */
export interface UrlSelection {
  readonly runId?: string;
  readonly nodeId?: string;
  /**
   * The timeline item opened in the node view, by its recorded id.
   *
   * A span is as linkable as an event — a dispatch and one of its turns are both
   * moments of the node's execution — so this is an item id, not an event id. The
   * query key stays `event` because that is the shared address already in use.
   */
  readonly itemId?: string;
  /**
   * Which reading of the run is open.
   *
   * An address that names neither a view nor a node lands on `overall`: a run is
   * read as a whole first, and the graph is where a reader goes to open one node of
   * it. A link that does name a node is already asking for that node, so it opens
   * where the node view lives.
   */
  readonly view: "graph" | "overall";
  readonly nodeTab: NodeTab;
  readonly selectRun: (runId: string) => void;
  readonly selectNode: (nodeId?: string) => void;
  readonly selectItem: (itemId?: string) => void;
  readonly showOverall: () => void;
  readonly selectNodeTab: (tab: NodeTab) => void;
}

export const NODE_TAB_LABELS = {
  timeline: "Timeline",
  task: "Task",
  criteria: "Completion criteria",
  dependencies: "Dependencies",
  pr: "PR",
  checks: "Checks",
};
export type NodeTab = keyof typeof NODE_TAB_LABELS;

export function isNodeTab(value: string | null): value is NodeTab {
  return value !== null && Object.hasOwn(NODE_TAB_LABELS, value);
}

export function useUrlSelection(): UrlSelection {
  const query = useSyncExternalStore(subscribe, currentQuery, currentQuery);
  const params = new URLSearchParams(query);
  const runId = params.get("run") ?? undefined;
  const nodeId = params.get("node") ?? undefined;
  const itemId = params.get("event") ?? undefined;
  const named = params.get("view");
  const namedTab = params.get("tab");
  const nodeTab = isNodeTab(namedTab) ? namedTab : "timeline";
  const view: UrlSelection["view"] =
    named === "graph" || named === "overall"
      ? named
      : nodeId === undefined
        ? "overall"
        : "graph";

  const update = useCallback((change: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(window.location.search);
    change(next);
    window.history.pushState(null, "", `${window.location.pathname}?${next}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, []);

  return {
    runId,
    nodeId,
    itemId,
    view,
    nodeTab,
    // The reading stays where it was: an operator comparing two runs on the overall
    // view is not asking to be moved to the graph by picking the second one.
    selectRun: (id) =>
      update((next) => {
        next.set("run", id);
        next.delete("node");
        next.delete("event");
        next.delete("tab");
      }),
    // A different node has different recorded work, so the moment selected inside the
    // one being left cannot survive the move. Both a node and the way back out of one
    // live in the graph view, so this names it rather than falling back to the
    // landing view.
    selectNode: (id) =>
      update((next) => {
        if (id) next.set("node", id);
        else next.delete("node");
        next.delete("event");
        next.delete("tab");
        next.set("view", "graph");
      }),
    selectItem: (id) =>
      update((next) => {
        if (id) next.set("event", id);
        else next.delete("event");
      }),
    showOverall: () =>
      update((next) => {
        next.set("view", "overall");
        next.delete("node");
        next.delete("event");
        next.delete("tab");
      }),
    selectNodeTab: (tab) =>
      update((next) => {
        if (tab === "timeline") next.delete("tab");
        else next.set("tab", tab);
        if (tab !== "timeline") next.delete("event");
      }),
  };
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  return () => window.removeEventListener("popstate", onChange);
}

function currentQuery(): string {
  return window.location.search;
}
