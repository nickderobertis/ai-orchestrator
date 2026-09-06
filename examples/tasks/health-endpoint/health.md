---
title: "feat(api): add /health endpoint"
project: "health-endpoint"
status: "todo"
repositories: ["github.com/nickderobertis/some-service"]
metadata:
  "onepipeline.id": "health"
  "onepipeline.repo_type": "team"
  "onepipeline.persona": "engineer"
---

## What
Add a `GET /health` endpoint that returns build metadata, with tests that drive real requests.

## Why
Operators currently learn a deploy is unhealthy from user reports, because nothing exposes build metadata to poll.

## Acceptance criteria
- `GET /health` returns the build metadata contract.
- Real request tests prove the happy path and one failure path.
- Each new test's subject is the behaviour this change adds, so removing that behaviour fails it.
- The change opens no coverage gap.
