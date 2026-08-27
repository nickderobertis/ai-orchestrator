---
title: "docs"
project: "plan-example"
status: "todo"
depends_on: ["plan-example/api", "plan-example/ui"]
metadata:
  "onepipeline.id": "docs"
  "onepipeline.persona": "docs-writer"
---

## What
Write the README for the URL shortener: what it is, how to run it, and the API surface.

## Why
A reader arriving at the repository has no account of what the service does or how to start it, so every one of them has to reconstruct it from the code.

## Acceptance criteria
- The README says what the service is, how to run it, and what its API surface is.
- Every command in it was run against this checkout and behaves as written.
- Every documented request and response matches the implemented endpoints.
