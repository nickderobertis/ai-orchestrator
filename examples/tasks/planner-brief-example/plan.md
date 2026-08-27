---
title: "feat(plan): planner-brief-example"
project: "planner-brief-example"
status: "todo"
metadata:
  "onepipeline.id": "plan"
  "onepipeline.repo": "ai-orchestrator"
  "onepipeline.persona": "../personas/planner.yaml"
  "onepipeline.execution_checkout": "ai-orchestrator-isolated"
---

# Give the read API a paginated node listing

## What

Design the decomposition for adding a paginated node listing to the read API: the
route and its request and response fields, the browser view that consumes it, and
the order the pieces have to land in. Answer with one node per subtask, each naming
its id, its persona, its dependencies, and its task.

## Why

An operator supervising a long run cannot see past the first screen of nodes, so
they read the run's journal by hand to answer "what is still outstanding" — which is
the question the view exists to answer. Nobody has decided whether the cursor is an
opaque token or a node id, and that choice decides whether the view can deep-link.

## Acceptance criteria

- The plan names every subtask, its persona, its dependencies, and the contract
  between it and the subtasks that consume it.
- The route's request and response fields and their types are stated, including
  what the cursor is and what an exhausted page answers.
- The split follows that contract: the node establishing the route lands first and
  non-breaking, so its consumers are unaffected and the gate stays green.
- Every choice the brief left open is either decided in the plan, with the reason,
  or asked of the manager before the plan is finished.

## Additional info

The read API is `onepipeline-api` and the view is the `onepipeline-ui` bundle;
neither is built in this repository, so a subtask against either is a lifecycle node
carrying that repository's `repo`.
