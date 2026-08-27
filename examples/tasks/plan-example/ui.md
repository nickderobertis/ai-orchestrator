---
title: "ui"
project: "plan-example"
status: "todo"
depends_on: ["plan-example/design"]
metadata:
  "onepipeline.id": "ui"
  "onepipeline.persona": "engineer"
---

## What
Build the URL-shortener web UI per `docs/design.md`: a form to create a short link and a view of the result. Match the existing component patterns and keep it accessible.

## Why
Without a UI the service is reachable only by hand-written HTTP calls, which is not how the people it is for will use it.

## Acceptance criteria
- A user-facing test creates a short link by driving the rendered form the way a person would.
- The form and result view carry the labels, roles, and keyboard paths the existing components use.
- The components follow the existing design-system patterns rather than introducing new ones.
