---
title: 'Design: planner-brief-example'
project: planner-brief-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:d9fc7226a615c542a1db86abed3a13c8e513ea11e9a287dac35edcbf8a94fa54
  "onetaskgraph.template": {"template":"onepipeline:design-doc","digest":"sha256:a1d0d6c5e9fdb37cd08db25d03e27c5aebb5303e155374d4545c831c838200b3","body_digest":"sha256:3c3a8049cddfdb99f0afd880b14193ab285fd1eb34211d7455b37540bf6a1340","answers_digest":"sha256:de6beb00d302772a9e020c7ec0d96b3f73d65a6cfb82c7facb1bf86377e9f0b3"}
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

<details>
<summary>The plan's address</summary>

The brief names the qualified project the planner writes to, and everything after this run reads that same address.

</details>

<details>
<summary>The document's shape</summary>

The second launch writes a rendering of the `design-doc` template, so its task names the template rather than restating it.

</details>

## Acceptance criteria

- The plan names every task, its role, its dependencies, and the contract between it and
  the tasks that consume it.
- The document that second launch writes reads back out of the plan store as a document of
  that same plan's project.
- Every row of the document's planned-tasks table points at a task using the location the
  store reports for it.

## Planned tasks

<details>
<summary>All 1 planned tasks</summary>

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| feat(plan): planner-brief-example | Planning run | the plan itself | none | examples/tasks/planner-brief-example/plan.md |

</details>


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
accepted_opaque: []
architecture: |-
  One node. The planner reads the repository and writes the plan into the plan store, as a
  direct node working in the checkout the launch was made from. What happens to that plan
  afterwards is a second launch rather than a second node of this one, because the design
  document a person reviews the plan as has to be written from reviewed content, and a run
  cannot interject a review between its own nodes.
budgets: []
dag_image: ''
delivers: {}
goals: []
graph: {}
plan_budgets: {}
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
visual_changes: []
what: |-
  A planning run: one agent turns a manager's brief into a plan another agent could execute
  without re-deriving it.
why: |-
  A brief is one person's request, and a plan is a graph of tasks. Somebody has to do the
  reading in between, and neither the manager nor the reader of the plan is well placed to.
-->
