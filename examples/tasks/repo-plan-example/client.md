---
title: "feat(client): add health() method"
project: "repo-plan-example"
status: "todo"
repositories: ["github.com/nickderobertis/some-client"]
depends_on: ["repo-plan-example/api"]
metadata:
  "onepipeline.id": "client"
  "onepipeline.repo_type": "team"
  "onepipeline.persona": "engineer"
---

## What
Add a typed `health()` method to the client that calls `GET /health`, with a test against a real local server.

## Why
Every consumer of the new endpoint is otherwise left to hand-roll the request and its response type, which is where the contract drifts first.

## Acceptance criteria
- `health()` returns the build metadata as a declared type rather than an untyped mapping.
- A test drives it against a real local server rather than a mocked transport.
- One failure response is covered alongside the happy path.
