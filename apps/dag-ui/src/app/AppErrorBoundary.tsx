import { Component, type ErrorInfo, type ReactNode } from "react";

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
          <button
            type="button"
            onClick={() =>
              (this.props.onReload ?? (() => window.location.reload()))()
            }
          >
            Reload
          </button>
        </main>
      );
    }
    return this.props.children;
  }
}
