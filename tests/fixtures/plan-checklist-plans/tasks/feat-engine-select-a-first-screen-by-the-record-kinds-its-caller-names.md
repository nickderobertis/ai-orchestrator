---
title: 'feat(store): select a run''s first screen by the record kinds its caller names'
status: todo
project: first-screen-read-contract
metadata:
  onepipeline.id: eng-first-screen-stub-kinds
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
pub enum Selection {
    // every existing variant, unchanged
    FirstScreen {
        node: Option<NodeId>,
        node_latest: usize,
        stub_kinds: Vec<RecordKind>,
    },
}
```

- `node`: the node the page opens on, or none for the run's own page.
- `node_latest`: how many of that node's latest records, or the run's when no node is
  named, come back whole, whatever their kind.
- `stub_kinds`: the engine record kinds whose payloads come back as stubs (kind, node,
  time and size, no payload). The read API names them from the engine's `RecordKind`
  enum: today `RecordKind::TurnActivity`, `RecordKind::ToolOutput` and
  `RecordKind::HarnessTranscript`, the kinds its first screen does not draw.

The answer is the run's folded state, then every record of the run in order, each whole
or stubbed as the selection says. This contract is the one the manager ruled to keep, as
the plan's **Decided** part records.

## Why

A run with 40,000 records takes 30 seconds to open in the read API, because its run page
reads every record whole, payloads it never draws included. The user wants the engine to
offer a read that leaves those payloads unread, with every existing engine read unchanged;
the read API adopts it in a plan of its own.

## Acceptance criteria

- A test reads a recorded run of 40,000 records, of every record kind and several nodes,
  with `onepipeline_store::read` and `Selection::FirstScreen`, once naming a node and once
  naming none, and asserts the answer is the folded state followed by every record of the
  run in the store's order, none omitted; the latest `node_latest` records of the named
  node, or of the run when none is named, whole with their payloads, a `stub_kinds` kind
  among them; every other record of a `stub_kinds` kind as a stub with its kind, node,
  time and size and no payload; and every record of any other kind whole with its payload.
- The same test counts the payload bytes the store reads, and asserts no stubbed record's
  payload was read.
- The store's existing read tests pass unchanged, so every existing `Selection` variant
  answers as before, and the lint over the changed code is green on the finished tree.
- Every claim the dispatch's report makes about what it built and verified is true of the
  finished tree.

## Additional info

The read API adopts the selection in a plan of its own after this one lands; this plan changes
nothing on the host that runs the read API.
