---
title: "api"
project: "plan-example"
status: "todo"
depends_on: ["plan-example/design"]
metadata:
  "onepipeline.id": "api"
  "onepipeline.persona": "engineer"
---

## What
Implement the URL-shortener HTTP API per `docs/design.md`: the create and resolve endpoints, validation, and persistence. Cover the endpoints with tests that drive real requests.

## Why
The service has no working create-or-resolve path yet, so nothing else in the product can be exercised end to end until this one exists.

## Acceptance criteria
- Create and resolve behave as `docs/design.md` specifies, proven by tests that drive real HTTP requests.
- Invalid input is rejected rather than persisted, and a test proves it.
- Persistence is covered by a test that reads back what a create wrote.
