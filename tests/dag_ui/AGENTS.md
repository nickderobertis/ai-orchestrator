# `tests/dag_ui`

The journeys over `just dag-ui` and `just telemetry-server`, driven over HTTP the way an
operator's browser reads them.

- **This repository builds neither the reader (`onepipeline-api`) nor the bundle
  (`onepipeline-ui`).** What this tier covers is this repository's composition of them —
  the two recipes, the proxy, the address file, the refusals, the pin reconciliations —
  and its own prose about what the reader answers.
- **Asserting that the bundle renders belongs to the repository that builds it.** A
  browser in a check tier is a browser on every publication's merge path, and nothing
  here provisions one; assert what the *reader* serves, which is the layer a rendered
  row can only ever be as true as. `test_no_browser_needed_e2e.py` enforces it.
  `scripts/dag-ui-screens.sh` is where a browser is still driven, deliberately outside
  every tier.
- **Two real servers per test is why this is a project rather than a directory**: that
  cost stays off an unrelated edit only as a separate Nx project.
- **Derive an addition to `dagUiWorkspace` from a read this suite performs, never from a
  sibling project's list.** The read guard in `tests/conftest.py` proves a key
  sufficient, never minimal: a key wider than the reads passes every check and shows up
  only as unrelated edits paying for two servers per test.
- **One target, not two.** Nothing here reads the repository's prose, so a `reads_docs`
  test would be a read outside this project's only key.
