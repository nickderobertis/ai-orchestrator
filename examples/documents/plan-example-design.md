---
title: 'Design: plan-example'
project: plan-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:7543864a301acea3a06b63a9cda7d47b585511926f55b6d379566db4530ee0a8
  "orchestrator.design-approval": {"approved_at":"2026-09-29T05:14:15.276278+00:00","key":"0388c7a178ae99b91e9497f1e3da9ab56f388cf814066ddaa84863ea2f9fb5df"}
  "onetaskgraph.template": {"answers_digest":"sha256:1cfaad250196dc5e484f6a0b5f71597041f5d657c84047853812f2eb9dbd7a89","body_digest":"sha256:ab2291e5440d94097790302d6fa085735abb82dcfb21b35752f1c6eeb1cc819e","digest":"sha256:ee358d23284193c8fe3b8a0c03f79076d50584241527f148c6e00c33a8146515","template":"onepipeline:design-doc"}
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

- **The HTTP API.** Two operations: create a short link, and resolve one. The UI calls it
  and nothing else does, so what it accepts and answers is the agreement between the two
  halves of this plan.
- **The storage schema.** What a stored link is. It outlives every version of the code that
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


<!-- onetaskgraph:template-answers
acceptance_criteria:
- A person can create a short link and follow it.
- The API and the UI hold to the design the first task writes.
- The README says what the service is, how to run it, and what its API is.
architecture: |-
  Three pieces, split so two of them can be built at once. One agent settles the design —
  the API, the storage, and the line between the two halves — and the API and the web UI
  are then built in parallel against it. Documentation and a review follow both.
contracts:
- |-
  **The HTTP API.** Two operations: create a short link, and resolve one. The UI calls it
  and nothing else does, so what it accepts and answers is the agreement between the two
  halves of this plan.
- |-
  **The storage schema.** What a stored link is. It outlives every version of the code that
  writes it, so this is the decision that costs most to reverse.
planned_tasks:
- delivers: the API, the storage, and the module boundaries
  depends_on: none
  location: examples/tasks/plan-example/design.md
  task: design
- delivers: the create and resolve endpoints, with tests
  depends_on: design
  location: examples/tasks/plan-example/api.md
  task: api
- delivers: the page that creates a link and shows it
  depends_on: design
  location: examples/tasks/plan-example/ui.md
  task: ui
- delivers: the README
  depends_on: api, ui
  location: examples/tasks/plan-example/docs.md
  task: docs
- delivers: verified, severity-ranked findings
  depends_on: api, ui
  location: examples/tasks/plan-example/review.md
  task: review
what: |-
  A URL shortener: somewhere to turn a long link into a short one, and somewhere to follow
  a short one back.
why: |-
  Links that have to be shared, typed, or printed are unusable at their full length, and
  there is nothing here today that shortens them.
-->
