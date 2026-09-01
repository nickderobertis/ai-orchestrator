---
title: 'Design: planner-brief-example'
project: planner-brief-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:planner-brief-example-design
  orchestrator.design-approval:
    approved_at: 2026-09-01T15:56:31.716241+00:00
    key: c00342beeb5b7488685e2291905059e2877d5637f2692e0bc302670e268a36b4
---
## What

A planning run: one agent turns a manager's brief into a plan, and a second writes the
short document that plan is reviewed as.

## Why

A brief is one person's request, and a plan is a graph of tasks. Somebody has to do the
reading in between, and neither the manager nor the reader of the plan is well placed to.

## Architecture

Two nodes on one line. The planner reads the repository and writes the plan into the plan
store. The second node runs only once the first has settled, reads that finished plan back
out of the store, and writes the document a person reviews it as. Nothing hands one node's
output to the next, so the brief names the plan's address and both nodes are told it.

## Contracts

**The plan's address.** The brief names the qualified project the planner writes to, and
the second node reads that same address. It is the only thing the two share, and a launch
whose brief omits it is refused rather than dispatched.

**The document's shape.** What the second node writes is stated once, in this repository's
design-document template, and the node's task points at that file rather than restating it.

## Acceptance criteria

- The plan names every task, its role, its dependencies, and the contract between it and
  the tasks that consume it.
- The document reads back out of the plan store as a document of that same plan's project.
- Every row of the document's planned-tasks table points at a task using the location the
  store reports for it.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| feat(plan): planner-brief-example | the plan itself | none | examples/tasks/planner-brief-example/plan.md |
| feat(plan): design document for planner-brief-example | the document the plan is reviewed as | the plan | examples/tasks/planner-brief-example/design-doc.md |
