---
title: "feat(api): add /health endpoint"
project: "repo-plan-example"
status: "todo"
repositories: ["github.com/nickderobertis/some-service"]
metadata:
  "onepipeline.id": "api"
  "onepipeline.repo_type": "team"
  "onepipeline.persona": "engineer"
---

## What
Add a `GET /health` endpoint that returns build metadata, with tests that drive real requests. Keep the change scoped to the endpoint.

## Why
Operators currently learn a deploy is unhealthy from user reports, because nothing exposes build metadata to poll.

## Acceptance criteria
- `GET /health` returns the build metadata contract, proven by tests that drive real requests.
- One failure path is proven alongside the happy path.
- Nothing outside the endpoint and its tests changes.
