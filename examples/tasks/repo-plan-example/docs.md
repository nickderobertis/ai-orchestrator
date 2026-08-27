---
title: "docs(api): document /health"
project: "repo-plan-example"
status: "todo"
repositories: ["github.com/nickderobertis/some-docs"]
metadata:
  "onepipeline.id": "docs"
  "onepipeline.repo_type": "team"
  "onepipeline.merge_policy": "change-auto"
  "onepipeline.persona": "docs-writer"
---

## What
Document the new `/health` endpoint: its request, its response, and worked examples.

## Why
Operators are being asked to poll an endpoint that appears in no documentation, so each of them has to read the service source to learn its response shape.

## Acceptance criteria
- The request and response are documented with field names and types.
- Every example was run against the API and behaves as written.
- The documented contract matches the endpoint as implemented, not as proposed.
