---
title: The read API draws a run page's first screen from a selection naming the kinds to stub
status: todo
metadata:
  "onepipeline.goal": {"text":"Give the engine one read that answers a run page's first screen without reading the payloads that screen does not draw, for the read API to adopt in a plan of its own."}
  onepipeline.schema_version: 3
---
Execution plan first-screen-read-contract-user-ruled.

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
- The user ruled on the contract, in their own words, quoted from the brief: "Use
  `Selection::FirstScreen { node, node_latest, stub_kinds }` and have the read API pass
  the engine's record kinds it wants stubbed in `stub_kinds`. I've weighed the coupling
  and I want this shape; don't redesign it." `eng-first-screen-stub-kinds-user` lands
  that contract as the user worded it.
