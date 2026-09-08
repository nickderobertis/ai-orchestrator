# `tests/dag_ui`

The journeys over `just dag-ui` and `just telemetry-server`: the published read API and
the published browser bundle, served on one origin and read the way an operator's
browser reads them — over HTTP.

## What this tier covers, and what it does not

This repository builds neither half of the view. `onepipeline-api` is the reader and
`onepipeline-ui` is the bundle; both are pinned here and installed here, and both are
proven in the repositories that build them. What this tier covers is **this
repository's own composition of them**: the two recipes, the proxy that puts them
behind one origin, the address file they find each other through, the recipe's
refusals, the client-route fallback and its climb-out, the event stream held open
through the proxy, the two pin reconciliations, and this repository's own prose about
what the reader answers.

**Asserting that the bundle renders belongs to the repository that builds it.** A
journey here that drives a real browser makes a browser a prerequisite of every
publication from this repository: every target in `just check` is inside `just gate`,
which is inside the `pre-push` hook, and nothing here provisions one — no script, no
recipe, no target. What it buys is an assertion `onepipeline-ui` already owes. So
assert what the *reader* serves instead, which is the layer a rendered row can only
ever be as true as. `test_no_browser_needed_e2e.py` enforces it.

`scripts/dag-ui-screens.sh` is where this repository does still drive a browser, and it
is deliberately outside every check tier — an operator runs it to photograph the view.
Its driver is the workspace's own `playwright` devDependency, pinned in `package.json`
and resolved out of `node_modules` by `bunx`.

## Why this is a project rather than a directory

These journeys start two real servers per test and drive them over HTTP, which is a
cost `nx affected` can only keep off an unrelated edit where it is a separate project.
`tests/ask_seam` and `tests/plan_tooling` are projects for the same reason.

## Adding to `dagUiWorkspace`

Derive an addition from a read this suite actually performs, never from a sibling
project's list. **The read guard proves a key sufficient, never minimal**: a test reading
outside its key fails in `tests/conftest.py`, while a key wider than what the tests read
passes every deterministic check here and shows up only as unrelated edits paying for
two servers per test.

Its prose is excluded for that reason too: nothing here reads this file, so an
instruction-only edit must not start them.

## One target, not two

Nothing here reads this repository's prose, so a `reads_docs` test would be a read outside
this project's only key rather than a routing instruction.
