import { useCallback, useSyncExternalStore } from "react";

export interface UrlSelection {
  readonly runId?: string;
  readonly nodeId?: string;
  readonly view: "graph" | "overall";
  readonly selectRun: (runId: string) => void;
  readonly selectNode: (nodeId?: string) => void;
  readonly showOverall: () => void;
}

export function useUrlSelection(): UrlSelection {
  const query = useSyncExternalStore(subscribe, currentQuery, currentQuery);
  const params = new URLSearchParams(query);
  const runId = params.get("run") ?? undefined;
  const nodeId = params.get("node") ?? undefined;
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
    view,
    selectRun: (id) =>
      update((next) => {
        next.set("run", id);
        next.delete("node");
        next.delete("view");
      }),
    selectNode: (id) =>
      update((next) => {
        if (id) next.set("node", id);
        else next.delete("node");
        next.delete("view");
      }),
    showOverall: () =>
      update((next) => {
        next.set("view", "overall");
        next.delete("node");
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
