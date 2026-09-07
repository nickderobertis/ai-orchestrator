---
title: 'Design: health-endpoint'
project: health-endpoint
metadata:
  onetaskgraph.origin: orchestrator-record-staging:5697e51426246121fbc2486ca79881d3fbc11590f7b984c7514a0c1b5100a474
  orchestrator.design-approval:
    approved_at: 2026-09-06T10:33:49.179824+00:00
    key: 39fc71de845c6a2a67b50840b3dfa79f2fa6c2f4369ac4ab9695ab96502efafd
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
