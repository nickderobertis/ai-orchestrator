## What
Recover and publish the ALREADY-COMPLETED, gate-green "merge queue: all local strategies + resolve-and-requeue on conflict" feature. It is fully built on the preserved lifecycle branch `ai-orchestrator/engineer/8ff9580e-61f30eba62` (5 commits: requeue local merge conflicts, queue local recovery conflicts, preserve queued publication semantics, close merge conflict recovery paths, harden conflict recovery contracts). Its gate (including llmlint) passed before it hung in closeout. **Do NOT reimplement it.** Your job is integration + verification only:

1. Reuse the finished work: from your working branch cut off current `main`, merge the preserved branch `ai-orchestrator/engineer/8ff9580e-61f30eba62` (`git merge ai-orchestrator/engineer/8ff9580e-61f30eba62`) to bring in all its commits. The feature routes every local integration strategy through `merge_queue_turn` and turns a base-advanced content conflict into a dequeue → worker-resolve → re-enqueue loop instead of aborting — that content must survive intact.
2. Resolve the merge conflict with current `main` in `tests/e2e/test_lifecycle_e2e.py` (and any other conflicting files) by keeping BOTH the feature's behavior/assertions AND main's concurrent changes. Do not drop either side's intent. (`orchestrator/lifecycle.py`, `docs/repo-lifecycle.md`, and the unit tests auto-merge; only `test_lifecycle_e2e.py` conflicts as of the last check.)
3. Run `just gate` fully green with the enforced 95% line coverage held, then publish through the registered workflow (local direct merge to `main`).

## Why
The feature was built correctly and completely and passed its gate, but the dispatch hung during closeout before it could integrate, and meanwhile `main` advanced under it. The user wants the finished, gate-green work salvaged and published, not thrown away and redone. Redoing ~6 hours of completed work would be pure waste.

## Acceptance criteria
- The merge-queue feature from the preserved branch is present and unmodified in intent: every `workflow=local` integration path takes a `merge_queue_turn`; a base-advanced content conflict triggers dequeue → worker-resolve → fair re-enqueue → retry (bounded), not a `gate-failed`/from-scratch restart.
- All merge conflicts with current `main` are resolved, preserving both the feature and main's concurrent changes; the tree is clean.
- The existing merge-queue/conflict-recovery e2e still passes (no mocking of the queue or the merge; real bare git origin).
- `just gate` passes with 95% coverage held and no relevant checks skipped.
- The change is published to `main`.

## Additional info
- Preserved branch: `ai-orchestrator/engineer/8ff9580e-61f30eba62` (reachable in the isolated execution checkout). Base: current `main`.
- The dispatch hung after a green gate (llmlint passed at 19:27) with no integration; this recovery just needs to merge the advanced main, resolve the one e2e conflict, re-verify, and publish.
- CONCURRENCY: other self-dispatch runs may land on `main` while you work. If publication loses a race (base moved again), fetch the latest `main`, re-merge/resolve conflicts, re-run `just gate`, and retry publication — do not abandon the work.
