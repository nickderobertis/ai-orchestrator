---
title: 'Design: health-endpoint'
project: health-endpoint
metadata:
  onetaskgraph.origin: orchestrator-record-staging:5697e51426246121fbc2486ca79881d3fbc11590f7b984c7514a0c1b5100a474
  orchestrator.design-approval:
    approved_at: 2026-09-09T04:37:50.813208+00:00
    key: 87784f2be137cca6c4eda29d029cd43a873cf3e8474e323df8875e798a1460ad
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

**The endpoint's response.** `GET /health` answers a fixed shape, and anything that polls
it — a load balancer, a monitor, a person — depends on that shape rather than on the code
behind it. Adding a field later is safe; renaming or removing one is not.

## Acceptance criteria

- Polling the endpoint says whether the service is up.
- Tests drive real requests rather than asserting on internal state.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| feat(api): add /health endpoint | the endpoint and its tests | none | examples/tasks/health-endpoint/health.md |
