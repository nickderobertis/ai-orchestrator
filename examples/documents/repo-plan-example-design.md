---
title: 'Design: repo-plan-example'
project: repo-plan-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:102de0e7ec9d17f43c286db96673ed970aa1e0b2d444f9ee5085351344f8eee5
  "orchestrator.design-approval": {"approved_at":"2026-09-29T05:14:15.728218+00:00","key":"e192742c26e65866efaf2f8e104bffcb48eea142edf74c5f8cc414af2defa79d"}
  "onetaskgraph.template": {"answers_digest":"sha256:651893d78341377652f583ab8a29ad55d5ec2f84347b63e44be46e6f68d4756b","body_digest":"sha256:117a01b426b9470d662d314c8312748f6ced1b23fabfd7ca6193bb15f12b60ea","digest":"sha256:ee358d23284193c8fe3b8a0c03f79076d50584241527f148c6e00c33a8146515","template":"onepipeline:design-doc"}
---
## What

A health endpoint on the service, the client method that calls it, an admin page that
shows what it returns, and the documentation for all three — each landing in the
repository it belongs to.

## Why

Operators can already be told whether the service is up, but only from a terminal. The
people who need that answer during an incident are reading raw JSON, or asking somebody
who can.

## Architecture

Four repositories, one plan. The endpoint lands first because everything else reads it;
the client method and the dashboard are then built at the same time, each in its own
repository, and the documentation lands beside them. The dashboard is one piece of work
with a person's approval inside it rather than a separate node, so the approval happens
on the branch the work is on.

## Contracts

- **The health response.** One shape, published by the service and consumed by the client
  library, the dashboard, and the documentation — three consumers in three repositories, so
  changing it later means changing all of them at once.

## Acceptance criteria

- Polling the service says whether it is up.
- The client library exposes that answer as a method.
- The dashboard shows every field the response carries, and refuses a viewer who may not
  see it.
- The documentation describes the endpoint that actually exists.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| feat(api): add /health endpoint | the endpoint | none | examples/tasks/repo-plan-example/api.md |
| feat(client): add health() method | the client method | the endpoint | examples/tasks/repo-plan-example/client.md |
| feat(admin): add dashboard | the page, and a person's approval of it | the endpoint | examples/tasks/repo-plan-example/dashboard.md |
| docs(api): document /health | the published description | none | examples/tasks/repo-plan-example/docs.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- Polling the service says whether it is up.
- The client library exposes that answer as a method.
- |-
  The dashboard shows every field the response carries, and refuses a viewer who may not
  see it.
- The documentation describes the endpoint that actually exists.
architecture: |-
  Four repositories, one plan. The endpoint lands first because everything else reads it;
  the client method and the dashboard are then built at the same time, each in its own
  repository, and the documentation lands beside them. The dashboard is one piece of work
  with a person's approval inside it rather than a separate node, so the approval happens
  on the branch the work is on.
contracts:
- |-
  **The health response.** One shape, published by the service and consumed by the client
  library, the dashboard, and the documentation — three consumers in three repositories, so
  changing it later means changing all of them at once.
planned_tasks:
- delivers: the endpoint
  depends_on: none
  location: examples/tasks/repo-plan-example/api.md
  task: 'feat(api): add /health endpoint'
- delivers: the client method
  depends_on: the endpoint
  location: examples/tasks/repo-plan-example/client.md
  task: 'feat(client): add health() method'
- delivers: the page, and a person's approval of it
  depends_on: the endpoint
  location: examples/tasks/repo-plan-example/dashboard.md
  task: 'feat(admin): add dashboard'
- delivers: the published description
  depends_on: none
  location: examples/tasks/repo-plan-example/docs.md
  task: 'docs(api): document /health'
what: |-
  A health endpoint on the service, the client method that calls it, an admin page that
  shows what it returns, and the documentation for all three — each landing in the
  repository it belongs to.
why: |-
  Operators can already be told whether the service is up, but only from a terminal. The
  people who need that answer during an incident are reading raw JSON, or asking somebody
  who can.
-->
