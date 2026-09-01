---
title: 'Design: repo-plan-example'
project: repo-plan-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:repo-plan-example-design
  orchestrator.design-approval:
    approved_at: 2026-09-01T15:56:31.951721+00:00
    key: cb7c1f97a111840983ef3cdb209a56e259f2d62c25427626cb1158753bb7128c
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

**The health response.** One shape, published by the service and consumed by the client
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
