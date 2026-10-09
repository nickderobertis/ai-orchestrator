---
title: plan-example
status: todo
metadata:
  onepipeline.concurrency: 4
  onepipeline.schema_version: 3
  onetaskgraph.template:
    answers_digest: sha256:6d314348644770d58aacc4de5107bbaed9ad928fb1103295a09fe142170a8c1d
    body_digest: sha256:870fab6f325def583dbc37fb0e29d06af8f93bd5915f6172532a9810333bc28f
    digest: sha256:f06000650b079fef3e2586ce346781db0d2a73aa0cda518fc827d41a8404fd3e
    template: onepipeline:plan-description
  orchestrator.plan-budgets:
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
---
Design and build a small URL-shortener service: a written design, an HTTP API to create and resolve short links, a web form to create them, a README, and an end to end review of the result.

## Budgets

**What we're sizing for.** One team's internal link shortener: about 10,000 stored links, 500 created a day and 50 resolves a second at peak.

### Workload

About 10,000 stored links, growing by about 500 a day. Resolves peak at 50 a second, from people following links in chat and email; creates come from the web form at most a few a minute. Each link is a target URL of up to 2 KB and a 7-character code.

### At 10x realistic usage

**Resolves slow down as 100,000 links and 500 resolves a second meet a store with no index on the code.**

At 10x, 100,000 links and 500 resolves a second, a person following a link notices the redirect getting slower first, if the store looks codes up without an index. No budget covers it yet: the plan names no repository to register one in, so the `design` node states the resolve-latency target and the index in `docs/design.md`, and the target is registered once a repository holds the service.

### Checklist

- **latency:** not budgeted. n/a because this plan builds the service from nothing in whichever checkout launches it and names no repository to register a budget in; `design` states the resolve-latency target in `docs/design.md` instead.
- **quota and rate-limit headroom:** not budgeted. n/a because the service calls no rate-limited or metered service: it stores and reads its own links.
- **scaling with data:** not budgeted. n/a because 10,000 links of at most 2 KB each are about 20 MB, which any store the design picks holds and indexes; the 10x answer names the one risk.
- **spend:** not budgeted. n/a because the service runs on the team's existing host and calls no paid service.
- **resource use:** not budgeted. n/a because 50 resolves a second of one indexed lookup each is far below one small instance's capacity.

### Repo-wide effects


### Realistic data

- **Stored short links**, generator: A generator writes 10,000 links with realistic URL lengths in seconds and costs nothing to keep current as the schema moves, where a fixture of that size would need regenerating at every schema change. Artifact: `tests/generate_links.py`, written by the `api` node

### Spike findings

No spike finding changed this plan.


<!-- onetaskgraph:template-answers
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
-->
