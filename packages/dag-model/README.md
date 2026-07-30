# DAG model

Zod schemas and inferred TypeScript types for the `/api/v1` telemetry read API.
Parse untrusted JSON with the exported schemas (or `parseRunList` /
`parseRunDetail` / `parseRunTimeline`) before using it. Unknown additive fields
are preserved by the API object schemas.
