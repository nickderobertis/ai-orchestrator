## What
Finish and publish the NEARLY-COMPLETE "isolate oneharness per worktree" feature. It is
already built on the preserved lifecycle branch `ai-orchestrator/engineer/91c6abef-f87d04df14`
(tip `d9beb65`, 4 commits ahead of `main`): the core fix plus most tests are committed, and a
final WIP commit adds session-setup edge-case e2e tests whose **last test is truncated/incomplete**
(the machine restarted mid-edit). **Do NOT reimplement it.** Integration + finish + verify only:

1. Reuse the finished work: from your working branch cut off current `main`, merge the preserved
   branch (`git merge ai-orchestrator/engineer/91c6abef-f87d04df14`). This brings in: `oneharness-cli`
   added to `pyproject.toml` + `uv.lock`; the global `uv tool install oneharness` path REMOVED from
   `scripts/session-setup.sh` (now `uv sync` installs it per-worktree like `onejudge`); the exact-version
   guard in `tests/conftest.py` updated to resolve the venv-local oneharness; the fake-injection fixes in
   `tests/e2e/test_telemetry_e2e.py` and `tests/test_session_setup.py`; and new e2e in
   `tests/e2e/test_session_setup_e2e.py`.
2. Finish the incomplete WIP: complete the truncated last test in `tests/e2e/test_session_setup_e2e.py`
   (it was being written to assert session-setup rejects corrupt/wrong oneharness distribution metadata).
   Make it a real, passing test. Resolve any merge conflicts with current `main`, keeping both sides' intent.
3. Run `just gate` fully green with the enforced 95% line coverage held, then publish through the
   registered workflow (local direct merge to `main`).

## Why
The version fix is the reliability foundation the rest of the program depends on: concurrent
self-dispatch runs share ONE global `~/.local/bin/oneharness` while each branch pins an exact version and
the e2e guard is exact-match, so a version adoption in one run breaks every other run's gate (it cost this
program hours today). Making oneharness a per-worktree `pyproject.toml` dependency (exactly like `onejudge`
already is) makes that structurally impossible. The work was ~90% done and gate-green in intent when a
machine restart interrupted it; the user wants it finished and landed, not redone from scratch.

## Acceptance criteria
- `oneharness-cli` is a pinned `pyproject.toml` dependency installed by `uv sync` into each worktree's
  `.venv`; a fresh `just bootstrap` performs no global `uv tool install oneharness`.
- Exactly one source of version truth for the oneharness pin (a deterministic check fails if
  `config/oneharness.version` and the pyproject pin diverge).
- The e2e exact-version guard resolves the worktree-local oneharness and passes; resolution is venv-local
  (so two worktrees pinning different versions do not interfere).
- Gate tests that inject a fake oneharness select it explicitly (no PATH-shadow reliance);
  `tests/test_session_setup.py` matches the new install path with obsolete `uv tool` tests removed, not
  left failing; the truncated WIP e2e test is completed and passing.
- `uv sync` co-resolves `oneharness-cli` + `onejudge` + orchestrator deps with no version conflict;
  `scripts/session-setup.sh` still verifies oneharness importable/runnable at the pinned version.
- `just gate` passes with 95% coverage held and no relevant checks skipped; the change is published to `main`.

## Additional info
- Preserved branch: `ai-orchestrator/engineer/91c6abef-f87d04df14` (tip `d9beb65`), reachable in the isolated
  execution checkout. Base: current `main`.
- CONCURRENCY: other self-dispatch runs may land on `main` while you work. If publication loses a race,
  fetch the latest `main`, re-merge/resolve, re-run `just gate`, and retry publication — do not abandon the work.
- The now-landed merge queue routes local publications through `merge_queue_turn`; a base-advanced conflict
  should resolve-and-requeue rather than abort.
