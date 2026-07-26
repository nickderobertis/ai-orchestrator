# Telemetry client

Typed fetch and server-sent-event access to the read-only telemetry API. Every
JSON response and SSE payload is validated with `@ai-orchestrator/dag-model`
before callbacks or callers receive it.

Construct `TelemetryClient` with the server origin, call `listRuns()` or
`getRun()`, and close the handle returned by `subscribe()` when finished.
