---
title: 'Design: scheduler-research'
project: scheduler-research
metadata:
  onetaskgraph.origin: orchestrator-record-staging:511628c22c97dd8b34beb643fd9f28514f5fb04c6aed6e0bbd4c9267edd81f53
  orchestrator.design-approval:
    approved_at: 2026-09-09T04:37:51.063906+00:00
    key: da60f1787f5b832f9414888320a1a47109bb881c1b68cb2b26a0375a7ad796ec
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

None. The note is prose in one repository; nothing reads it but a person.

## Acceptance criteria

- The note names the selection rule and the code that implements it.
- Every claim in it points at a file and a symbol somebody can open.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| research | the written account of the selection rule | none | examples/tasks/scheduler-research/research.md |
