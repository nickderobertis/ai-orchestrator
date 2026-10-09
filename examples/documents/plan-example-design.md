---
title: 'Design: plan-example'
project: plan-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:7543864a301acea3a06b63a9cda7d47b585511926f55b6d379566db4530ee0a8
  "onetaskgraph.template": {"answers_digest":"sha256:f543a08d7dc83470913ffb3267cb3e26ebce3905e8b86801cc929b7d63b55560","body_digest":"sha256:34dbf8331736c98143689caf72defbaf36bb1711d3dbe27f769aad7ff2fdd7ea","digest":"sha256:b3081e2663074adf631864ea2a51e4148e6a82407f8fc9b893f85a2c6aef6634","template":"onepipeline:design-doc"}
  "orchestrator.design-approval": {"approved_at":"2026-10-09T00:49:54.883522+00:00","key":"b08c4e54c400d00b32859769c7a87f054ddd9ea6e95441002c506244333c8bdf"}
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
budgets: []
plan_budgets:
  checklist:
  - budget: ''
    concern: latency
    in_scope: true
    not_applicable: n/a because this plan builds the service from nothing in whichever checkout launches it and names no repository to register a budget in; `design` states the resolve-latency target in `docs/design.md` instead.
    summary: No repository to register a budget in; the design states the resolve target.
  - budget: ''
    concern: quota and rate-limit headroom
    in_scope: false
    not_applicable: 'n/a because the service calls no rate-limited or metered service: it stores and reads its own links.'
    summary: The service calls no rate-limited service.
  - budget: ''
    concern: scaling with data
    in_scope: true
    not_applicable: n/a because 10,000 links of at most 2 KB each are about 20 MB, which any store the design picks holds and indexes; the 10x answer names the one risk.
    summary: About 20 MB of links at realistic use; the 10x answer names the index risk.
  - budget: ''
    concern: spend
    in_scope: false
    not_applicable: n/a because the service runs on the team's existing host and calls no paid service.
    summary: It runs on an existing host and calls no paid service.
  - budget: ''
    concern: resource use
    in_scope: false
    not_applicable: n/a because 50 resolves a second of one indexed lookup each is far below one small instance's capacity.
    summary: 50 indexed lookups a second fit one small instance.
  overview: 'Design and build a small URL-shortener service: a written design, an HTTP API to create and resolve short links, a web form to create them, a README, and an end to end review of the result.'
  realistic_data:
  - artifact: '`tests/generate_links.py`, written by the `api` node'
    choice: generator
    data: Stored short links
    reason: A generator writes 10,000 links with realistic URL lengths in seconds and costs nothing to keep current as the schema moves, where a fixture of that size would need regenerating at every schema change.
    summary: A generator, because 10,000 realistic links are cheap to generate and a fixture would go stale with the schema.
  repo_wide_effects: []
  schema_version: 2
  sizing: 'One team''s internal link shortener: about 10,000 stored links, 500 created a day and 50 resolves a second at peak.'
  spike_findings: []
  ten_x: 'At 10x, 100,000 links and 500 resolves a second, a person following a link notices the redirect getting slower first, if the store looks codes up without an index. No budget covers it yet: the plan names no repository to register one in, so the `design` node states the resolve-latency target and the index in `docs/design.md`, and the target is registered once a repository holds the service.'
  ten_x_summary: Resolves slow down as 100,000 links and 500 resolves a second meet a store with no index on the code.
  workload: About 10,000 stored links, growing by about 500 a day. Resolves peak at 50 a second, from people following links in chat and email; creates come from the web form at most a few a minute. Each link is a target URL of up to 2 KB and a 7-character code.
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
predates_budgets: ''
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
