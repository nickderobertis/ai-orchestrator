import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * The read API this dev server proxies. It defaults to the loopback address and
 * port `orchestrator/server.py` binds, and the browser e2e overrides it to point at
 * the throwaway fixture server it starts instead of the operator's own runs.
 */
const apiTarget = process.env.DAG_UI_API_URL ?? "http://127.0.0.1:8787";

export default defineConfig({
  root: import.meta.dirname,
  plugins: [react(), tailwindcss()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 4173,
    proxy: {
      "/api": apiTarget,
      "/healthz": apiTarget,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: true,
  },
});
