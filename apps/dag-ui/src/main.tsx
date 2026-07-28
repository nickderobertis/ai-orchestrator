import "@xyflow/react/dist/style.css";
import "./styles.css";
import transcriptStyles from "@oneharness/ui/styles.css?raw";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { AppErrorBoundary } from "./app/AppErrorBoundary";

const root = document.getElementById("root");
if (!root) throw new Error("DAG UI root element is missing");

const transcriptTheme = document.createElement("style");
transcriptTheme.dataset.oneharnessTheme = "true";
transcriptTheme.textContent = transcriptStyles;
document.head.append(transcriptTheme);

createRoot(root).render(
  <StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </StrictMode>,
);
