---
title: 'Design: plan-example'
project: plan-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:plan-example-design
  orchestrator.design-approval:
    approved_at: 2026-09-01T15:56:31.492184+00:00
    key: c21421a65964a4ffa7aef39b094a055f3c849da97fd02913a46ee188efb8ce4c
---
## What

A URL shortener: somewhere to turn a long link into a short one, and somewhere to follow
a short one back.

## Why

Links that have to be shared, typed, or printed are unusable at their full length, and
there is nothing here today that shortens them.

## Architecture

Three pieces, split so two of them can be built at once. One agent settles the design —
the API, the storage, and the line between the two halves — and the API and the web UI
are then built in parallel against it. Documentation and a review follow both.

## Contracts

**The HTTP API.** Two operations: create a short link, and resolve one. The UI calls it
and nothing else does, so what it accepts and answers is the agreement between the two
halves of this plan.

**The storage schema.** What a stored link is. It outlives every version of the code that
writes it, so this is the decision that costs most to reverse.

## Acceptance criteria

- A person can create a short link and follow it.
- The API and the UI hold to the design the first task writes.
- The README says what the service is, how to run it, and what its API is.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| design | the API, the storage, and the module boundaries | none | examples/tasks/plan-example/design.md |
| api | the create and resolve endpoints, with tests | design | examples/tasks/plan-example/api.md |
| ui | the page that creates a link and shows it | design | examples/tasks/plan-example/ui.md |
| docs | the README | api, ui | examples/tasks/plan-example/docs.md |
| review | verified, severity-ranked findings | api, ui | examples/tasks/plan-example/review.md |
