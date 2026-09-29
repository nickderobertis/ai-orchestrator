---
title: 'Design: health-endpoint'
project: health-endpoint
metadata:
  onetaskgraph.origin: orchestrator-record-staging:5697e51426246121fbc2486ca79881d3fbc11590f7b984c7514a0c1b5100a474
  "orchestrator.design-approval": {"approved_at":"2026-09-29T05:14:15.057770+00:00","key":"c0103dfddf347b2fb8405a16586d8b9e0037103811b508c48ac457b1071f8b3b"}
  "onetaskgraph.template": {"answers_digest":"sha256:20190b73eab0109d6159534d63fdaa729427e75ee795e149ceed7a94309e5e21","body_digest":"sha256:68e85660bc8248d52011943d5ae3be8ff6e38f9f517d36cee31886c67cf37557","digest":"sha256:ee358d23284193c8fe3b8a0c03f79076d50584241527f148c6e00c33a8146515","template":"onepipeline:design-doc"}
---
## What

A health endpoint on the service, so an operator can ask whether it is up without
reading logs.

## Why

Operators have no cheap way to answer "is it running". They read logs or open a shell,
which is slow at the moment it matters most.

## Architecture

One route on the existing service, answered from the service itself. No new process, no
new store, nothing else to deploy or watch.

## Contracts

- **The endpoint's response.** `GET /health` answers a fixed shape, and anything that polls
  it — a load balancer, a monitor, a person — depends on that shape rather than on the code
  behind it. Adding a field later is safe; renaming or removing one is not.

## Acceptance criteria

- Polling the endpoint says whether the service is up.
- Tests drive real requests rather than asserting on internal state.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| feat(api): add /health endpoint | the endpoint and its tests | none | examples/tasks/health-endpoint/health.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- Polling the endpoint says whether the service is up.
- Tests drive real requests rather than asserting on internal state.
architecture: |-
  One route on the existing service, answered from the service itself. No new process, no
  new store, nothing else to deploy or watch.
contracts:
- |-
  **The endpoint's response.** `GET /health` answers a fixed shape, and anything that polls
  it — a load balancer, a monitor, a person — depends on that shape rather than on the code
  behind it. Adding a field later is safe; renaming or removing one is not.
planned_tasks:
- delivers: the endpoint and its tests
  depends_on: none
  location: examples/tasks/health-endpoint/health.md
  task: 'feat(api): add /health endpoint'
what: |-
  A health endpoint on the service, so an operator can ask whether it is up without
  reading logs.
why: |-
  Operators have no cheap way to answer "is it running". They read logs or open a shell,
  which is slow at the moment it matters most.
-->
