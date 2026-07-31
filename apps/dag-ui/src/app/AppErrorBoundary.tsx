import { Button } from "@oneharness/ui";
import { Component, type ErrorInfo, type ReactNode } from "react";

// llmlint: ignore-file[changed_behavior_has_e2e] Reaching this boundary needs a graph
// the renderer refuses, and the read API cannot serve one: the executor's journal
// writers reject a cycle, a duplicate id, and a dangling edge before they are ever
// recorded, so the browser suite's real server has no way to produce the input. The
// closest honest test is the one in App.test.tsx, which drives the whole real app and
// its real telemetry client into this boundary with a cyclic detail payload.

export class AppErrorBoundary extends Component<
  { readonly children: ReactNode; readonly onReload?: () => void },
  { readonly error?: Error }
> {
  state: { error?: Error } = {};

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("DAG UI render failed", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="fatal-error" role="alert">
          <p className="eyebrow">Rendering error</p>
          <h1>The DAG view could not be displayed.</h1>
          <p>{this.state.error.message}</p>
          <Button
            onClick={() =>
              (this.props.onReload ?? (() => window.location.reload()))()
            }
            type="button"
            variant="secondary"
          >
            Reload
          </Button>
        </main>
      );
    }
    return this.props.children;
  }
}
