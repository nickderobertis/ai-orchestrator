---
title: "design"
project: "plan-example"
status: "todo"
metadata:
  "onepipeline.id": "design"
  "onepipeline.persona": "planner"
---

## What
Design a URL-shortener service and write the design to `docs/design.md`: the HTTP API (create + resolve), the storage schema, and the module boundaries the api and ui subtasks build against.

## Why
The api and ui subtasks are meant to run in parallel, and without one written contract between them each would invent its own and the two would have to be reconciled afterwards.

## Acceptance criteria
- `docs/design.md` states the create and resolve request and response shapes with field names and types.
- It states the storage schema and the module boundary each subtask builds against.
- Every boundary it names is one a caller can code against without reading the implementation.
- The api and ui subtasks can proceed in parallel from this document alone.
