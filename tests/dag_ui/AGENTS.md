<!-- llmlint: ignore-file[instruction_layer_localized] `.github/CODEOWNERS` is `* @nickderobertis`, which routes every path here as it routes every sibling tier project under `tests/`, none of which carries an entry of its own; a per-directory line would restate that match rather than route anything. Lift this if ownership here ever stops being routed. -->
# `tests/dag_ui`

The journeys over `just dag-ui` and `just telemetry-server`, driven over HTTP the way an
operator's browser reads them.

- **This repository builds neither the reader nor the bundle**, and since
  `onepipeline-api serve --ui` it does not compose them either: one published binary
  answers the view and its data on one origin. What this tier covers is what is left of
  this repository's side — the two recipes, the address file, the acting session, the
  pin reconciliations — and its own prose about what the reader answers.
- **Asserting that the bundle renders belongs to the repository that builds it.** A
  browser in a check tier is a browser on every publication's merge path, and nothing
  here provisions one; assert what the *reader* serves, which is the layer a rendered
  row can only ever be as true as. The view is photographed by the `dag-ui-screens`
  recipe of `onepipeline-ui`, in the repository that builds and iterates on it; no
  recipe here does.
- **One real server per test is why this is a project rather than a directory**: that
  cost stays off an unrelated edit only as a separate Nx project.
- **Derive an addition to `dagUiWorkspace` from a read this suite performs, never from a
  sibling project's list.** The read guard in `tests/conftest.py` proves a key
  sufficient, never minimal: a key wider than the reads passes every check and shows up
  only as unrelated edits paying for a server per test.
- **One target, not two.** Nothing here reads the repository's prose, so a `reads_docs`
  test would be a read outside this project's only key.
