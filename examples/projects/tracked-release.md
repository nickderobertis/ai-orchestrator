---
title: tracked-release
status: todo
metadata:
  onepipeline.concurrency: 3
  onepipeline.goal:
    text: Deliver the tracked release safely across its target projects
  onepipeline.schema_version: 3
  onetaskgraph.template:
    answers_digest: sha256:dbb5a93cd43bb7be7ecfed5fd59dc54a0bffa38b9410a6029def692816c41530
    body_digest: sha256:44b1016b10f11be1ad98b778d269fa32eb6073cb23a98fe68485629d13337508
    digest: sha256:f06000650b079fef3e2586ce346781db0d2a73aa0cda518fc827d41a8404fd3e
    template: onepipeline:plan-description
  orchestrator.plan-budgets:
    checklist:
    - budget: ''
      concern: latency
      in_scope: true
      not_applicable: n/a because the release changes which behaviour serves a request, not how much work a request does, and the design states it keeps the request path's cost.
      summary: The release changes which behaviour serves a request, not the work it does.
    - budget: ''
      concern: quota and rate-limit headroom
      in_scope: false
      not_applicable: n/a because the release calls no rate-limited service.
      summary: The release calls no rate-limited service.
    - budget: ''
      concern: scaling with data
      in_scope: false
      not_applicable: n/a because the release changes no stored data's shape or volume.
      summary: The release changes no stored data's shape or volume.
    - budget: ''
      concern: spend
      in_scope: false
      not_applicable: n/a because the rollout runs on the 3 replicas already provisioned, with no new paid service.
      summary: The rollout uses the replicas already provisioned and no new paid service.
    - budget: ''
      concern: resource use
      in_scope: false
      not_applicable: n/a because both behaviours run on the same replicas during the rollout and neither does more work per request.
      summary: Both behaviours share the existing replicas and neither does more per request.
    overview: Design a release and its rollout, have the design approved, implement it in the service behind a staged approval, document it in the docs site, and announce it once a person approves publication.
    realistic_data: []
    repo_wide_effects: []
    schema_version: 2
    sizing: A rollout across 3 service replicas serving about 1,200 requests a minute at peak, moving through 10%, 50% and 100% of traffic over about two hours.
    spike_findings: []
    ten_x: At 10x, 12,000 requests a minute, the first thing the product owner notices is how many users a bad rollout step reaches before it is stopped. That is bounded by the staged rollout and the two approvals the plan already carries rather than by a measured threshold, so no budget covers it; the service's request cost does not change with this release.
    ten_x_summary: A slow step would hold more users on a bad version, so the staged steps, not a budget, are what bounds it at 12,000 requests a minute.
    workload: The service runs 3 replicas serving about 1,200 requests a minute at peak. The rollout moves 10%, then 50%, then 100% of traffic to the new behaviour, with a staging approval before it starts and about 40 minutes at each step. The docs site gains about 3 pages, read by the service's roughly 300 API users.
---
Design a release and its rollout, have the design approved, implement it in the service behind a staged approval, document it in the docs site, and announce it once a person approves publication.

## Budgets

**What we're sizing for.** A rollout across 3 service replicas serving about 1,200 requests a minute at peak, moving through 10%, 50% and 100% of traffic over about two hours.

### Workload

The service runs 3 replicas serving about 1,200 requests a minute at peak. The rollout moves 10%, then 50%, then 100% of traffic to the new behaviour, with a staging approval before it starts and about 40 minutes at each step. The docs site gains about 3 pages, read by the service's roughly 300 API users.

### At 10x realistic usage

**A slow step would hold more users on a bad version, so the staged steps, not a budget, are what bounds it at 12,000 requests a minute.**

At 10x, 12,000 requests a minute, the first thing the product owner notices is how many users a bad rollout step reaches before it is stopped. That is bounded by the staged rollout and the two approvals the plan already carries rather than by a measured threshold, so no budget covers it; the service's request cost does not change with this release.

### Checklist

- **latency:** not budgeted. n/a because the release changes which behaviour serves a request, not how much work a request does, and the design states it keeps the request path's cost.
- **quota and rate-limit headroom:** not budgeted. n/a because the release calls no rate-limited service.
- **scaling with data:** not budgeted. n/a because the release changes no stored data's shape or volume.
- **spend:** not budgeted. n/a because the rollout runs on the 3 replicas already provisioned, with no new paid service.
- **resource use:** not budgeted. n/a because both behaviours run on the same replicas during the rollout and neither does more work per request.

### Repo-wide effects


### Realistic data

This plan makes no data realistic.

### Spike findings

No spike finding changed this plan.


<!-- onetaskgraph:template-answers
checklist:
- budget: ''
  concern: latency
  in_scope: true
  not_applicable: n/a because the release changes which behaviour serves a request, not how much work a request does, and the design states it keeps the request path's cost.
  summary: The release changes which behaviour serves a request, not the work it does.
- budget: ''
  concern: quota and rate-limit headroom
  in_scope: false
  not_applicable: n/a because the release calls no rate-limited service.
  summary: The release calls no rate-limited service.
- budget: ''
  concern: scaling with data
  in_scope: false
  not_applicable: n/a because the release changes no stored data's shape or volume.
  summary: The release changes no stored data's shape or volume.
- budget: ''
  concern: spend
  in_scope: false
  not_applicable: n/a because the rollout runs on the 3 replicas already provisioned, with no new paid service.
  summary: The rollout uses the replicas already provisioned and no new paid service.
- budget: ''
  concern: resource use
  in_scope: false
  not_applicable: n/a because both behaviours run on the same replicas during the rollout and neither does more work per request.
  summary: Both behaviours share the existing replicas and neither does more per request.
overview: Design a release and its rollout, have the design approved, implement it in the service behind a staged approval, document it in the docs site, and announce it once a person approves publication.
realistic_data: []
repo_wide_effects: []
schema_version: 2
sizing: A rollout across 3 service replicas serving about 1,200 requests a minute at peak, moving through 10%, 50% and 100% of traffic over about two hours.
spike_findings: []
ten_x: At 10x, 12,000 requests a minute, the first thing the product owner notices is how many users a bad rollout step reaches before it is stopped. That is bounded by the staged rollout and the two approvals the plan already carries rather than by a measured threshold, so no budget covers it; the service's request cost does not change with this release.
ten_x_summary: A slow step would hold more users on a bad version, so the staged steps, not a budget, are what bounds it at 12,000 requests a minute.
workload: The service runs 3 replicas serving about 1,200 requests a minute at peak. The rollout moves 10%, then 50%, then 100% of traffic to the new behaviour, with a staging approval before it starts and about 40 minutes at each step. The docs site gains about 3 pages, read by the service's roughly 300 API users.
-->
