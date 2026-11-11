---
title: 'feat(store): list a payload-free head for every record and fetch records by handle'
status: todo
project: first-screen-read-contract
metadata:
  onepipeline.id: eng-heads-and-fetch
  onepipeline.max_turns: 100
  onepipeline.persona: engineer
  "orchestrator.budgets": []
repositories:
- github.com/nickderobertis/onepipeline
---
## What

Add two general reads to the engine's run-record store, which the read API composes to
draw a run page's first screen. In `onepipeline_store::read`:

```rust
pub struct RecordHead {
    pub handle: RecordHandle,
    pub kind: RecordKind,
    pub node: Option<NodeId>,
    pub at: Timestamp,
    pub size: u64,
}

pub enum Selection {
    // every existing variant, unchanged
    Heads,
}

pub fn fetch(run: &RunId, handles: &[RecordHandle]) -> Result<Vec<Record>, ReadError>;
```

- `Selection::Heads` answers with the run's folded state and a payload-free
  `RecordHead` for every record of the run, in order.
- `fetch` answers with the whole record for each handle, in the order given, and refuses
  a handle the run does not hold, naming it.

The read API, which owns the run page, reads the heads and chooses which records its first
screen draws whole and how many rows it shows; the engine chooses nothing about the screen.

## Why

A run with 40,000 records takes 30 seconds to open in the read API, because its run page
reads every record whole, payloads it never draws included. The user wants the engine to
offer a read that leaves those payloads unread, with every existing engine read unchanged;
the read API adopts it in a plan of its own.

## Acceptance criteria

- A test reads a recorded run of 40,000 records, of every record kind and several nodes,
  with `onepipeline_store::read` and `Selection::Heads`, and asserts the answer is the
  folded state followed by one head per record of the run in the store's order, each head's
  handle, kind, node, time and size matching its record, and counts the payload bytes the
  store reads to assert no payload was read.
- The same test fetches a set of handles given out of the store's order, and asserts
  `fetch` answers with exactly those records, whole, in the order given.
- `fetch` naming a handle the run does not hold is refused naming that handle.
- The store's existing read tests pass unchanged, so every existing `Selection` variant
  answers as before, and the lint over the changed code is green on the finished tree.
- Every claim the dispatch's report makes about what it built and verified is true of the
  finished tree.

## Additional info

The read API adopts both reads in a plan of its own after this one lands; this plan changes
nothing on the host that runs the read API.
