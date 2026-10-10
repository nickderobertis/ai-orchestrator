<!-- Static read (no tests run) of every module carrying SHARED_TOOLCHAIN_GROUP at base 3b6a1ef7, by a read-only research subagent of this dispatch; spot-checked against the profile in the report. -->

I found 46 modules in `SHARED_TOOLCHAIN_GROUP`: 9 write to the root toolchain from their own test bodies, 1 writes only through the `workspace_install` fixture, and 36 only read. Two things stand out. Only one of the five session-setup tests is caught by the rule that is meant to find writers. And `session-setup.sh` only runs `uv sync` on the root `.venv` when its version checks fail, so on a healthy checkout it doesn't rewrite `.venv/bin`.

All paths below are relative to `/home/nick.guest/.onevcs/workspaces/github.com-nickderobertis-ai-orchestrator-c2fddf4e28b4/pool/1/worktree/`. I didn't run pytest, so test counts are `def test_` counts. Counts marked ≈ include my estimate of the parametrised cases.

## How the group is applied, and the two rules

- **Group definition:** `tests/e2e/nx_workspace.py:63` sets `SHARED_TOOLCHAIN_GROUP = "shared-checkout-toolchain"`. `WORKSPACE_INSTALL_MARKS` (`:64-67`) is `usefixtures("workspace_install")` plus that group. `shares_workspace_install` (`:72-87`) applies both marks.
- **Ways modules join:** some spread `WORKSPACE_INSTALL_MARKS` into `pytestmark`, some use the decorator per test, and some put a plain `xdist_group(SHARED_TOOLCHAIN_GROUP)` in `pytestmark`. The plain form does **not** request the fixture.
- **Indirect use:** `tests/ask_seam/launch/test_launch_ask_seam_e2e.py:300` sets `LAUNCH_GROUP = SHARED_TOOLCHAIN_GROUP` and uses it at `:313`.
- **Not in the group:**
  - `tests/test_nx_cache_scope.py` only names the decorator as a string (`:544`).
  - `tests/e2e/test_shared_workspace_install_e2e.py` puts the group into a test file it generates in `tmp_path` (`:39`, `:66-67`). The module itself carries no group.
- **`_modules_naming_a_launch`** (`tests/test_nx_cache_scope.py:619-641`):
  - It parses each module and flags any `just("<recipe>")`, `_just(...)` or `["just","<recipe>",...]` call whose recipe reaches `onepipeline.sh start|adopt` (`:590-616`). Those recipes are derived from the justfile, through at most one script (`:566-587`).
  - It is only applied to `ASK_SEAM_ROOT = "tests/ask_seam"` (`tests/nx_inputs.py:325`; used at `test_nx_cache_scope.py:865`).
  - Every test in a flagged module must carry a group (`:877-884`). So readers outside `tests/ask_seam` joined voluntarily, and nothing enforces it.
- **`_reprovisioning_tests`** (`tests/test_nx_cache_scope.py:690-708`):
  - It scans every `tests/**/test_*_e2e.py` plus `tests/plan_tooling/test_plan_root_env.py`.
  - It flags a test function whose own source contains the text `REPO_ROOT / "scripts" / "session-setup.sh"` (`OWN_PROVISIONING`, `:540`).
  - **Gap:** only `test_session_setup_keeps_the_locked_plan_store_and_plan_root_in_force` matches, because its call is inline at `tests/session_setup/test_locked_plan_store_setup_e2e.py:249`. The other four session-setup tests call `Session.start` (`:386-387`), so the text isn't in their bodies. None of the nx.sh or bun writers below are detected either.
  - Writers and readers must resolve to one group name (`:886-916`).

## Writers

| module | tests in group | type | command that makes it a writer | notes |
|---|---|---|---|---|
| `tests/session_setup/test_locked_plan_store_setup_e2e.py` | 5 of 5 (each decorated: `:218,453,483,500,519`) | WRITER (root `.venv`, conditional) | Runs the root's `session-setup.sh` with `cwd=REPO_ROOT` at `:249` and `:387`. That script's `uv sync --project "$REPO_ROOT"` (`scripts/session-setup.sh:149`) runs **only if** the onejudge/oneharness/published-tool checks fail (`:139-143`). It always does `mkdir -p $REPO_ROOT/.plans/...` (`:341`). | **Test `:218` keeps the real HOME**, so `setup-llmlint.sh:84` always runs `uv tool install --upgrade llmlint-cli` into the host's uv tool dir, and `ensure_codex` relinks `~/.local/bin/codex` (`session-setup.sh:195`). Tests `:453-519` use a temp HOME; `ensure_just` installs rust-just there (`:208`). |
| `tests/e2e/test_workspace_contract_e2e.py` | 4 of 64 defs (`:1936,1974,2007,2038`) | WRITER (root `node_modules`), 3 of 4 tests | `_provisioning_copy` → `copy_checkout` (`:1845`) gives a copy whose `node_modules` symlinks to the root's. `./scripts/nx.sh` in that copy (`:1994`, `:2022`, `:2051`) calls `workspace-install.sh` (`nx.sh:21`), whose `bun install --frozen-lockfile` (`workspace-install.sh:114`) writes the root's `node_modules`. | The Python side goes to the copy's own or a temp venv: `UV_PROJECT_ENVIRONMENT` and `VIRTUAL_ENV` are stripped at `:1852-1862`. Test `:1938` only runs `python-install.sh` in the copy (`:1960`) → copy's `.venv`, so it touches nothing of the root. |
| `tests/e2e/test_nx_wrapper_version_e2e.py` | 1 of 1 (decorator `:17`) | WRITER (root `node_modules` + root `.venv`) | `./scripts/nx.sh --version` with `cwd=REPO_ROOT` (`:24-25`) and no `UV_NO_SYNC` → `workspace-install.sh:114` bun install at the root, then `python-install.sh:55` `uv sync --locked` against the root. | |
| `tests/test_project_boundaries.py` | 2 of 2 (`pytestmark` `:35`) | WRITER (root `node_modules` + root `.venv`) | `./scripts/nx.sh graph` with `cwd=REPO_ROOT` (`:124-125`), environment only adds `NX_DAEMON` → same two installs as above. | Called once per worker through the module-scoped `graph` fixture. |
| `tests/e2e/test_nx_cache_scope_e2e.py` | 27 of 27 (`:137`) | WRITER (root `node_modules`) | `copy_checkout` (`:298`, `:735`, `:790`), then `./scripts/nx.sh` in the copy (`:206`, `:233`, `:253`, `:718`, `:920`) → bun install through the symlink. | Root `.venv` is only read: `UV_NO_SYNC=1` and `UV_PROJECT_ENVIRONMENT=root/.venv` at `:214-215`, `:724-725`, `:926-927`. `resolved_project` (`:233-236`) and `graph` (`:253-255`) don't set them, so `python-install.sh` there syncs the **copy's own** `.venv`. |
| `tests/e2e/test_llmlint_cache_e2e.py` | 23 defs, ≈28 cases (`:77`) | WRITER (root `node_modules`) | `copy_checkout(root)` (`:204`), then `just lint-llm-diff` (`:95`) or `./scripts/nx.sh run workspace:lint-llm-diff` (`:595`) → `workspace-install.sh:114`. | `UV_NO_SYNC=1` and `UV_PROJECT_ENVIRONMENT=ROOT/.venv` (`:242-243`). The `.venv/bin` it builds and edits is the copy's (`:226-228`, `:639-641`). |
| `tests/e2e/test_llmlint_two_path_verdict_e2e.py` | 5 of 5 (`:92`; skipped if llmlint is absent) | WRITER (root `node_modules`) | Worktrees whose `node_modules` symlinks to the root's (`:139`, `:254`) run `just lint-llm-diff` (`:152`) → nx.sh → bun install. | `UV_NO_SYNC=1` and root `.venv` (`:279-280`). |
| `tests/e2e/test_budget_target_e2e.py` | 6 defs, ≈8 cases (`:53`) | WRITER (root `node_modules`) | A scratch tree with `package.json` and `bun.lock` (`:58-67`), `node_modules` symlinked to the root (`:210`), runs `./scripts/nx.sh` (`:160`, `:165`). | `UV_NO_SYNC=1` and root `.venv` (`:139-140`). |
| `tests/e2e/test_budget_telemetry_e2e.py` | 3 (`:45`) | WRITER (root `node_modules`) | Symlink at `:240`, then `./scripts/nx.sh` (`:179`) and `just check` (`:191`). | `UV_NO_SYNC=1` and root `.venv` (`:172-173`). |
| `tests/dag_ui/test_dag_ui_serving_e2e.py` | 19 of 19 (`:49`) | Test bodies only read; writes **only through the fixture** | `workspace_install` → root `workspace-install.sh` (`tests/conftest.py:400`). | `just dag-ui` (`:306`) → `telemetry-server.sh:139` `exec uv run onepipeline-api serve`, which only reads. |

## Readers

All 36 use a plain `pytestmark = pytest.mark.xdist_group(...)` covering the whole module, and none request `workspace_install`. None run session-setup, workspace-install, python-install, nx.sh, `uv sync` or bootstrap, and none write `REPO_ROOT/.venv` or `node_modules`. I checked this by grepping for those calls; every hit was a comment or an assertion on a message.

They reach the toolchain through `just` recipes, which use `uv run` (for example `onepipeline.sh:148`, `:291`; `register-repo` and `reclaim-branch` in the justfile at `:703` and `:888`), or by calling `REPO_ROOT/.venv/bin/*` directly. The ask_seam launch modules are the only readers the rule actually enforces.

| module | tests (≈ includes parametrised cases) |
|---|---|
| `tests/ask_seam/ask_manager/test_ask_manager_e2e.py` (`:187`) | 7 defs ≈17 (enforced: names `orchestrate`) |
| `tests/ask_seam/channel_reply/test_channel_reply_e2e.py` (`:154`) | 14 |
| `tests/ask_seam/follow_up_drafts_launch/test_follow_up_drafts_launch_e2e.py` (`:62`) | 5 ≈6 (enforced) |
| `tests/ask_seam/launch/test_launch_ask_seam_e2e.py` (`:312-313`) | 25 defs, 17 parametrise decorators, ≈50+ (enforced; its autouse module fixture writes the root `.env`, not the toolchain) |
| `tests/ask_seam/planner_fallback_ask/test_planner_fallback_ask_e2e.py` (`:141`) | 2 |
| `tests/ask_seam/settled_correlated_ruling/test_settled_correlated_ruling_e2e.py` (`:138`) | 1 |
| `tests/ask_seam/structural_reply/test_structural_reply_e2e.py` (`:40`) | 1 |
| `tests/e2e/test_adopted_engine_reads_an_adopted_run_e2e.py` (`:58`) | 3 |
| `tests/e2e/test_adopted_engine_settles_and_logs_e2e.py` (`:84`) | 2 |
| `tests/e2e/test_concurrency_interlock_e2e.py` (`:52`) | 3 |
| `tests/e2e/test_extends_chain_dispatch_e2e.py` (`:76`) | 5 |
| `tests/e2e/test_graph_overrides_dispatch_e2e.py` (`:57`) | 3 ≈6 |
| `tests/e2e/test_launch_walks_no_host_identity_e2e.py` (`:62`) | 1 |
| `tests/e2e/test_parked_run_is_driven_e2e.py` (`:52`) | 3 |
| `tests/e2e/test_publish_branch_e2e.py` (`:62`) | 22 ≈27 |
| `tests/e2e/test_reparent_waiting_human_adopted_e2e.py` (`:60`) | 1 |
| `tests/e2e/test_watch_selector_e2e.py` (`:105-108`) | 4 |
| `tests/e2e/unpublished_view/test_superseded_branches_e2e.py` (`:47`) | 3 |
| `tests/e2e/unpublished_view/test_unpublished_e2e.py` (`:65`) | 28 ≈34 |
| `tests/e2e/unpublished_view/test_unpublished_held_publication_e2e.py` (`:40`) | 1 |
| `tests/graceful_cancel/test_graceful_cancel_e2e.py` (`:44`) | 1 |
| `tests/merge_policy/test_merge_policy_vocabulary_e2e.py` (`:41`) | 2 ≈4 |
| `tests/plan_tooling/test_follow_ups_answer_comments_recipe_e2e.py` (`:78`) | 34 ≈45. Builds a mirror `.venv` with `lib` and `bin` symlinked into the root's (`:255-264`) but only writes a new file in the mirror's own `bin`; `UV_NO_SYNC=1` is set so nothing syncs through the links (`:570-572`, `:1193`). |
| `tests/plan_tooling/test_follow_ups_comment_answers_e2e.py` (`:85`) | 12 ≈21 |
| `tests/plan_tooling/test_follow_ups_recipe_e2e.py` (`:85`) | 58 ≈70 |
| `tests/run_end_hooks/test_configured_plan_stores_stay_untouched_e2e.py` (`:58`) | 3 |
| `tests/run_end_hooks/test_run_end_hooks_e2e.py` (`:55`) | 16 ≈31 |
| `tests/run_end_hooks/test_spike_branch_discard_e2e.py` (`:50`) | 7 |
| `tests/session_open_conflict/test_session_open_conflict_e2e.py` (`:52`) | 3 ≈4 (`repos-apply` → `uv run --project` at `apply-repo-registry.sh:142`) |
| `tests/unfinished/test_unfinished_e2e.py` (`:63`) | 34 ≈48 |
| `tests/unwatched/test_unwatched_and_stop_hook_e2e.py` (`:64`) | 20 ≈21 |
| `tests/unwatched/test_unwatched_launch_shapes_e2e.py` (`:62`) | 1 ≈3 |
| `tests/writeback_budget/test_adopted_engine_projects_incrementally_e2e.py` (`:78`) | 1 |
| `tests/writeback_budget/test_lineage_item_reuse_e2e.py` (`:113`) | 1 |
| `tests/writeback_budget/test_linked_plan_store_e2e.py` (`:60`) | 2 |
| `tests/writeback_budget/test_targeted_writeback_e2e.py` (`:113`) | 1 |

One caveat applies to all readers: `uv run` without `UV_NO_SYNC` does its own sync of the root project, and in these modules nothing sets `UV_NO_SYNC` around it. When the environment already matches the lockfile that is a no-op apart from taking the lock. If the checkout has drifted, a "reader" will rewrite the root `.venv` too.

## Does `workspace_install` rewrite an already-provisioned toolchain?

- **What it runs:** `tests/conftest.py:392-402` is `scope="session"`. It runs `scripts/workspace-install.sh` (`WORKSPACE_INSTALL`, `:121`) with no arguments. The script works out the root from its own location (`workspace-install.sh:30-31`).
- **How often:** session scope means once per pytest process, which is once per xdist worker, not once per run.
- **Every time:** nothing skips the install when the checkout is already in agreement with the lockfile. It always takes the tree lock (`:85`) and always runs `bun install --frozen-lockfile` (`:114`), so Bun decides.
- **Effect when nothing changed:** the comment at `:8-11` says Bun "installs only the difference" and exits quickly. But `:22-26` says a no-change run "still re-links every package carrying a `bin`". So it does rewrite the `node_modules/.bin` links even on an up-to-date tree, which is why concurrent runs failed with `EEXIST`.
- **Python:** it never runs `python-install.sh` or `uv sync`, so it doesn't touch `.venv`.

## How copies reach `node_modules` and `.venv`

- **`node_modules` is a symlink to the root's.**
  - `copy_working_tree` copies only `git ls-files --cached --others --exclude-standard` (`nx_workspace.py:101-111`), so ignored `.venv` and `node_modules` are not copied (`:7-8`).
  - `copy_checkout` then runs `(destination / "node_modules").symlink_to(NODE_MODULES)` (`:117`), where `NODE_MODULES = REPO_ROOT / "node_modules"` (`:26`). The budget and two-path modules make the same symlink by hand (cited in the table).
  - A copy's `nx.sh` runs its own `workspace-install.sh` (`nx.sh:21`). That script follows the symlink to find the tree root (`workspace-install.sh:65-69`), takes the **root's** lock (`:85`), and runs `bun install` from the copy (`:114`), which writes the root's `node_modules`.
  - A `--force` run would only delete the link, not the root tree (`:104-107`). No grouped test uses `--force` on a copy.
- **`.venv` is not symlinked.** A copy has none. `nx.sh:29` calls the copy's `python-install.sh`, which runs `uv sync --locked` in the copy (`:55`).
  - Whether that touches the root `.venv` depends only on the environment the test passes.
  - With `UV_NO_SYNC=1` the script exits immediately (`:21-23`). With `UV_PROJECT_ENVIRONMENT=REPO_ROOT/.venv` as well, `uv run` reads the root's `.venv` without writing it.
  - Without them (`test_nx_cache_scope_e2e.py:233`, `:253`; the workspace-contract copies), it builds the **copy's own** `.venv`. It would only hit the root if the outer environment already pointed `UV_PROJECT_ENVIRONMENT` there; no `project.json`, `nx.json` or justfile sets it.

## Totals

| | modules | `def test_` | with parametrised cases |
|---|---|---|---|
| All grouped | 46 | ≈425 | ≈550 |
| Writers, all | 10 | 95 | ≈102 |
| – from the test body | 9 | 76 (73 actually write; 3 are workspace-contract defs that don't) | ≈80 |
| – through the fixture only (`dag_ui`) | 1 | 19 | 19 |
| Readers | 36 | ≈330 | ≈450 |

Kind of write:
- **Root `.venv`:** `test_nx_wrapper_version_e2e.py`, `test_project_boundaries.py` (each run, through `python-install.sh`), and `session_setup` (only when its checks fail).
- **Root `node_modules`:** all nine body writers except `session_setup`, plus the fixture for the 51 grouped tests that request it (all `WORKSPACE_INSTALL_MARKS` and `shares_workspace_install` tests).
