---
title: 'feat(store): select every record a run page''s first screen draws'
status: todo
project: first-screen-read-contract
metadata:
  onepipeline.id: eng-first-screen-owned
  onepipeline.max_turns: 100
  onepipeline.persona: engineer
  "orchestrator.budgets": []
repositories:
- github.com/nickderobertis/onepipeline
---
## What

Add one selection to the engine's run-record read, which the read API calls to draw a run
page's first screen in one request. In `onepipeline_store::read`:

```rust
pub const FIRST_SCREEN_ROWS: usize = 40;

pub enum Selection {
    // every existing variant, unchanged
    FirstScreen { node: Option<NodeId> },
}
```

The engine answers with the run's folded state and every record whose payload a first
screen draws, whole: the latest `FIRST_SCREEN_ROWS` records of the named node, or of the
run when no node is named, of the kinds the run page shows in its first rows. Every other
record comes back as a stub (kind, node, time and size, no payload). The engine decides
which kinds a first screen draws and how many rows it shows, so the read API passes only
the node.

## Why

A run with 40,000 records takes 30 seconds to open in the read API, because its run page
reads every record whole, payloads it never draws included. The user wants the engine to
offer a read that leaves those payloads unread, with every existing engine read unchanged;
the read API adopts it in a plan of its own.

## Acceptance criteria

- A test reads a recorded run of 40,000 records, of every record kind and several nodes,
  with `onepipeline_store::read` and `Selection::FirstScreen`, once naming a node and once
  naming none, and asserts the answer is the folded state followed by every record of the
  run in the store's order, none omitted; the latest `FIRST_SCREEN_ROWS` records of the
  named node, or of the run, of every drawn kind whole; and every other record as a stub
  with its kind, node, time and size and no payload.
- The same test counts the payload bytes the store reads, and asserts no stubbed record's
  payload was read.
- The store's existing read tests pass unchanged, so every existing `Selection` variant
  answers as before, and the lint over the changed code is green on the finished tree.
- Every claim the dispatch's report makes about what it built and verified is true of the
  finished tree.

## Additional info

The read API adopts the selection in a plan of its own after this one lands; this plan changes
nothing on the host that runs the read API.
