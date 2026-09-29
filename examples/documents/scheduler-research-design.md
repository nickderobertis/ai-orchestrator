---
title: 'Design: scheduler-research'
project: scheduler-research
metadata:
  onetaskgraph.origin: orchestrator-record-staging:511628c22c97dd8b34beb643fd9f28514f5fb04c6aed6e0bbd4c9267edd81f53
  "orchestrator.design-approval": {"approved_at":"2026-09-29T05:14:15.937105+00:00","key":"7de05408153c8f34ec5de4325433b422257a23d8ec8de9522a45009712b416d7"}
  "onetaskgraph.template": {"answers_digest":"sha256:2ea004e8b1999d99bd2547bbf64fa814fe0939c946ecaf999d5a3617d2798beb","body_digest":"sha256:22a18b7d5b9351e84cd6e8e465f400bd8a9619a8769b89b3502ca5186ef3bc68","digest":"sha256:ee358d23284193c8fe3b8a0c03f79076d50584241527f148c6e00c33a8146515","template":"onepipeline:design-doc"}
---
## What

One written account of how the scheduler decides which node to run next, kept as a note
in the repository the scheduler lives in.

## Why

The next person to change the scheduler needs that rule written down. Today they have to
rediscover it from the code, under whatever time pressure sent them there.

## Architecture

Nothing is built. One agent reads the scheduler and writes what it found to
`docs/scheduler-notes.md`, beside the code it describes, so the note moves with it.

## Contracts

- None. The note is prose in one repository; nothing reads it but a person.

## Acceptance criteria

- The note names the selection rule and the code that implements it.
- Every claim in it points at a file and a symbol somebody can open.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| research | the written account of the selection rule | none | examples/tasks/scheduler-research/research.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- The note names the selection rule and the code that implements it.
- Every claim in it points at a file and a symbol somebody can open.
architecture: |-
  Nothing is built. One agent reads the scheduler and writes what it found to
  `docs/scheduler-notes.md`, beside the code it describes, so the note moves with it.
contracts:
- None. The note is prose in one repository; nothing reads it but a person.
planned_tasks:
- delivers: the written account of the selection rule
  depends_on: none
  location: examples/tasks/scheduler-research/research.md
  task: research
what: |-
  One written account of how the scheduler decides which node to run next, kept as a note
  in the repository the scheduler lives in.
why: |-
  The next person to change the scheduler needs that rule written down. Today they have to
  rediscover it from the code, under whatever time pressure sent them there.
-->
