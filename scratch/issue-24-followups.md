# Follow-ups from adopting onepipeline 0.7.5 / onevcs 0.6.1

## oneagentgraph stays at 0.2.18. The persona rewrite is blocked, and here is the proof.

**Decision: `config/oneagentgraph.version` was NOT moved to 0.3.0, and no file in
`personas/` was rewritten.** Doing both together would have broken the monitor and
the check-in pacemaker on every orchestrated run.

### What 0.3.0 changes

oneagentgraph #54 (`feat!: make a persona a onejudge config fragment`, released in
v0.3.0) replaces the persona schema. `docs/persona-format.md` at that tag is the
spec, and its own "The previous spelling is refused" section says the old shape
"no longer loads, anywhere — not through `oneagentgraph persona validate`, and not
when a member resolves a persona as a graph runs. There is no alias, no flag, no
environment variable, and no deprecation period."

| previous (what `personas/` is written in) | 0.3.0 |
| --- | --- |
| `agent.instructions` | `system_prompt` (top level) |
| `agent.name` | `name` (top level) |
| `user.persona` / `user.done_when` / `user.done_when_replaces_base` / `user.max_turns` | unchanged |
| `evals` | unchanged |

The same rule is stated for a **base config**, so `config/onejudge.base.yaml`'s
`agent:` block would have had to move in the same change.

### Which reader actually sees these files

- Most of `personas/` is a catalog and a validation target. A plan node's bare
  `persona: engineer` resolves to a role built into the crate, not to this
  directory (`personas/README.md`).
- **`graphs/dag-scope.yaml` names `../personas/orchestrator.yaml` and
  `../personas/check-in.yaml` by path, and a path-named persona *is* read** — on
  every orchestrated run, as the monitor and the heartbeat pacemaker.
- `just validate-personas` runs the oneagentgraph **CLI**, so `config/oneagentgraph.version`
  decides which shape validation certifies.
- What reads a persona **at dispatch** is the oneagentgraph `onepipeline` links,
  and that is **0.2.18 even at onepipeline v0.7.5** — confirmed from `Cargo.lock`
  at each of v0.7.2, v0.7.3, v0.7.4, and v0.7.5, all resolving `oneagentgraph
  0.2.18`.

So the hazard was a split: bump the CLI, rewrite the personas, and
`validate-personas` certifies a shape the linked 0.2.18 reader does not accept.

### The measurement

0.2.18's `Persona` (`src/persona.rs`) carries `#[serde(deny_unknown_fields)]` at
every level, so the failure mode is a hard refusal rather than silent degradation.
Confirmed three ways against the **installed** stack (`oneagentgraph 0.2.18`,
`onepipeline 0.7.5`), with a 0.2.18-shaped control run beside each:

1. `oneagentgraph persona validate <0.3.0-shaped file>` →
   exit 2, ``invalid config: unknown field `name`, expected one of `agent`, `user`, `evals` ``.
   The 0.2.18-shaped control → `OK`, exit 0.
2. `oneagentgraph validate <one-member graph naming that persona by path>` →
   exit 2, ``member "worker": invalid config: … unknown field `name` ``.
   The control graph → `1 member(s) OK`, exit 0.
3. **A real launch.** `oneagentgraph run <that graph> --task probe`, with
   `ONEAGENTGRAPH_ONEHARNESS_BIN` pointed at `tests/e2e/fake_backend.py`, exits 2
   on the same refusal **before any `graph-started` event**. The identical run
   against the 0.2.18-shaped control reaches `graph-settled` with
   `{"exit_code":0,"members":{"worker":"settled"}}` and labels the member
   `persona: probe`.

The refusal is loud, which is the good case — but it happens at config validation,
so under a split pin the monitor and the pacemaker would produce no member at all
on every run.

### The exact condition that unblocks this

**An `onepipeline` release whose `Cargo.lock` resolves `oneagentgraph 0.3.0`.**
Check it, do not infer it — `Cargo.toml`'s requirement is not the constraint, the
lock resolution is (`AGENTS.md` carries the same lesson about `onevcs = "0.4.1"`
permitting 0.4.2 through v0.7.5):

```sh
git -C ~/.ai-orchestrator/repos/nickderobertis__onepipeline fetch --tags
git -C ~/.ai-orchestrator/repos/nickderobertis__onepipeline show <tag>:Cargo.lock \
  | grep -A2 '^name = "oneagentgraph"'
```

When that reads `0.3.0`, the three moves land in **one** change: bump
`config/oneagentgraph.version` (and `pyproject.toml`) with the onepipeline pin,
rewrite every file in `personas/` and `config/onejudge.base.yaml` per the table
above, and re-run the three probes to confirm the new shape is what the linked
reader accepts. Not before, and never one without the others.

Measured 2026-08-18 against onepipeline 0.7.5, onevcs 0.6.1, oneagentgraph 0.2.18.

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
