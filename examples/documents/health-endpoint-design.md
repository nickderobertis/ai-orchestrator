---
title: 'Design: health-endpoint'
project: health-endpoint
metadata:
  onetaskgraph.origin: orchestrator-record-staging:5697e51426246121fbc2486ca79881d3fbc11590f7b984c7514a0c1b5100a474
  "onetaskgraph.template": {"template":"onepipeline:design-doc","digest":"sha256:74e0141a235840245424bb4a58718233eda51d093703e25db52bfcc1fd41122d","body_digest":"sha256:ae7907fb71ccde8aa1e0431c85a5d43b0d40a900b39eb523a7d6c0a802da39f3","answers_digest":"sha256:069948f1db58c046372b96dcda781d068d6adbea57c571208ed670e83041627e"}
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

<details>
<summary>Answered in process</summary>

The route is served by the service itself, with no new process or store.

</details>

## Budgets

**Sizing.** Three monitoring probes polling every 10 seconds and one load balancer checking every 5 seconds, against a service already serving about 200 requests a second.

**At 10×.** Probes see slower health answers once 40 pollers compete with ordinary traffic, covered by health-endpoint-p95-latency.

**Added budgets:**

| Budget | Target |
| --- | --- |
| [Health endpoint p95 latency][1] | ≤50 ms |

[1]: examples/tasks/health-endpoint/health.md

**Check runtime added:** not estimated for Health endpoint p95 latency.

## Acceptance criteria

- Polling the endpoint says whether the service is up.
- Tests drive real requests rather than asserting on internal state.

## Planned tasks

<details>
<summary>All 1 planned tasks</summary>

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| feat(api): add /health endpoint | Service | the endpoint and its tests | none | examples/tasks/health-endpoint/health.md |

</details>


<!-- onetaskgraph:template-answers
acceptance_criteria:
- Polling the endpoint says whether the service is up.
- Tests drive real requests rather than asserting on internal state.
accepted_opaque: []
architecture: |-
  One route on the existing service, answered from the service itself. No new process, no
  new store, nothing else to deploy or watch.
budgets:
- basis: estimate
  check_runtime_seconds: 3
  command: python scripts/budgets/health_latency.py .budgets/health-requests.jsonl
  direction: max
  evidence: 'Estimate: the handler returns build metadata fixed at startup and reads no store, so its time is routing and serialization, a few milliseconds; 50 ms leaves headroom for a loaded test host.'
  file: budgets.yaml
  file_change: add
  id: health-endpoint-p95-latency
  inner_measure_reason: ''
  location: examples/tasks/health-endpoint/health.md
  measure: The 95th-percentile time a poller waits for `GET /health`, from the request sent to the response read through the service's real HTTP stack, over the requests the endpoint's request tests record; its analysis reports the median and the slowest request as its breakdown.
  name: Health endpoint p95 latency
  node: health
  repository: github.com/nickderobertis/some-service
  schema_version: 2
  source: telemetry
  threshold: 50
  unit: ms
  workload: 200 sequential `GET /health` requests against one service instance, the requests three monitoring probes polling every 10 s and a load balancer checking every 5 s make over about five minutes.
dag_image: ''
delivers: {}
goals: []
graph: {}
plan_budgets:
  checklist:
  - budget: health-endpoint-p95-latency
    concern: latency
    in_scope: true
    not_applicable: ''
    summary: ''
  - budget: ''
    concern: quota and rate-limit headroom
    in_scope: false
    not_applicable: 'n/a because the endpoint calls no rate-limited service: it answers from metadata held in memory since startup.'
    summary: The endpoint calls no rate-limited service.
  - budget: ''
    concern: scaling with data
    in_scope: false
    not_applicable: n/a because the response is the same build metadata whatever the service stores, under 1 KB, so nothing it returns grows with data.
    summary: The response is fixed build metadata, under 1 KB, whatever the service stores.
  - budget: ''
    concern: spend
    in_scope: false
    not_applicable: n/a because the endpoint calls no paid service and adds about one request a second to instances already provisioned for 200.
    summary: No paid service is called, and about one request a second is negligible load.
  - budget: ''
    concern: resource use
    in_scope: false
    not_applicable: n/a because the metadata is read once at startup and each answer allocates under 1 KB, far below what the instance's ordinary traffic uses.
    summary: The metadata is read once at startup and each answer is under 1 KB.
  overview: Give the service a `GET /health` endpoint returning its build metadata, so operators can poll an instance for its health and version instead of learning from user reports that a deploy went wrong.
  realistic_data: []
  repo_wide_effects: []
  schema_version: 2
  sizing: Three monitoring probes polling every 10 seconds and one load balancer checking every 5 seconds, against a service already serving about 200 requests a second.
  spike_findings: []
  ten_x: At 10x, about 11 health requests a second, the first thing an operator notices is a health answer that arrives late enough to look like an unhealthy instance, which `health-endpoint-p95-latency` covers. The response stays under 1 KB and reads nothing that grows, so no other cost moves.
  ten_x_summary: Probes see slower health answers once 40 pollers compete with ordinary traffic, covered by health-endpoint-p95-latency.
  workload: 'Three monitoring probes poll `GET /health` every 10 seconds and the load balancer checks it every 5 seconds per instance, across 4 instances: about 1.1 health requests a second in total, beside about 200 requests a second of ordinary traffic. Each response carries the same build metadata, under 1 KB, fixed when the instance starts.'
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
visual_changes: []
what: |-
  A health endpoint on the service, so an operator can ask whether it is up without
  reading logs.
why: |-
  Operators have no cheap way to answer "is it running". They read logs or open a shell,
  which is slow at the moment it matters most.
-->
