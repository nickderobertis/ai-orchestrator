import { useCallback, useSyncExternalStore } from "react";

/**
 * The whole drill-down, held in the query string: which run, which node, which view,
 * and which moment of that node's recorded execution. One mechanism, so every one of
 * them is bookmarkable and every one of them survives a back button.
 */
export interface UrlSelection {
  readonly runId?: string;
  readonly nodeId?: string;
  /** The timeline span or event opened in the node view, by its recorded id. */
  readonly eventId?: string;
  readonly view: "graph" | "overall";
  readonly selectRun: (runId: string) => void;
  readonly selectNode: (nodeId?: string) => void;
  readonly selectEvent: (eventId?: string) => void;
  readonly showOverall: () => void;
}

export function useUrlSelection(): UrlSelection {
  const query = useSyncExternalStore(subscribe, currentQuery, currentQuery);
  const params = new URLSearchParams(query);
  const runId = params.get("run") ?? undefined;
  const nodeId = params.get("node") ?? undefined;
  const eventId = params.get("event") ?? undefined;
  const view = params.get("view") === "overall" ? "overall" : "graph";

  const update = useCallback((change: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(window.location.search);
    change(next);
    window.history.pushState(null, "", `${window.location.pathname}?${next}`);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, []);

  return {
    runId,
    nodeId,
    eventId,
    view,
    selectRun: (id) =>
      update((next) => {
        next.set("run", id);
        next.delete("node");
        next.delete("event");
        next.delete("view");
      }),
    // A different node has different recorded work, so the moment selected inside the
    // one being left cannot survive the move.
    selectNode: (id) =>
      update((next) => {
        if (id) next.set("node", id);
        else next.delete("node");
        next.delete("event");
        next.delete("view");
      }),
    selectEvent: (id) =>
      update((next) => {
        if (id) next.set("event", id);
        else next.delete("event");
      }),
    showOverall: () =>
      update((next) => {
        next.set("view", "overall");
        next.delete("node");
        next.delete("event");
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
