import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AppErrorBoundary } from "./AppErrorBoundary";

afterEach(cleanup);

test("offers reload recovery when a descendant cannot render", () => {
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  const caught = vi
    .spyOn(AppErrorBoundary.prototype, "componentDidCatch")
    .mockImplementation(() => {});
  const reload = vi.fn();

  function BrokenView(): never {
    throw new Error("invalid graph");
  }

  render(
    <AppErrorBoundary onReload={reload}>
      <BrokenView />
    </AppErrorBoundary>,
  );
  expect(screen.getByRole("alert", { name: "" })).toHaveTextContent(
    "The DAG view could not be displayed.",
  );
  expect(screen.getByText("invalid graph")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Reload" }));
  expect(reload).toHaveBeenCalledOnce();

  caught.mockRestore();
  consoleError.mockRestore();
});
