---
title: scheduler-research
status: todo
metadata:
  onepipeline.goal:
    text: Understand how the scheduler picks the next runnable node
  onepipeline.schema_version: 3
  onetaskgraph.template:
    answers_digest: sha256:22cfbe1932c96f772c0b3765d20212842c80e7f2d95c18b30f19ace6b69fb10b
    body_digest: sha256:df8f0682ece05d9ef1b0dce05c8a14f08c10d59ab9c3b71647a1ebbe7175ae29
    digest: sha256:f06000650b079fef3e2586ce346781db0d2a73aa0cda518fc827d41a8404fd3e
    template: onepipeline:plan-description
  orchestrator.plan-budgets:
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
---
Read the scheduler and write one note, `docs/scheduler-notes.md`, stating how it picks the next runnable node, with every claim traced to the code.

## Budgets

**What we're sizing for.** One note of one or two pages, read by the two or three engineers who change the scheduler, about a scheduler of roughly 1,500 lines.

### Workload

One research dispatch reading a scheduler of roughly 1,500 lines across about 6 files, and writing one note of one or two pages, read by the 2 or 3 engineers who change the scheduler, a few times a quarter.

### At 10x realistic usage

**Nothing a reader notices: ten times the readers still read one short note, and no code path changes.**

Nothing the product owner notices. The plan changes no code path, so no runtime cost moves; ten times the readers read the same short note. A scheduler ten times larger would make the note longer to write, which is this one dispatch's cost and not something anybody waits on.

### Checklist

- **latency:** not budgeted. n/a because the plan writes one note and changes no code, so nothing latency measures moves.
- **quota and rate-limit headroom:** not budgeted. n/a because the plan writes one note and changes no code, so nothing quota and rate-limit headroom measures moves.
- **scaling with data:** not budgeted. n/a because the plan writes one note and changes no code, so nothing scaling with data measures moves.
- **spend:** not budgeted. n/a because the plan writes one note and changes no code, so nothing spend measures moves.
- **resource use:** not budgeted. n/a because the plan writes one note and changes no code, so nothing resource use measures moves.

### Repo-wide effects


### Realistic data

This plan makes no data realistic.

### Spike findings

No spike finding changed this plan.


<!-- onetaskgraph:template-answers
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
-->
