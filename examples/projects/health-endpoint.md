---
title: health-endpoint
status: todo
metadata:
  onepipeline.goal:
    text: Give the service a health endpoint operators can poll
  onepipeline.schema_version: 3
  onetaskgraph.template:
    answers_digest: sha256:17b39f42cf8c37d6819e7237a53f9dbbd769498cd98c7524d831fed8155c6de9
    body_digest: sha256:146c01a99563182709db84bd890dc2835dd1ac0831e1b89e29d6b6cb1dd1b15d
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
    overview: Give the service a `GET /health` endpoint returning its build metadata, so operators can poll an instance for its health and version instead of learning from user reports that a deploy went wrong.
    realistic_data: []
    repo_wide_effects: []
    schema_version: 2
    sizing: Three monitoring probes polling every 10 seconds and one load balancer checking every 5 seconds, against a service already serving about 200 requests a second.
    spike_findings: []
    ten_x: At 10x, about 11 health requests a second, the first thing an operator notices is a health answer that arrives late enough to look like an unhealthy instance, which `health-endpoint-p95-latency` covers. The response stays under 1 KB and reads nothing that grows, so no other cost moves.
    ten_x_summary: Probes see slower health answers once 40 pollers compete with ordinary traffic, covered by health-endpoint-p95-latency.
    workload: 'Three monitoring probes poll `GET /health` every 10 seconds and the load balancer checks it every 5 seconds per instance, across 4 instances: about 1.1 health requests a second in total, beside about 200 requests a second of ordinary traffic. Each response carries the same build metadata, under 1 KB, fixed when the instance starts.'
---
Give the service a `GET /health` endpoint returning its build metadata, so operators can poll an instance for its health and version instead of learning from user reports that a deploy went wrong.

## Budgets

**What we're sizing for.** Three monitoring probes polling every 10 seconds and one load balancer checking every 5 seconds, against a service already serving about 200 requests a second.

### Workload

Three monitoring probes poll `GET /health` every 10 seconds and the load balancer checks it every 5 seconds per instance, across 4 instances: about 1.1 health requests a second in total, beside about 200 requests a second of ordinary traffic. Each response carries the same build metadata, under 1 KB, fixed when the instance starts.

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
overview: Give the service a `GET /health` endpoint returning its build metadata, so operators can poll an instance for its health and version instead of learning from user reports that a deploy went wrong.
realistic_data: []
repo_wide_effects: []
schema_version: 2
sizing: Three monitoring probes polling every 10 seconds and one load balancer checking every 5 seconds, against a service already serving about 200 requests a second.
spike_findings: []
ten_x: At 10x, about 11 health requests a second, the first thing an operator notices is a health answer that arrives late enough to look like an unhealthy instance, which `health-endpoint-p95-latency` covers. The response stays under 1 KB and reads nothing that grows, so no other cost moves.
ten_x_summary: Probes see slower health answers once 40 pollers compete with ordinary traffic, covered by health-endpoint-p95-latency.
workload: 'Three monitoring probes poll `GET /health` every 10 seconds and the load balancer checks it every 5 seconds per instance, across 4 instances: about 1.1 health requests a second in total, beside about 200 requests a second of ordinary traffic. Each response carries the same build metadata, under 1 KB, fixed when the instance starts.'
-->
