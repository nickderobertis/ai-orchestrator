---
title: repo-plan-example
status: todo
metadata:
  onepipeline.concurrency: 4
  onepipeline.schema_version: 3
  onetaskgraph.template:
    answers_digest: sha256:d48446763aa735a1e1cf7b68f86ee90bb4fa8c8023ae515e2d3877e5d450bede
    body_digest: sha256:79bacbccbcd0c84115924c8c795b19e2c0fa5985a986b308ae0bf605cb4735d4
    digest: sha256:f06000650b079fef3e2586ce346781db0d2a73aa0cda518fc827d41a8404fd3e
    template: onepipeline:plan-description
  orchestrator.plan-budgets:
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
---
Add a `GET /health` endpoint to the service, a typed `health()` method to its client, an admin dashboard that shows the health metadata, and the endpoint's documentation, across the service, client and docs repositories.

## Budgets

**What we're sizing for.** Three monitoring probes polling every 10 seconds, a load balancer checking every 5 seconds, and a handful of on-call engineers opening the dashboard during an incident, against a service serving about 200 requests a second.

### Workload

Three monitoring probes poll `GET /health` every 10 seconds and the load balancer checks it every 5 seconds per instance, across 4 instances: about 1.1 health requests a second, beside about 200 requests a second of ordinary traffic. Up to 5 on-call engineers open the dashboard during an incident, each view one health request. Each response carries the same build metadata, under 1 KB.

### At 10x realistic usage

**Probes see slower health answers once 40 pollers compete with ordinary traffic, covered by health-endpoint-p95-latency.**

At 10x, about 11 health requests a second, the first thing an operator notices is a health answer that arrives late enough to look like an unhealthy instance, which `health-endpoint-p95-latency` covers. The response stays under 1 KB and reads nothing that grows, so no other cost moves.

### Checklist

- **latency:** covered by `health-endpoint-p95-latency`.
- **quota and rate-limit headroom:** not budgeted. n/a because the endpoint calls no rate-limited service: it answers from metadata held in memory since startup.
- **scaling with data:** not budgeted. n/a because the response is the same build metadata whatever the service stores, under 1 KB, so nothing it returns grows with data.
- **spend:** not budgeted. n/a because the endpoint calls no paid service and adds about one request a second to instances already provisioned for 200.
- **resource use:** not budgeted. n/a because the metadata is read once at startup and each answer allocates under 1 KB, far below what the instance's ordinary traffic uses.

### Repo-wide effects


### Realistic data

This plan makes no data realistic.

### Spike findings

No spike finding changed this plan.


<!-- onetaskgraph:template-answers
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
-->
