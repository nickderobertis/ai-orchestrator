---
title: 'Design: health-endpoint'
project: health-endpoint
metadata:
  onetaskgraph.origin: orchestrator-record-staging:health-endpoint-design
  orchestrator.design-approval:
    approved_at: 2026-09-01T15:56:31.253222+00:00
    key: 0ae41e5572671f2910beb98772403fbdaaa1a2f7255dbb7fe6db62a1521b2e33
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
