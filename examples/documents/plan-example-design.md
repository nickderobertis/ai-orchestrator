---
title: 'Design: plan-example'
project: plan-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:7543864a301acea3a06b63a9cda7d47b585511926f55b6d379566db4530ee0a8
  onetaskgraph.template:
    answers_digest: sha256:d9e0b12e23c99d355e484ed57ec15bd1197bfb30ad149e455e326ca9ebff63f5
    body_digest: sha256:34dbf8331736c98143689caf72defbaf36bb1711d3dbe27f769aad7ff2fdd7ea
    digest: sha256:40ceac966dad3e35698a79077e2f0faaa59d9373ca5bb912dfde7ffa0469686d
    template: onepipeline:design-doc
  "orchestrator.design-approval": {"approved_at":"2026-10-01T13:24:52.302482+00:00","key":"275b8085abf88a540855e75cdd478103f0ea1a9c04bd01e7bea2576cb431def5"}
---
## What

A URL shortener: somewhere to turn a long link into a short one, and somewhere to follow
a short one back.

## Why

Links that have to be shared, typed, or printed are unusable at their full length, and
there is nothing here today that shortens them.

## Architecture

Three pieces, split so two of them can be built at once. One agent settles the design —
the API, the storage, and the line between the two halves — and the API and the web UI
are then built in parallel against it. Documentation and a review follow both.

### API — `shortener` (`api`)

The API creates short links, stores them, and resolves them back.

#### The storage schema

**Reversibility: high cost** — stored links outlive every version of the code that writes them, and that data cannot be rewritten once people have shared the links.

A stored link is its short code and the long URL it points at.

```sql
CREATE TABLE links (
  code TEXT PRIMARY KEY,
  url  TEXT NOT NULL
);
```

**Reversible:**

- **The HTTP API.** Two operations, `POST /links` to create a short link and `GET /<code>` to follow one, called only by this plan's UI.

### Web UI — `shortener` (`ui`)

One page creates a link through the API and shows the short form.

### Documentation — `shortener` (`README.md`)

The README says what the service is, how to run it, and what its API is.

## Acceptance criteria

- A person can create a short link and follow it.
- The API and the UI hold to the design the first task writes.
- The README says what the service is, how to run it, and what its API is.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| design | API | the API, the storage, and the module boundaries | none | examples/tasks/plan-example/design.md |
| api | API | the create and resolve endpoints, with tests | design | examples/tasks/plan-example/api.md |
| ui | Web UI | the page that creates a link and shows it | design | examples/tasks/plan-example/ui.md |
| docs | Documentation | the README | api, ui | examples/tasks/plan-example/docs.md |
| review | API | verified, severity-ranked findings | api, ui | examples/tasks/plan-example/review.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- A person can create a short link and follow it.
- The API and the UI hold to the design the first task writes.
- The README says what the service is, how to run it, and what its API is.
architecture: |-
  Three pieces, split so two of them can be built at once. One agent settles the design —
  the API, the storage, and the line between the two halves — and the API and the web UI
  are then built in parallel against it. Documentation and a review follow both.
planned_tasks:
- delivers: the API, the storage, and the module boundaries
  depends_on: none
  location: examples/tasks/plan-example/design.md
  task: design
  unit: API
- delivers: the create and resolve endpoints, with tests
  depends_on: design
  location: examples/tasks/plan-example/api.md
  task: api
  unit: API
- delivers: the page that creates a link and shows it
  depends_on: design
  location: examples/tasks/plan-example/ui.md
  task: ui
  unit: Web UI
- delivers: the README
  depends_on: api, ui
  location: examples/tasks/plan-example/docs.md
  task: docs
  unit: Documentation
- delivers: verified, severity-ranked findings
  depends_on: api, ui
  location: examples/tasks/plan-example/review.md
  task: review
  unit: API
units:
- decisions:
  - artifact: |-
      ```sql
      CREATE TABLE links (
        code TEXT PRIMARY KEY,
        url  TEXT NOT NULL
      );
      ```
    justification: stored links outlive every version of the code that writes them, and that data cannot be rewritten once people have shared the links.
    name: The storage schema
    summary: A stored link is its short code and the long URL it points at.
  name: API
  part: api
  repository: shortener
  reversible:
  - text: Two operations, `POST /links` to create a short link and `GET /<code>` to follow one, called only by this plan's UI.
    title: The HTTP API
  summary: The API creates short links, stores them, and resolves them back.
- decisions: []
  name: Web UI
  part: ui
  repository: shortener
  reversible: []
  summary: One page creates a link through the API and shows the short form.
- decisions: []
  name: Documentation
  part: README.md
  repository: shortener
  reversible: []
  summary: The README says what the service is, how to run it, and what its API is.
what: |-
  A URL shortener: somewhere to turn a long link into a short one, and somewhere to follow
  a short one back.
why: |-
  Links that have to be shared, typed, or printed are unusable at their full length, and
  there is nothing here today that shortens them.
-->
