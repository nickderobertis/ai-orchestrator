## What
Recover and publish the ALREADY-COMPLETED "persist run artifacts + results view" feature. It is fully built on the preserved lifecycle branch `ai-orchestrator/engineer/eb89f33e-0d64b96f79` (10 commits, `19d03c0..03114ac`). **Do NOT reimplement it.** Your job is integration + stabilization only:

1. Reuse the finished work: from your working branch cut off current `main`, merge the preserved branch `ai-orchestrator/engineer/eb89f33e-0d64b96f79` to bring in all its commits (`git merge ai-orchestrator/engineer/eb89f33e-0d64b96f79`). The feature adds `orchestrator/results.py`, a `just results` recipe, per-node artifact paths recorded on `GraphResultItem`, and full gate-log persistence — that content must survive intact.
2. Resolve the merge conflict with current `main` in `tests/e2e/test_live_edit_e2e.py` (and any other conflicting files) by keeping BOTH the feature's behavior/assertions AND main's e2e-timing adjustments from `c9ca41b test: scale remaining e2e channel waits`. Do not drop either side's intent.
3. Eliminate the timing-sensitive flake in `test_real_cli_recovers_settled_lifecycle_stack_anchor` — it fails only in the full suite and passes in isolation. Make it robust (do not delete it, do not weaken coverage). Run the full suite several times to confirm it is stable.
4. Get `just gate` fully green with the enforced 95% line coverage held, then publish through the registered workflow (local direct merge to `main`).

## Why
The feature was built correctly and completely, but it failed to *integrate* solely because `origin/main` moved during the original run and the e2e test files collided — an integration/merge failure, not a quality failure. The user explicitly wants the finished work salvaged and published rather than thrown away and redone from scratch. Redoing ~3 hours of completed work would be pure waste.

## Acceptance criteria
- The results feature from the preserved branch is present and unmodified in intent: `orchestrator/results.py` exists, `just results <run>` works, per-node result records carry artifact/log paths, and full gate output is persisted to a retrievable path.
- All merge conflicts with current `main` are resolved, preserving both the feature and main's concurrent changes; the tree is clean.
- `test_real_cli_recovers_settled_lifecycle_stack_anchor` no longer flakes when the full suite runs (demonstrate by running the complete suite multiple times).
- `just gate` passes with 95% coverage held and no relevant checks skipped.
- The change is published to `main`.

## Additional info
- Preserved branch: `ai-orchestrator/engineer/eb89f33e-0d64b96f79` (reachable in the isolated execution checkout). Base: current `main` (`c9ca41b`).
- The original failure error was: `sync-conflict: could not merge current origin/main into <branch>; merge aborted and branch was not pushed`, outcome `gate-failed`, but the implementation step had completed.
- CONCURRENCY: other self-dispatch runs may land on `main` while you work. If publication loses a race (base moved again), fetch the latest `main`, re-merge/resolve conflicts, re-run `just gate`, and retry publication — do not abandon the work.
