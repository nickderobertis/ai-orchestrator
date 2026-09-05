# `tests/dag_ui`

The journeys over `just dag-ui` and `just telemetry-server`: the published read API and
the published browser bundle, served on one origin and read the way an operator reads
them — over HTTP, and through a real browser.

## Why this is a project rather than a directory

A real browser is the most expensive thing this suite launches, and `nx affected` can only
keep that cost off an unrelated edit where it is a separate project. `tests/ask_seam` and
`tests/plan_tooling` are projects for the same reason.

## Adding to `dagUiWorkspace`

Derive an addition from a read this suite actually performs, never from a sibling
project's list. **The read guard proves a key sufficient, never minimal**: a test reading
outside its key fails in `tests/conftest.py`, while a key wider than what the tests read
passes every deterministic check here and shows up only as unrelated edits paying for a
browser.

Its prose is excluded for that reason too: nothing here reads this file, so an
instruction-only edit must not start one.

## One target, not two

Nothing here reads this repository's prose, so a `reads_docs` test would be a read outside
this project's only key rather than a routing instruction.
