---
title: 'Design: tracked-release'
project: tracked-release
metadata:
  onetaskgraph.origin: orchestrator-record-staging:f9a181fad4a495bf5f9042f736a807665321e8f64d5e8eb0eceae56c9c56a6a8
  orchestrator.design-approval:
    approved_at: 2026-09-09T04:37:51.125836+00:00
    key: 29f538fced7c618193345dbc8f62d966105088fb2ad0bb1094ca0d63b7ad957b
---
## What

One release carried from a written design, through a person's approval of it, into the
service and the documentation, to a second approval and the announcement.

## Why

A release that crosses several repositories goes wrong where the parties disagree about
what was agreed. Writing the agreement down once, and having a person accept it before
anything is built, is what keeps that from happening late.

## Architecture

A line with two people in it. The design is written first; somebody approves it; the
service and the documentation are then built against it in parallel, in their own
repositories; somebody approves the result; the announcement is written last. One node
changes nothing at all and says so — it records that the readiness handoff produced no
repository change, rather than pretending to.

## Contracts

**The API and rollout contract.** Written into the design and approved by a person before
anything is built. Everything after that restates it rather than deciding it again, which
is why the approval is a node of the plan and not a conversation beside it.

## Acceptance criteria

- The API and rollout are agreed in writing before any of it is built.
- The service does what was agreed, and the documentation describes what the service does.
- Nothing is published until a person has read both.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| design | the API and rollout contract | none | examples/tasks/tracked-release/design.md |
| design-approval | a person's approval of that contract | design | examples/tasks/tracked-release/design-approval.md |
| unchanged-handoff | the recorded readiness handoff | design | examples/tasks/tracked-release/unchanged-handoff.md |
| feat: implement approved release | the service, approved in staging | design-approval | examples/tasks/tracked-release/service.md |
| docs: document the approved API and rollout | the published description | design-approval | examples/tasks/tracked-release/docs.md |
| release-approval | a person's approval to publish | service, docs | examples/tasks/tracked-release/release-approval.md |
| announce | the release announcement | release-approval | examples/tasks/tracked-release/announce.md |
