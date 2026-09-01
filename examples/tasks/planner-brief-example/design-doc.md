---
# llmlint: ignore-file[no_redundant_instruction_pointers] This record is a generated dispatch prompt rather than a document in this repository's reference graph: its reader is another agent process in another worktree, and `just plan --repo` can place that worktree in a repository whose AGENTS.md is not this one, which is why the sentence names the repository `config/design-doc-template.md` lives in instead of assuming it is already loaded. The pointer is also load-bearing for the criteria beneath it, which say "that template" and are the whole bar the design-doc dispatch is judged against. File-scoped because the directive cannot sit at the site: everything below this frontmatter is the task prose delivered to that worker and reconciled byte for byte against `scripts/plan.sh`.
title: "feat(plan): design document for planner-brief-example"
project: "planner-brief-example"
status: "todo"
depends_on: ["planner-brief-example/plan"]
metadata:
  "onepipeline.id": "design-doc"
  "onepipeline.persona": "../personas/design-doc.yaml"
  "onepipeline.agent_graph": "graphs/design-doc.yaml"
  "onepipeline.execution_checkout": "ai-orchestrator-isolated"
  "onepipeline.repo": "ai-orchestrator"
---

# Give the read API a paginated node listing

Plan project: authoring:read-api-paginated-listing

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

## What this dispatch owes

**Everything above is the brief a planner was given, and none of it is this dispatch's
acceptance criteria.** Writing the plan was another node's job and it is already done.
The brief is here because the document opens with what is being built and why, and the
manager's own words are where those two come from. Where anything above and anything
below disagree about what this dispatch owes, below wins.

What this dispatch owes is the one short document a person reviews that plan as, instead
of reading it node by node.

Read the whole plan out of the plan store — the project record and every one of its
tasks. The plan is
`authoring:read-api-paginated-listing`.
`onetaskgraph` is that store's command line, and `--help` documents what it can do.

The document itself is stated in the `ai-orchestrator` orchestration repository, at
`config/design-doc-template.md`.
That file states its sections, their order, the reader it is written for, and every
property it is judged on, and it is the only statement of any of that — so read it before
writing anything and follow it exactly, and where anything else disagrees with it about
the document, that file wins.

## Acceptance criteria for this dispatch

- One document exists, written to that template: its sections, in that file's order, and
  no others, satisfying every property it states of them.
- That document is stored as a document of that plan's own project, in the same plan
  store the plan itself is in, so a reader finds it beside the plan rather than in a
  directory only this dispatch knows about.
- Every task of that plan has one row in the document's planned-tasks table, and each row
  points at its task using the location the store reports for that task — read back out
  of the store, never composed by hand.
- This dispatch reports where the stored document is, in the form the store reports it: a
  link where the store puts it on a website, a path where it puts it in a file on this
  machine.
- Every claim this dispatch makes about the finished work is true of the tree as it
  finally stands.
