---
title: 'Design: tracked-release'
project: tracked-release
metadata:
  onetaskgraph.origin: orchestrator-record-staging:f9a181fad4a495bf5f9042f736a807665321e8f64d5e8eb0eceae56c9c56a6a8
  onetaskgraph.template:
    answers_digest: sha256:4a5a87c17b53603b57bcab7de0a220ea2375b3e71ab3b9fdadf2f1f33a401673
    body_digest: sha256:968f1c6cd102da0aa3269c872a39b9a23c2f74f9fb7a5e7f6cb73d9ce2a164a2
    digest: sha256:40ceac966dad3e35698a79077e2f0faaa59d9373ca5bb912dfde7ffa0469686d
    template: onepipeline:design-doc
  "orchestrator.design-approval": {"approved_at":"2026-10-01T13:24:55.733273+00:00","key":"538a534abcf20a4430ea3c0e9a5c56fdfa8c45342eded0e75105a838a4f67a2d"}
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

**Reversibility: high cost** — once released, callers we cannot list depend on the API, and the rollout cannot be taken back from them.

Written into the design and approved by a person before anything is built; everything after restates it rather than deciding it again.

**Reversible:**

- **Readiness handoff.** One node records that the readiness handoff produced no repository change, rather than pretending to.

### Documentation — `some-docs`

The documentation describes the approved API and rollout, and the announcement follows the second approval.

## Acceptance criteria

- The API and rollout are agreed in writing before any of it is built.
- The service does what was agreed, and the documentation describes what the service does.
- Nothing is published until a person has read both.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| design | Service | the API and rollout contract | none | examples/tasks/tracked-release/design.md |
| design-approval | Service | a person's approval of that contract | design | examples/tasks/tracked-release/design-approval.md |
| unchanged-handoff | Service | the recorded readiness handoff | design | examples/tasks/tracked-release/unchanged-handoff.md |
| feat: implement approved release | Service | the service, approved in staging | design-approval | examples/tasks/tracked-release/service.md |
| docs: document the approved API and rollout | Documentation | the published description | design-approval | examples/tasks/tracked-release/docs.md |
| release-approval | Service | a person's approval to publish | service, docs | examples/tasks/tracked-release/release-approval.md |
| announce | Documentation | the release announcement | release-approval | examples/tasks/tracked-release/announce.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- The API and rollout are agreed in writing before any of it is built.
- The service does what was agreed, and the documentation describes what the service does.
- Nothing is published until a person has read both.
architecture: |-
  A line with two people in it. The design is written first; somebody approves it; the
  service and the documentation are then built against it in parallel, in their own
  repositories; somebody approves the result; the announcement is written last. One node
  changes nothing at all and says so.
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
- decisions:
  - artifact: ''
    justification: once released, callers we cannot list depend on the API, and the rollout cannot be taken back from them.
    name: The API and rollout contract
    summary: Written into the design and approved by a person before anything is built; everything after restates it rather than deciding it again.
  name: Service
  part: ''
  repository: some-service
  reversible:
  - text: One node records that the readiness handoff produced no repository change, rather than pretending to.
    title: Readiness handoff
  summary: The service implements the API and rollout the approved design states, and is approved in staging.
- decisions: []
  name: Documentation
  part: ''
  repository: some-docs
  reversible: []
  summary: The documentation describes the approved API and rollout, and the announcement follows the second approval.
what: |-
  One release carried from a written design, through a person's approval of it, into the
  service and the documentation, to a second approval and the announcement.
why: |-
  A release that crosses several repositories goes wrong where the parties disagree about
  what was agreed. Writing the agreement down once, and having a person accept it before
  anything is built, is what keeps that from happening late.
-->
