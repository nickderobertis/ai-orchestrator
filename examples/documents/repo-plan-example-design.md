---
title: 'Design: repo-plan-example'
project: repo-plan-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:102de0e7ec9d17f43c286db96673ed970aa1e0b2d444f9ee5085351344f8eee5
  "onetaskgraph.template": {"answers_digest":"sha256:7fa0efd8f1550e7f4ebb7fe510dfb94f094db89ddacab9c8509fb1619e79560c","body_digest":"sha256:87242bed5f58680ad5ced6d53f9cfaa6a4bb9bf4c602870516076dc985e2eccb","digest":"sha256:b3081e2663074adf631864ea2a51e4148e6a82407f8fc9b893f85a2c6aef6634","template":"onepipeline:design-doc"}
  "orchestrator.design-approval": {"approved_at":"2026-10-09T00:49:56.727632+00:00","key":"57a16b09431de7254340f0092b375c52f8ac484eb7f4172d3cf2957ac339f010"}
---
## What

A health endpoint on the service, the client method that calls it, an admin page that
shows what it returns, and the documentation for all three — each landing in the
repository it belongs to.

## Why

Operators can already be told whether the service is up, but only from a terminal. The
people who need that answer during an incident are reading raw JSON, or asking somebody
who can.

## Architecture

Four pieces of work across three repositories. The endpoint lands first because everything
else reads it; the client method and the dashboard are then built at the same time, and the
documentation lands beside them. The dashboard carries a person's approval inside its own
work, so the approval happens on the branch the work is on.

### Service — `some-service`

The service gains the health endpoint, and its admin dashboard shows what that endpoint returns to viewers allowed to see it.

#### The health response

**Reversibility: high cost** — the client library, the dashboard and the documentation all read it, and the client library's own users are people we cannot list.

One response shape, published by the service and read by every other unit.

```json
{"status": "ok", "version": "1.4.2"}
```

**Reversible:**

- **Dashboard access.** The admin page refuses a viewer without the `admin` role.

### Client library — `some-client`

The client library gains a method that calls the endpoint.

#### The `health()` method

**Reversibility: high cost** — the library is released, and every caller of a released method would have to change.

A new public method returning the parsed health response.

```diff
 class Client:
+    def health(self) -> Health: ...
```

### Documentation — `some-docs`

The published documentation describes the endpoint that actually exists.

## Budgets

**Sizing.** Three monitoring probes polling every 10 seconds, a load balancer checking every 5 seconds, and a handful of on-call engineers opening the dashboard during an incident, against a service serving about 200 requests a second.

**At 10×.** Probes see slower health answers once 40 pollers compete with ordinary traffic, covered by health-endpoint-p95-latency.

| Budget | Target | Basis | How it is measured | Check runtime |
| --- | --- | --- | --- | --- |
| [Health endpoint p95 latency][1] | ≤50 ms | estimate | telemetry | ≈3 s |

[1]: examples/tasks/repo-plan-example/api.md

## Acceptance criteria

- Polling the service says whether it is up.
- The client library exposes that answer as a method.
- The dashboard shows every field the response carries, and refuses a viewer who may not
  see it.
- The documentation describes the endpoint that actually exists.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| feat(api): add /health endpoint | Service | the endpoint | none | examples/tasks/repo-plan-example/api.md |
| feat(client): add health() method | Client library | the client method | the endpoint | examples/tasks/repo-plan-example/client.md |
| feat(admin): add dashboard | Service | the page, and a person's approval of it | the endpoint | examples/tasks/repo-plan-example/dashboard.md |
| docs(api): document /health | Documentation | the published description | none | examples/tasks/repo-plan-example/docs.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- Polling the service says whether it is up.
- The client library exposes that answer as a method.
- |-
  The dashboard shows every field the response carries, and refuses a viewer who may not
  see it.
- The documentation describes the endpoint that actually exists.
architecture: |-
  Four pieces of work across three repositories. The endpoint lands first because everything
  else reads it; the client method and the dashboard are then built at the same time, and the
  documentation lands beside them. The dashboard carries a person's approval inside its own
  work, so the approval happens on the branch the work is on.
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
  location: examples/tasks/repo-plan-example/api.md
  measure: The 95th-percentile time a poller waits for `GET /health`, from the request sent to the response read through the service's real HTTP stack, over the requests the endpoint's request tests record; its analysis reports the median and the slowest request as its breakdown.
  name: Health endpoint p95 latency
  node: api
  repository: github.com/nickderobertis/some-service
  schema_version: 2
  source: telemetry
  threshold: 50
  unit: ms
  workload: 200 sequential `GET /health` requests against one service instance, the requests three monitoring probes polling every 10 s and a load balancer checking every 5 s make over about five minutes.
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
  overview: Add a `GET /health` endpoint to the service, a typed `health()` method to its client, an admin dashboard that shows the health metadata, and the endpoint's documentation, across the service, client and docs repositories.
  realistic_data: []
  repo_wide_effects: []
  schema_version: 2
  sizing: Three monitoring probes polling every 10 seconds, a load balancer checking every 5 seconds, and a handful of on-call engineers opening the dashboard during an incident, against a service serving about 200 requests a second.
  spike_findings: []
  ten_x: At 10x, about 11 health requests a second, the first thing an operator notices is a health answer that arrives late enough to look like an unhealthy instance, which `health-endpoint-p95-latency` covers. The response stays under 1 KB and reads nothing that grows, so no other cost moves.
  ten_x_summary: Probes see slower health answers once 40 pollers compete with ordinary traffic, covered by health-endpoint-p95-latency.
  workload: 'Three monitoring probes poll `GET /health` every 10 seconds and the load balancer checks it every 5 seconds per instance, across 4 instances: about 1.1 health requests a second, beside about 200 requests a second of ordinary traffic. Up to 5 on-call engineers open the dashboard during an incident, each view one health request. Each response carries the same build metadata, under 1 KB.'
planned_tasks:
- delivers: the endpoint
  depends_on: none
  location: examples/tasks/repo-plan-example/api.md
  task: 'feat(api): add /health endpoint'
  unit: Service
- delivers: the client method
  depends_on: the endpoint
  location: examples/tasks/repo-plan-example/client.md
  task: 'feat(client): add health() method'
  unit: Client library
- delivers: the page, and a person's approval of it
  depends_on: the endpoint
  location: examples/tasks/repo-plan-example/dashboard.md
  task: 'feat(admin): add dashboard'
  unit: Service
- delivers: the published description
  depends_on: none
  location: examples/tasks/repo-plan-example/docs.md
  task: 'docs(api): document /health'
  unit: Documentation
predates_budgets: ''
units:
- decisions:
  - artifact: |-
      ```json
      {"status": "ok", "version": "1.4.2"}
      ```
    justification: the client library, the dashboard and the documentation all read it, and the client library's own users are people we cannot list.
    name: The health response
    summary: One response shape, published by the service and read by every other unit.
  name: Service
  part: ''
  repository: some-service
  reversible:
  - text: The admin page refuses a viewer without the `admin` role.
    title: Dashboard access
  summary: The service gains the health endpoint, and its admin dashboard shows what that endpoint returns to viewers allowed to see it.
- decisions:
  - artifact: |-
      ```diff
       class Client:
      +    def health(self) -> Health: ...
      ```
    justification: the library is released, and every caller of a released method would have to change.
    name: The `health()` method
    summary: A new public method returning the parsed health response.
  name: Client library
  part: ''
  repository: some-client
  reversible: []
  summary: The client library gains a method that calls the endpoint.
- decisions: []
  name: Documentation
  part: ''
  repository: some-docs
  reversible: []
  summary: The published documentation describes the endpoint that actually exists.
what: |-
  A health endpoint on the service, the client method that calls it, an admin page that
  shows what it returns, and the documentation for all three — each landing in the
  repository it belongs to.
why: |-
  Operators can already be told whether the service is up, but only from a terminal. The
  people who need that answer during an incident are reading raw JSON, or asking somebody
  who can.
-->
