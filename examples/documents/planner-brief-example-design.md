---
title: 'Design: planner-brief-example'
project: planner-brief-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:d9fc7226a615c542a1db86abed3a13c8e513ea11e9a287dac35edcbf8a94fa54
  "orchestrator.design-approval": {"approved_at":"2026-09-29T05:14:15.502878+00:00","key":"f6e5648d3ec4bd78c43c6350e6cd9faf565df330a8a63255554c987ac6b64e90"}
  "onetaskgraph.template": {"answers_digest":"sha256:76aeb282c24c3c0f9349596e404b90348cb7444d9f537e21f5ca8f212c0caef9","body_digest":"sha256:1ee910fbbadfab84cdd22c6e77b55625d2ae97d10976c4d9054be12229183ebd","digest":"sha256:ee358d23284193c8fe3b8a0c03f79076d50584241527f148c6e00c33a8146515","template":"onepipeline:design-doc"}
---
## What

A planning run: one agent turns a manager's brief into a plan another agent could execute
without re-deriving it.

## Why

A brief is one person's request, and a plan is a graph of tasks. Somebody has to do the
reading in between, and neither the manager nor the reader of the plan is well placed to.

## Architecture

One node. The planner reads the repository and writes the plan into the plan store, as a
direct node working in the checkout the launch was made from: it produces a record and
never a branch, and the engine settles a lifecycle dispatch that commits nothing to its
branch as a failure, so it is told in its task to write only to gitignored paths, commit
nothing, and cut no branch.

What happens to that plan afterwards is a second launch rather than a second node of this
one, and the reason is ordering: the short document a person reviews the plan as has to be
written from *reviewed* content, and a run cannot interject a review between its own nodes
— a review record is written by this repository's own code and never by a dispatched
agent. So `just plan` runs this plan, records what its planner authored, and then hands
over to `just finish-plan`, which reviews the plan, checks it, launches the document, and
copies both into the destination a person reviews them in.

## Contracts

- **The plan's address.** The brief names the qualified project the planner writes to, and
  everything after this run reads that same address. It is the only thing the two launches
  share, and a brief that omits it is refused before this planner is dispatched.
- **The document's shape.** What the second launch writes is a rendering of this
  host's design-doc template, whose variables state what each section is judged on, so
  that dispatch's task names the template rather than restating it.

## Acceptance criteria

- The plan names every task, its role, its dependencies, and the contract between it and
  the tasks that consume it.
- The document that second launch writes reads back out of the plan store as a document of
  that same plan's project.
- Every row of the document's planned-tasks table points at a task using the location the
  store reports for it.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| feat(plan): planner-brief-example | the plan itself | none | examples/tasks/planner-brief-example/plan.md |


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
  direct node working in the checkout the launch was made from: it produces a record and
  never a branch, and the engine settles a lifecycle dispatch that commits nothing to its
  branch as a failure, so it is told in its task to write only to gitignored paths, commit
  nothing, and cut no branch.

  What happens to that plan afterwards is a second launch rather than a second node of this
  one, and the reason is ordering: the short document a person reviews the plan as has to be
  written from *reviewed* content, and a run cannot interject a review between its own nodes
  — a review record is written by this repository's own code and never by a dispatched
  agent. So `just plan` runs this plan, records what its planner authored, and then hands
  over to `just finish-plan`, which reviews the plan, checks it, launches the document, and
  copies both into the destination a person reviews them in.
contracts:
- |-
  **The plan's address.** The brief names the qualified project the planner writes to, and
  everything after this run reads that same address. It is the only thing the two launches
  share, and a brief that omits it is refused before this planner is dispatched.
- |-
  **The document's shape.** What the second launch writes is a rendering of this
  host's design-doc template, whose variables state what each section is judged on, so
  that dispatch's task names the template rather than restating it.
planned_tasks:
- delivers: the plan itself
  depends_on: none
  location: examples/tasks/planner-brief-example/plan.md
  task: 'feat(plan): planner-brief-example'
what: |-
  A planning run: one agent turns a manager's brief into a plan another agent could execute
  without re-deriving it.
why: |-
  A brief is one person's request, and a plan is a graph of tasks. Somebody has to do the
  reading in between, and neither the manager nor the reader of the plan is well placed to.
-->
