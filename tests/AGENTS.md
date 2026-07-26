# tests/AGENTS.md

Conventions for this repo's tests.

- **e2e (`tests/e2e/`) drives the real onejudge CLI.** Use the `command_base`
  fixture: it builds a base whose provider is onejudge's `command` provider,
  pointed at `fake_backend.py`. That fakes only the paid model/harness — the one
  sanctioned mock. Never mock the merge, the dispatch, onejudge itself, or the
  report parsing; assert on the real `Report` / exit code.
- **`fake_backend.py` is a protocol double, not a test.** It speaks onejudge's
  JSON-lines protocol and is steered by task sentinels (`should-fail`,
  `complete-now`). Keep it deterministic and dependency-free (stdlib only) — it is
  spawned as a subprocess by onejudge.
- **A fixture that encodes an external contract gets one home.** `harness_records.py`
  holds the real oneharness provider records, captured from live turns, and both the
  unit and e2e smoke suites derive from it. Modules at `tests/` are importable by
  bare name from `tests/e2e/` too; restating such a shape in a suite is how one suite
  keeps passing against a record the provider no longer writes.
- **Unit tests inject the runner.** The scheduler (`run_plan`) takes the dispatch
  function as an argument, so scheduling behavior (ordering, parallelism,
  skip-on-failure) is tested without spawning onejudge. Prove parallelism with a
  `threading.Barrier`, not a sleep.
- Every orchestrator verb needs a real e2e journey here before it is done.
