---
title: 'Design: scheduler-research'
project: scheduler-research
metadata:
  onetaskgraph.origin: orchestrator-record-staging:511628c22c97dd8b34beb643fd9f28514f5fb04c6aed6e0bbd4c9267edd81f53
  onetaskgraph.template:
    answers_digest: sha256:d369f67d344bf9de52dc439b125b5a5a87e32e68014515015f8b2af6b1a565e3
    body_digest: sha256:0f3952ac8b0fa96e84d3d49ed887b52a3de0ec1f0a6fe780f47d5109c033a308
    digest: sha256:40ceac966dad3e35698a79077e2f0faaa59d9373ca5bb912dfde7ffa0469686d
    template: onepipeline:design-doc
  "orchestrator.design-approval": {"approved_at":"2026-10-01T13:24:54.884704+00:00","key":"5976b1be52a02e6af9c15011aeae6a39218aa8d68c4b7a65ecfe43df85fbe372"}
---
## What

One written account of how the scheduler decides which node to run next, kept as a note
in the repository the scheduler lives in.

## Why

The next person to change the scheduler needs that rule written down. Today they have to
rediscover it from the code, under whatever time pressure sent them there.

## Architecture

Nothing is built. One agent reads the scheduler and writes what it found beside the code
it describes, so the note moves with it.

### Scheduler notes — `onepipeline` (`docs/scheduler-notes.md`)

A new note states the selection rule and points at the code that implements it. Nothing reads it but a person.

## Acceptance criteria

- The note names the selection rule and the code that implements it.
- Every claim in it points at a file and a symbol somebody can open.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| research | Scheduler notes | the written account of the selection rule | none | examples/tasks/scheduler-research/research.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- The note names the selection rule and the code that implements it.
- Every claim in it points at a file and a symbol somebody can open.
architecture: |-
  Nothing is built. One agent reads the scheduler and writes what it found beside the code
  it describes, so the note moves with it.
planned_tasks:
- delivers: the written account of the selection rule
  depends_on: none
  location: examples/tasks/scheduler-research/research.md
  task: research
  unit: Scheduler notes
units:
- decisions: []
  name: Scheduler notes
  part: docs/scheduler-notes.md
  repository: onepipeline
  reversible: []
  summary: A new note states the selection rule and points at the code that implements it. Nothing reads it but a person.
what: |-
  One written account of how the scheduler decides which node to run next, kept as a note
  in the repository the scheduler lives in.
why: |-
  The next person to change the scheduler needs that rule written down. Today they have to
  rediscover it from the code, under whatever time pressure sent them there.
-->
