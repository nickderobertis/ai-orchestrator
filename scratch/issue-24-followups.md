# Follow-ups from adopting onepipeline 0.7.5 / onevcs 0.6.1

## RESOLVED: oneagentgraph is on 0.3.0, and the persona rewrite has landed.

**Resolved 2026-08-18, by adopting onepipeline 0.8.0.** This section is kept as the
history of a block, not as a standing one. What it named as the unblocking condition
— *an `onepipeline` release whose `Cargo.lock` resolves `oneagentgraph 0.3.0`* — is
exactly what v0.8.0 is, so the three moves it said had to land together did:
`config/onepipeline.version` to 0.8.0, `config/oneagentgraph.version` (and
`pyproject.toml`) to 0.3.0, and every file in `personas/` plus
`config/onejudge.base.yaml` rewritten into the new shape.

`onepipeline` v0.8.0 declares `oneagentgraph = "0.3.0"` and its lock resolves 0.3.0,
so this one moved the *requirement* as well as the resolution — unlike the onevcs
lesson beside it in `AGENTS.md`, where only the lock ever moved. Read the lock
either way; it is the resolution that decides what a dispatch reads.

### What the block was

oneagentgraph #54 (`feat!: make a persona a onejudge config fragment`, released in
v0.3.0) replaced the persona schema, and `docs/persona-format.md` at that tag says
the previous spelling "no longer loads, anywhere — not through `oneagentgraph
persona validate`, and not when a member resolves a persona as a graph runs. There
is no alias, no flag, no environment variable, and no deprecation period."

| previous | 0.3.0 |
| --- | --- |
| `agent.instructions` | `system_prompt` (top level) |
| `agent.name` | `name` (top level) |
| `user.persona` / `user.done_when` / `user.done_when_replaces_base` / `user.max_turns` | unchanged |
| `evals` | unchanged |

The same rule holds for a **base config**, which is why `config/onejudge.base.yaml`
moved in the same change.

The hazard was a **split**: `just validate-personas` runs the oneagentgraph *CLI*,
while what reads a persona at dispatch is the oneagentgraph `onepipeline` **links**.
Bump one alone and validation certifies a shape the dispatching reader refuses —
loudly, at config validation, which means `graphs/dag-scope.yaml`'s monitor and
pacemaker would have produced no member at all on every run while `validate-personas`
went on saying `OK`.

### What was re-measured at the adoption

All against the installed stack (`oneagentgraph 0.3.0`, `onepipeline 0.8.0`), with
the retired spelling as the control beside each:

1. `oneagentgraph persona validate personas` → `personas: OK`, exit 0. A file
   carrying an `agent:` block → exit 2, `` `agent` is not a persona key: a persona
   is a onejudge config fragment … There is no alias and no deprecation period ``.
2. `oneagentgraph validate` on all three of `graphs/` → `N member(s) OK`, exit 0.
3. A real `oneagentgraph run` of each member `graphs/dag-scope.yaml` names by path,
   in that document's own shape, reaching `graph-settled` with `exit_code: 0` and a
   `member-started` carrying the persona's own label. The retired spelling dies
   before any `graph-started`, exactly as it did in the other direction under 0.2.18.
4. The graph schema range and the `{task}` boundary the dag-scope document states:
   0.3.0 still reads versions 1 through 6, and still expands `{task}` from 4.

Probe 3 is not a one-off any more — `tests/e2e/test_path_dispatched_personas_e2e.py`
takes it on every gate run, which is what stops the next persona-format break from
being caught by validation that cannot see it.

### Why the other 0.7.5 measurements were carried forward rather than re-taken

The drift gates in `tests/test_onejudge_version.py` make every sentence naming the
adopted onepipeline release move with the pin, which is a prompt to re-measure and
not a licence to retype the number. Here the whole of `git diff v0.7.5..v0.8.0` is
the three persona documents onepipeline itself ships, `Cargo.toml`/`Cargo.lock`, and
its own tests — no engine, driver, channel, ledger, or environment path is touched —
so every claim those sentences carry is a claim about code that did not move. Two of
them are re-taken on every gate run anyway, by
`tests/e2e/test_orchestrate_launch_e2e.py`: the `ONEPIPELINE_RUN_ID` export an
observer member is given, and the `--config` a dispatched agent side arrives with.

Three sentences changed wording rather than only their number, in `AGENTS.md` and
`docs/repo-lifecycle.md`. Each said "since onepipeline <adopted>" about a behaviour
that arrived in 0.7.5, and the adopted release and the arrival release stopped being
the same one at 0.8.0; they were rewritten to say "on the adopted onepipeline
<adopted>", which is what the gate is actually asking them to keep true, and
`tests/test_onejudge_version.py` has moved that number with the pin at every
adoption since.

---

## A hand-rolled `pytest` here does not mean what `just check` means

Not a follow-up so much as a trap to avoid re-falling into, and an argument for the
command surface rather than an exception to it. Running
`.venv/bin/python -m pytest tests/` by hand makes
`tests/e2e/test_session_setup_e2e.py::test_session_setup_syncs_real_pinned_clis_and_then_needs_no_uv`
fail on `assert "sweep: examined family" in installed.stderr` — on a clean tree at
`8aa54a0a` as well as with these pins. `just check` passes the same test, and `just
gate` is green.

**Why.** On this host `uv` is an asdf shim (`~/.local/bin/uv`) that resolves its
version from `$HOME/.tool-versions`, and the journey deliberately points `HOME` at
`tmp_path`. The recipes reach a real `uv` ahead of that shim on `PATH`, so the sweep
runs; the hand-rolled invocation leaves the shim first, it answers `No version is set
for command uv`, `just sweep-scratch` exits 126, and the line the journey asserts on
is never printed.

So the recipe is not a convenience wrapper around the same run — it is what makes the
run mean something, which is why `AGENTS.md` says to use the `just` recipes and not
hand-roll equivalents. This cost an hour of diagnosing a repository defect that did
not exist. `just check` is the deterministic tier and `just gate` the complete bar;
neither has a raw substitute worth typing.
