# Telemetry client

Typed fetch and server-sent-event access to the read-only telemetry API. Every
JSON response and SSE payload is validated with `@ai-orchestrator/dag-model`
before callbacks or callers receive it.

Construct `TelemetryClient` with the server origin, call `listRuns()`,
`getRun()`, or `getTimeline()`, and close the handle returned by `subscribe()`
when finished.

For a live view, prefer `getTimeline(runId)` beside
`getRun(runId, {includeConversations: false})`: transcripts dominate the detail
payload and are refetched on every invalidation, while the timeline carries only
references to them and one conversation stays reachable via `getConversation()`.
