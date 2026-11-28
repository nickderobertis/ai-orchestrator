---
title: 'Design: tracked-release'
project: tracked-release
metadata:
  onetaskgraph.origin: orchestrator-record-staging:f9a181fad4a495bf5f9042f736a807665321e8f64d5e8eb0eceae56c9c56a6a8
  "onetaskgraph.template": {"template":"onepipeline:design-doc","digest":"sha256:ff54a57adaf34a907e3ae341d854d4b7bcd7ed66db852a7d989c64b43b2d401e","body_digest":"sha256:909c31816e1bef10ba4d37887616994388358fbb91c79fa55c691ee5f497dc76","answers_digest":"sha256:174a01f00c026fd5f2e7da6ff028df3f852970ad19de66f9bac1385c986fc84f"}
---
## What

One release carried from a written design, through a person's approval of it, into the
service and the documentation, to a second approval and the announcement.

## Why

A release that crosses several repositories goes wrong where the parties disagree about
what was agreed. Writing the agreement down once, and having a person accept it before
anything is built, is what keeps that from happening late.

## Architecture

A line with two people in it. The design is written first; somebody approves it; the
service and the documentation are then built against it in parallel, in their own
repositories; somebody approves the result; the announcement is written last. One node
changes nothing at all and says so.

### Service — `some-service`

The service implements the API and rollout the approved design states, and is approved in staging.

#### The API and rollout contract

Written into the design and approved by a person before anything is built; everything after restates it rather than deciding it again.

<details>
<summary><strong>High cost to undo</strong></summary>

once released, callers we cannot list depend on the API, and the rollout cannot be taken back from them.

</details>

**Reversible decisions (1):**

<details>
<summary>Readiness handoff</summary>

One node records that the readiness handoff produced no repository change, rather than pretending to.

</details>

### Documentation — `some-docs`

The documentation describes the approved API and rollout, and the announcement follows the second approval.

## Acceptance criteria

- The API and rollout are agreed in writing before any of it is built.
- The service does what was agreed, and the documentation describes what the service does.
- Nothing is published until a person has read both.

## Planned tasks

<details>
<summary>All 7 planned tasks</summary>

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| design | Service | the API and rollout contract | none | examples/tasks/tracked-release/design.md |
| design-approval | Service | a person's approval of that contract | design | examples/tasks/tracked-release/design-approval.md |
| unchanged-handoff | Service | the recorded readiness handoff | design | examples/tasks/tracked-release/unchanged-handoff.md |
| feat: implement approved release | Service | the service, approved in staging | design-approval | examples/tasks/tracked-release/service.md |
| docs: document the approved API and rollout | Documentation | the published description | design-approval | examples/tasks/tracked-release/docs.md |
| release-approval | Service | a person's approval to publish | service, docs | examples/tasks/tracked-release/release-approval.md |
| announce | Documentation | the release announcement | release-approval | examples/tasks/tracked-release/announce.md |

</details>


<!-- onetaskgraph:template-answers
acceptance_criteria:
- The API and rollout are agreed in writing before any of it is built.
- The service does what was agreed, and the documentation describes what the service does.
- Nothing is published until a person has read both.
accepted_opaque: []
architecture: |-
  A line with two people in it. The design is written first; somebody approves it; the
  service and the documentation are then built against it in parallel, in their own
  repositories; somebody approves the result; the announcement is written last. One node
  changes nothing at all and says so.
budgets: []
dag_image: ''
delivers: {}
goals: []
graph: {}
plan_budgets:
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
planned_tasks:
- delivers: the API and rollout contract
  depends_on: none
  location: examples/tasks/tracked-release/design.md
  task: design
  unit: Service
- delivers: a person's approval of that contract
  depends_on: design
  location: examples/tasks/tracked-release/design-approval.md
  task: design-approval
  unit: Service
- delivers: the recorded readiness handoff
  depends_on: design
  location: examples/tasks/tracked-release/unchanged-handoff.md
  task: unchanged-handoff
  unit: Service
- delivers: the service, approved in staging
  depends_on: design-approval
  location: examples/tasks/tracked-release/service.md
  task: 'feat: implement approved release'
  unit: Service
- delivers: the published description
  depends_on: design-approval
  location: examples/tasks/tracked-release/docs.md
  task: 'docs: document the approved API and rollout'
  unit: Documentation
- delivers: a person's approval to publish
  depends_on: service, docs
  location: examples/tasks/tracked-release/release-approval.md
  task: release-approval
  unit: Service
- delivers: the release announcement
  depends_on: release-approval
  location: examples/tasks/tracked-release/announce.md
  task: announce
  unit: Documentation
units:
- name: Service
  repository: some-service
  part: ''
  summary: The service implements the API and rollout the approved design states, and is approved in staging.
  reversible:
  - group: ''
    gist: ''
    items:
    - text: One node records that the readiness handoff produced no repository change, rather than pretending to.
      title: Readiness handoff
  decisions:
  - name: The API and rollout contract
    justification: once released, callers we cannot list depend on the API, and the rollout cannot be taken back from them.
    gist: Written into the design and approved by a person before anything is built; everything after restates it rather than deciding it again.
    show: []
    details: []
- name: Documentation
  repository: some-docs
  part: ''
  summary: The documentation describes the approved API and rollout, and the announcement follows the second approval.
  reversible: []
  decisions: []
visual_changes: []
what: |-
  One release carried from a written design, through a person's approval of it, into the
  service and the documentation, to a second approval and the announcement.
why: |-
  A release that crosses several repositories goes wrong where the parties disagree about
  what was agreed. Writing the agreement down once, and having a person accept it before
  anything is built, is what keeps that from happening late.
-->
