---
title: 'Design: scheduler-research'
project: scheduler-research
metadata:
  onetaskgraph.origin: orchestrator-record-staging:511628c22c97dd8b34beb643fd9f28514f5fb04c6aed6e0bbd4c9267edd81f53
  "onetaskgraph.template": {"template":"onepipeline:design-doc","digest":"sha256:74e0141a235840245424bb4a58718233eda51d093703e25db52bfcc1fd41122d","body_digest":"sha256:69f4d29cf681ce24ef8ed77bd8db8d5d71430b16517f2573f3b95902e005701f","answers_digest":"sha256:b723efea4bd5818404994f9734da4c47aec27f4591465f48dd2f000c6c1041e6"}
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

<details>
<summary>All 1 planned tasks</summary>

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| research | Scheduler notes | the written account of the selection rule | none | examples/tasks/scheduler-research/research.md |

</details>


<!-- onetaskgraph:template-answers
acceptance_criteria:
- The note names the selection rule and the code that implements it.
- Every claim in it points at a file and a symbol somebody can open.
accepted_opaque: []
architecture: |-
  Nothing is built. One agent reads the scheduler and writes what it found beside the code
  it describes, so the note moves with it.
budgets: []
dag_image: ''
delivers: {}
goals: []
graph: {}
plan_budgets:
  checklist:
  - budget: ''
    concern: latency
    in_scope: false
    not_applicable: n/a because the plan writes one note and changes no code, so nothing latency measures moves.
    summary: The plan writes one note and changes no code latency measures.
  - budget: ''
    concern: quota and rate-limit headroom
    in_scope: false
    not_applicable: n/a because the plan writes one note and changes no code, so nothing quota and rate-limit headroom measures moves.
    summary: The plan writes one note and changes no code quota and rate-limit headroom measures.
  - budget: ''
    concern: scaling with data
    in_scope: false
    not_applicable: n/a because the plan writes one note and changes no code, so nothing scaling with data measures moves.
    summary: The plan writes one note and changes no code scaling with data measures.
  - budget: ''
    concern: spend
    in_scope: false
    not_applicable: n/a because the plan writes one note and changes no code, so nothing spend measures moves.
    summary: The plan writes one note and changes no code spend measures.
  - budget: ''
    concern: resource use
    in_scope: false
    not_applicable: n/a because the plan writes one note and changes no code, so nothing resource use measures moves.
    summary: The plan writes one note and changes no code resource use measures.
  overview: Read the scheduler and write one note, `docs/scheduler-notes.md`, stating how it picks the next runnable node, with every claim traced to the code.
  realistic_data: []
  repo_wide_effects: []
  schema_version: 2
  sizing: One note of one or two pages, read by the two or three engineers who change the scheduler, about a scheduler of roughly 1,500 lines.
  spike_findings: []
  ten_x: Nothing the product owner notices. The plan changes no code path, so no runtime cost moves; ten times the readers read the same short note. A scheduler ten times larger would make the note longer to write, which is this one dispatch's cost and not something anybody waits on.
  ten_x_summary: 'Nothing a reader notices: ten times the readers still read one short note, and no code path changes.'
  workload: One research dispatch reading a scheduler of roughly 1,500 lines across about 6 files, and writing one note of one or two pages, read by the 2 or 3 engineers who change the scheduler, a few times a quarter.
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
visual_changes: []
what: |-
  One written account of how the scheduler decides which node to run next, kept as a note
  in the repository the scheduler lives in.
why: |-
  The next person to change the scheduler needs that rule written down. Today they have to
  rediscover it from the code, under whatever time pressure sent them there.
-->
