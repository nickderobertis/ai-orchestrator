import { expect, test } from "bun:test";

// eslint-disable-next-line @nx/enforce-module-boundaries -- This verifies the package export over a real HTTP boundary.
import { TelemetryClient } from "@ai-orchestrator/telemetry-client";

test("a package consumer reads a validated response from a real HTTP server", async () => {
  const server = Bun.serve({
    port: 0,
    fetch(request) {
      const url = new URL(request.url);
      if (
        url.pathname === "/api/v1/runs" &&
        url.searchParams.get("include_settled") === "false"
      ) {
        return Response.json({
          api_version: 1,
          telemetry_schema_version: 7,
          observed_at: "2026-07-26T12:00:00Z",
          runs: [],
        });
      }
      return Response.json(
        { error: { code: "not_found", message: "Not found" } },
        { status: 404 },
      );
    },
  });
  try {
    const client = new TelemetryClient(`http://127.0.0.1:${server.port}`);
    expect((await client.listRuns()).runs).toEqual([]);
  } finally {
    await server.stop();
  }
});

test("a package consumer receives typed HTTP and response-contract failures", async () => {
  const server = Bun.serve({
    port: 0,
    fetch(request) {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/invalid")) {
        return Response.json({
          api_version: 1,
          telemetry_schema_version: 7,
          observed_at: "2026-07-26T12:00:00Z",
          run: {},
          rounds: [],
          conversations: [],
        });
      }
      return Response.json(
        { error: { code: "not_found", message: "Run is missing" } },
        { status: 404 },
      );
    },
  });
  try {
    const client = new TelemetryClient(`http://127.0.0.1:${server.port}`);
    await expect(client.getRun("invalid")).rejects.toMatchObject({
      message: "Telemetry response failed contract validation",
    });
    await expect(client.getRun("missing")).rejects.toMatchObject({
      status: 404,
      code: "not_found",
      message: "Run is missing",
    });
  } finally {
    await server.stop();
  }
});

test("a package consumer reads and validates one complete conversation", async () => {
  const conversation = {
    conversation: {
      canContinue: false,
      harnesses: ["codex"],
      id: "conversation-1",
      name: "Worker",
      project: "repo",
      startedAt: "2026-07-26T12:00:00Z",
      state: "completed",
      turns: [],
    },
    attribution: {
      runId: "run-1",
      transportRole: "agent",
      agentRole: "worker",
    },
  };
  const server = Bun.serve({
    port: 0,
    fetch(request) {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/conversations/conversation-1")) {
        return Response.json(conversation);
      }
      if (path.endsWith("/conversations/invalid")) {
        return Response.json({ conversation: { id: 7 } });
      }
      return Response.json(
        { error: { code: "not_found", message: "Conversation is missing" } },
        { status: 404 },
      );
    },
  });
  try {
    const client = new TelemetryClient(`http://127.0.0.1:${server.port}`);
    expect(
      (await client.getConversation("run-1", "conversation-1")).conversation.id,
    ).toBe("conversation-1");
    await expect(
      client.getConversation("run-1", "invalid"),
    ).rejects.toMatchObject({
      message: "Telemetry response failed contract validation",
    });
    await expect(
      client.getConversation("run-1", "missing"),
    ).rejects.toMatchObject({
      status: 404,
      code: "not_found",
    });
  } finally {
    await server.stop();
  }
});
