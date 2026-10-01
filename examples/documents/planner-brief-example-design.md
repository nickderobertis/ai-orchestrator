---
title: 'Design: planner-brief-example'
project: planner-brief-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:d9fc7226a615c542a1db86abed3a13c8e513ea11e9a287dac35edcbf8a94fa54
  onetaskgraph.template:
    answers_digest: sha256:d2d517d217e1d9514586678340eb5ca86f2076ca957f0fc269318d657f9749bb
    body_digest: sha256:365ff2aa21039a827e1d670d4931057a08704615252e16de0145b191dcecd89f
    digest: sha256:40ceac966dad3e35698a79077e2f0faaa59d9373ca5bb912dfde7ffa0469686d
    template: onepipeline:design-doc
  "orchestrator.design-approval": {"approved_at":"2026-10-01T13:24:53.121163+00:00","key":"963e1fae60b91bb4f49739b68f93499f3e4fc66def6ab6b90de65043940a8b99"}
---
## What

A planning run: one agent turns a manager's brief into a plan another agent could execute
without re-deriving it.

## Why

A brief is one person's request, and a plan is a graph of tasks. Somebody has to do the
reading in between, and neither the manager nor the reader of the plan is well placed to.

## Architecture

One node. The planner reads the repository and writes the plan into the plan store, as a
direct node working in the checkout the launch was made from. What happens to that plan
afterwards is a second launch rather than a second node of this one, because the design
document a person reviews the plan as has to be written from reviewed content, and a run
cannot interject a review between its own nodes.

### Planning run — `ai-orchestrator`

The planner writes a plan into the plan store and commits nothing; `just plan` then hands over to `just finish-plan`, which reviews the plan, checks it, launches the document, and copies both into the destination a person reviews them in.

**Reversible:**

- **The plan's address.** The brief names the qualified project the planner writes to, and everything after this run reads that same address.
- **The document's shape.** The second launch writes a rendering of the `design-doc` template, so its task names the template rather than restating it.

## Acceptance criteria

- The plan names every task, its role, its dependencies, and the contract between it and
  the tasks that consume it.
- The document that second launch writes reads back out of the plan store as a document of
  that same plan's project.
- Every row of the document's planned-tasks table points at a task using the location the
  store reports for it.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| feat(plan): planner-brief-example | Planning run | the plan itself | none | examples/tasks/planner-brief-example/plan.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- |-
  The plan names every task, its role, its dependencies, and the contract between it and
  the tasks that consume it.
- |-
  The document that second launch writes reads back out of the plan store as a document of
  that same plan's project.
- |-
  Every row of the document's planned-tasks table points at a task using the location the
  store reports for it.
architecture: |-
  One node. The planner reads the repository and writes the plan into the plan store, as a
  direct node working in the checkout the launch was made from. What happens to that plan
  afterwards is a second launch rather than a second node of this one, because the design
  document a person reviews the plan as has to be written from reviewed content, and a run
  cannot interject a review between its own nodes.
planned_tasks:
- delivers: the plan itself
  depends_on: none
  location: examples/tasks/planner-brief-example/plan.md
  task: 'feat(plan): planner-brief-example'
  unit: Planning run
units:
- decisions: []
  name: Planning run
  part: ''
  repository: ai-orchestrator
  reversible:
  - text: The brief names the qualified project the planner writes to, and everything after this run reads that same address.
    title: The plan's address
  - text: The second launch writes a rendering of the `design-doc` template, so its task names the template rather than restating it.
    title: The document's shape
  summary: The planner writes a plan into the plan store and commits nothing; `just plan` then hands over to `just finish-plan`, which reviews the plan, checks it, launches the document, and copies both into the destination a person reviews them in.
what: |-
  A planning run: one agent turns a manager's brief into a plan another agent could execute
  without re-deriving it.
why: |-
  A brief is one person's request, and a plan is a graph of tasks. Somebody has to do the
  reading in between, and neither the manager nor the reader of the plan is well placed to.
-->
