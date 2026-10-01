---
title: 'Design: health-endpoint'
project: health-endpoint
metadata:
  onetaskgraph.origin: orchestrator-record-staging:5697e51426246121fbc2486ca79881d3fbc11590f7b984c7514a0c1b5100a474
  onetaskgraph.template:
    answers_digest: sha256:f17db7c9379795d40ab1fc7d1ce835db2388f6f0dcd2cdcfc4ae0bf710e1f73c
    body_digest: sha256:3094c1a12669c7e7f84b8d85a7a714c1bbcf5f0109d8cbf16d3e59541d5cac85
    digest: sha256:40ceac966dad3e35698a79077e2f0faaa59d9373ca5bb912dfde7ffa0469686d
    template: onepipeline:design-doc
  "orchestrator.design-approval": {"approved_at":"2026-10-01T13:24:51.383128+00:00","key":"12a766a31a58d7927298da06bfa6040585521d3f0205f5a79859fc8af0cfe3c3"}
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

### Service — `some-service`

The service gains one route that answers whether it is running.

#### The health response

**Reversibility: high cost** — load balancers, monitors and people we cannot list poll it, and each would have to change.

`GET /health` answers a fixed shape. Adding a field later is safe; renaming or removing one is not.

```json
{"status": "ok"}
```

**Reversible:**

- **Answered in process.** The route is served by the service itself, with no new process or store.

## Acceptance criteria

- Polling the endpoint says whether the service is up.
- Tests drive real requests rather than asserting on internal state.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| feat(api): add /health endpoint | Service | the endpoint and its tests | none | examples/tasks/health-endpoint/health.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- Polling the endpoint says whether the service is up.
- Tests drive real requests rather than asserting on internal state.
architecture: |-
  One route on the existing service, answered from the service itself. No new process, no
  new store, nothing else to deploy or watch.
planned_tasks:
- delivers: the endpoint and its tests
  depends_on: none
  location: examples/tasks/health-endpoint/health.md
  task: 'feat(api): add /health endpoint'
  unit: Service
units:
- decisions:
  - artifact: |-
      ```json
      {"status": "ok"}
      ```
    justification: load balancers, monitors and people we cannot list poll it, and each would have to change.
    name: The health response
    summary: '`GET /health` answers a fixed shape. Adding a field later is safe; renaming or removing one is not.'
  name: Service
  part: ''
  repository: some-service
  reversible:
  - text: The route is served by the service itself, with no new process or store.
    title: Answered in process
  summary: The service gains one route that answers whether it is running.
what: |-
  A health endpoint on the service, so an operator can ask whether it is up without
  reading logs.
why: |-
  Operators have no cheap way to answer "is it running". They read logs or open a shell,
  which is slow at the moment it matters most.
-->
