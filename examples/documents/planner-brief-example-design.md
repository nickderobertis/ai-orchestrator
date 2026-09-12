---
title: 'Design: planner-brief-example'
project: planner-brief-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:d9fc7226a615c542a1db86abed3a13c8e513ea11e9a287dac35edcbf8a94fa54
  orchestrator.design-approval:
    approved_at: 2026-09-12T03:27:19.039782+00:00
    key: d8879571929463f98b51820be7c9e2f2aaf945e2f4f3f97cc00f1648f94398ab
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

**The plan's address.** The brief names the qualified project the planner writes to, and
everything after this run reads that same address. It is the only thing the two launches
share, and a brief that omits it is refused before this planner is dispatched.

**The document's shape.** What the second launch writes is stated once, in this
repository's design-document template, and that dispatch's task points at the file rather
than restating it.

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
