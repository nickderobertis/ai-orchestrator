---
title: The read API draws a run page's first screen without reading every record of the run whole
status: todo
metadata:
  "onepipeline.goal": {"text":"Give the engine one read that answers a run page's first screen without reading the payloads that screen does not draw, for the read API to adopt in a plan of its own."}
  onepipeline.schema_version: 3
---
Execution plan first-screen-read-contract.

## What this plan delivers

**Requested** — the user, in the brief: the read API (`onepipeline-ui`) opens a run page by
reading every record of the run whole, and a run with 40,000 records takes 30 seconds to
show its first screen. Give the engine one read that answers the first screen without
reading the payloads that screen does not draw, and keep every existing engine read
answering as it does now; the read API adopts the new read in a plan of its own.

**Decided**

- The engine (`onepipeline`) owns its record store, its record kinds and how a run's state
  is folded. The read API owns the run page: what its first screen draws and how many rows
  it shows.
- This plan holds the three engine contracts the first-screen read went through, one node
  each, so one review reads all three: `eng-first-screen-stub-kinds`,
  `eng-first-screen-owned` and `eng-heads-and-fetch`.
- The manager ruled, answering question 2 of the planner's batch, to keep
  `Selection::FirstScreen { node, node_latest, stub_kinds }` as the contract
  `eng-first-screen-stub-kinds` lands, with the read API passing the engine record kinds
  it wants stubbed through `stub_kinds`.
- The planner decided `eng-first-screen-owned`'s contract, in which the engine selects the
  records a first screen draws, and `eng-heads-and-fetch`'s, in which the engine lists a
  payload-free head for every record and fetches whole records by handle.
