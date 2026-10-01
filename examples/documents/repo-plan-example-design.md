---
title: 'Design: repo-plan-example'
project: repo-plan-example
metadata:
  onetaskgraph.origin: orchestrator-record-staging:102de0e7ec9d17f43c286db96673ed970aa1e0b2d444f9ee5085351344f8eee5
  onetaskgraph.template:
    answers_digest: sha256:eeb247a0bc3a0806ab94342d0fb563357b1e026c498c4e4a053e22701743c0b6
    body_digest: sha256:c63ddbcd802a695af855645767f670afaa8e671348e65d08f9ec2c1f39b0b5a8
    digest: sha256:40ceac966dad3e35698a79077e2f0faaa59d9373ca5bb912dfde7ffa0469686d
    template: onepipeline:design-doc
  "orchestrator.design-approval": {"approved_at":"2026-10-01T13:24:54.007902+00:00","key":"ba78575ae5099549e01a4c4d93facff322e73696ab5fbd616ad5feea484bfbdb"}
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

Four pieces of work across three repositories. The endpoint lands first because everything
else reads it; the client method and the dashboard are then built at the same time, and the
documentation lands beside them. The dashboard carries a person's approval inside its own
work, so the approval happens on the branch the work is on.

### Service — `some-service`

The service gains the health endpoint, and its admin dashboard shows what that endpoint returns to viewers allowed to see it.

#### The health response

**Reversibility: high cost** — the client library, the dashboard and the documentation all read it, and the client library's own users are people we cannot list.

One response shape, published by the service and read by every other unit.

```json
{"status": "ok", "version": "1.4.2"}
```

**Reversible:**

- **Dashboard access.** The admin page refuses a viewer without the `admin` role.

### Client library — `some-client`

The client library gains a method that calls the endpoint.

#### The `health()` method

**Reversibility: high cost** — the library is released, and every caller of a released method would have to change.

A new public method returning the parsed health response.

```diff
 class Client:
+    def health(self) -> Health: ...
```

### Documentation — `some-docs`

The published documentation describes the endpoint that actually exists.

## Acceptance criteria

- Polling the service says whether it is up.
- The client library exposes that answer as a method.
- The dashboard shows every field the response carries, and refuses a viewer who may not
  see it.
- The documentation describes the endpoint that actually exists.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| feat(api): add /health endpoint | Service | the endpoint | none | examples/tasks/repo-plan-example/api.md |
| feat(client): add health() method | Client library | the client method | the endpoint | examples/tasks/repo-plan-example/client.md |
| feat(admin): add dashboard | Service | the page, and a person's approval of it | the endpoint | examples/tasks/repo-plan-example/dashboard.md |
| docs(api): document /health | Documentation | the published description | none | examples/tasks/repo-plan-example/docs.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- Polling the service says whether it is up.
- The client library exposes that answer as a method.
- |-
  The dashboard shows every field the response carries, and refuses a viewer who may not
  see it.
- The documentation describes the endpoint that actually exists.
architecture: |-
  Four pieces of work across three repositories. The endpoint lands first because everything
  else reads it; the client method and the dashboard are then built at the same time, and the
  documentation lands beside them. The dashboard carries a person's approval inside its own
  work, so the approval happens on the branch the work is on.
planned_tasks:
- delivers: the endpoint
  depends_on: none
  location: examples/tasks/repo-plan-example/api.md
  task: 'feat(api): add /health endpoint'
  unit: Service
- delivers: the client method
  depends_on: the endpoint
  location: examples/tasks/repo-plan-example/client.md
  task: 'feat(client): add health() method'
  unit: Client library
- delivers: the page, and a person's approval of it
  depends_on: the endpoint
  location: examples/tasks/repo-plan-example/dashboard.md
  task: 'feat(admin): add dashboard'
  unit: Service
- delivers: the published description
  depends_on: none
  location: examples/tasks/repo-plan-example/docs.md
  task: 'docs(api): document /health'
  unit: Documentation
units:
- decisions:
  - artifact: |-
      ```json
      {"status": "ok", "version": "1.4.2"}
      ```
    justification: the client library, the dashboard and the documentation all read it, and the client library's own users are people we cannot list.
    name: The health response
    summary: One response shape, published by the service and read by every other unit.
  name: Service
  part: ''
  repository: some-service
  reversible:
  - text: The admin page refuses a viewer without the `admin` role.
    title: Dashboard access
  summary: The service gains the health endpoint, and its admin dashboard shows what that endpoint returns to viewers allowed to see it.
- decisions:
  - artifact: |-
      ```diff
       class Client:
      +    def health(self) -> Health: ...
      ```
    justification: the library is released, and every caller of a released method would have to change.
    name: The `health()` method
    summary: A new public method returning the parsed health response.
  name: Client library
  part: ''
  repository: some-client
  reversible: []
  summary: The client library gains a method that calls the endpoint.
- decisions: []
  name: Documentation
  part: ''
  repository: some-docs
  reversible: []
  summary: The published documentation describes the endpoint that actually exists.
what: |-
  A health endpoint on the service, the client method that calls it, an admin page that
  shows what it returns, and the documentation for all three — each landing in the
  repository it belongs to.
why: |-
  Operators can already be told whether the service is up, but only from a terminal. The
  people who need that answer during an incident are reading raw JSON, or asking somebody
  who can.
-->
