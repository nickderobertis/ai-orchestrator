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
  spawned as a subprocess by onejudge once per protocol step, so an `orchestrator`
  import there charges every step of every journey for onejudge_sdk, jsonschema,
  asyncio, and yaml. A constant it shares with production is restated and held by
  the drift gate in `tests/test_fake_backend_contract.py`. A journey that needs a
  node in flight holds it at a `Rendezvous` (`tests/e2e/rendezvous.py`) rather than
  asking the double to sleep. There is one such mechanism, keyed by agent turn, and
  a second one is what makes the next test reach for `time.sleep` instead.
- **A fixture that encodes an external contract gets one home.** `harness_records.py`
  holds the real oneharness provider records, captured from live turns, and both the
  unit and e2e smoke suites derive from it. Modules at `tests/` are importable by
  bare name from `tests/e2e/` too; restating such a shape in a suite is how one suite
  keeps passing against a record the provider no longer writes.
- **Unit tests inject the runner.** The scheduler (`run_plan`) takes the dispatch
  function as an argument, so scheduling behavior (ordering, parallelism,
  skip-on-failure) is tested without spawning onejudge. Prove parallelism with a
  `threading.Barrier`, not a sleep.
- **Wait on a fact, and assert one.** A rendezvous that sleeps and then assumes the
  work has begun measures the machine rather than the code, and so does an assertion
  that only holds when the box was quick. Wait for an observable state transition,
  preserve production ordering in protocol doubles, and correlate fields from the
  same event rather than independent log-wide matches.
- **Only a surface that waits may be replied to.** `just channel-next` delivers
  every planner surface, but `just channel-reply` lands only where a reader is
  parked for it — the supervisor question an orchestrator turn ends with. The
  proposal a node raises as it settles is read by a poller instead, so a reply to
  one is a race rather than a property, and holding the round open does not change
  that. Keep such a proposal out of a journey with `no-assessment`.
- **Nothing a test starts may outlive it.** `leak_guard.py` is a pytest plugin that
  reaps a test's process trees and fails the test on what it could not. Build a
  realistic tree with `process_tree.write_orphaning_tree`, and ask
  `process_tree.is_running` whether a process is gone rather than looking for
  `/proc/<pid>`.
- **A test must not leave a background daemon behind.** `NX_DAEMON=false` is set for
  the whole session and `scripts/nx.sh` defaults it off for every caller; anything
  else a test starts in the background owes the same.
- **A test that reads this repository's prose declares it.** `orchestrator:test` is
  keyed on the workspace minus its documentation, so an undeclared read would let a
  documentation edit replay a stale verdict. The autouse guard in `conftest.py`
  fails such a test; mark it `@pytest.mark.reads_docs` and it runs in
  `orchestrator:test-docs`, which keeps the whole-workspace key.
- **A test whose subject is the process itself declares that, not a weaker
  assertion.** An xdist worker always carries execnet's receiver thread, so a test
  that blocks or re-raises a process-directed signal cannot survive one at any
  worker count. Mark it `@pytest.mark.single_threaded` and it runs in
  `orchestrator:test-serial`, a tier of its own that blocks nothing and measures
  coverage into its own data file.
- **A constraint between two tests is declared, not timed around.** A journey that
  starts several real processes and waits for a readiness handshake between them
  cannot be co-scheduled with another of its kind: they contend for the same cores
  and the same advisory locks, and the handshake is what gives way. Mark it
  `@pytest.mark.load_sensitive` and the whole family runs on one xdist worker under
  `--dist loadgroup`, so no two are ever in flight at once. Scaling the hang guard
  up instead only moves the failure; a solo re-proof by hand proves nothing about
  the next run. `tests/test_nx_cache_scope.py` fails a new journey of that shape
  that has not joined the family.
- Every orchestrator verb needs a real e2e journey here before it is done.
