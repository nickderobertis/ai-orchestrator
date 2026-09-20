# Host setup

The plan-store SDK and its matching `onetaskgraph` CLI are installed from `uv.lock`;
`config/onetaskgraph.version` is reconciled with those locked distributions.

The one-time, **manual** steps that bring a new machine to a working state — and,
just as importantly, what is **already automated**, so nothing here is redone by
hand. Everything below was reconstructed by reading the scripts on a host that had
just been brought up; this document exists so the next host is something to follow
rather than an investigation to repeat.

Add a subscription to an existing host? Only [The six harness
logins](#4-the-six-harness-logins) and [The trust-marking
pass](#5-the-trust-marking-pass-order-matters) apply.

## What is already automated

**Do not install these by hand.** `just bootstrap` runs, in order:

1. `scripts/session-setup.sh` (below),
2. `scripts/workspace-install.sh --force` — the locked Bun install, applied to a
   discarded tree rather than reconciled with the one that is there (every
   `scripts/nx.sh` already reconciles),
3. `scripts/nx.sh run-many -t bootstrap` — each project's own setup,
4. `git config core.hooksPath .githooks` — activates the pre-push `just gate`, the
   `commit-msg` subject policy, and the `post-checkout` mark that makes a freshly
   created worktree trusted, since the setting names the whole directory,
5. `git config receive.denyCurrentBranch updateInstead` — lets a local-mode
   lifecycle push land in this non-bare checkout.

`scripts/session-setup.sh` also runs on every Claude Code SessionStart hook, and is
idempotent. It:

- `uv sync`s the exact `onejudge` (`config/onejudge.version`) and `oneharness`
  (`config/oneharness.version`) versions into the worktree's `.venv` and verifies
  each as both a distribution and a CLI;
- brings the four published tools this repository configures — `oneagentgraph`,
  `onevcs`, `onepipeline`, and `onepipeline-ui` (PyPI's `onepipeline-api-cli`) —
  from PyPI through that same `uv sync`, at the releases adopted in
  `config/<tool>.version`, and verifies each one the same two ways;
- sweeps the reclaimable scratch and publication workspaces (`just sweep`);
- installs `bun` via npm, and `codex` via npm when it is absent, exposing a stable
  `~/.local/bin/codex`;
- installs `cargo-sweep` through `cargo install` when it is absent or at another
  release — pinned in one place in the script, `CARGO_SWEEP_VERSION` — because it is
  the `maintain` command `config/onevcs.workspaces.yml` names for every Rust identity's
  warm worktree slots, and a command the host lacks would fail on every idle tick the
  engine sweeps the pool on. Optional: a host without `cargo` is told and never failed,
  since it has no Rust slot to maintain;
- wires allowlister's codex `repo-write` PreToolUse hook — **only if allowlister is
  already installed** (see [step 6](#6-install-allowlister-nothing-automates-this));
- marks this checkout trusted in every Claude config directory a dispatch can run
  under — both alternates and the primary — **only if that config already exists**
  (see [step 5](#5-the-trust-marking-pass-order-matters));
- persists the session's `PATH` (and `LLMLINT_ONEHARNESS_BIN`) into Claude Code's
  session env file, creating its parent directory when the config directory is new;
- hands off to `scripts/setup-llmlint.sh`, which installs `llmlint-cli` with
  `uv tool` — that one install brings the llmlint judge tier and its bundled
  oneharness;
- last, runs `just repos-bootstrap`, which provisions the **registered sibling
  checkouts' gates** and relays its report without letting a sibling's failure
  change this session's exit status.

What automation cannot do is authenticate an account, accept a trust dialog,
install allowlister, or rebuild the per-machine repository registry. That is
exactly what the rest of this document is.

### The sibling gates: `just repos-bootstrap`

A dispatch publishes through its target repository's own merge path, and for a local
identity that is the target's `pre-push` gate, run on this host with whatever tools this
host has. Provisioning this checkout says nothing about those, so a correct, committed
change once spent three publication attempts discovering, tool by tool, what an
onetaskgraph gate needed — `cargo-deny`, `cargo-machete`, then the pinned `release-plz`.
A Codex dispatch never runs the target's Claude session-start hook, and the engine's
dispatch-env hook runs before a session opens, so the one place that reaches every
dispatch is this repository's own session setup, and its last step is:

```sh
just repos-bootstrap
```

It runs each registered checkout's **own** `just bootstrap` — every target repository
defines one, and each is idempotent by that repository's design — reading the checkouts
from the same tracked `config/onevcs.checkouts` that `just repos-apply` registers,
through the same reader, and skipping a path this host does not have the same way. Two
kinds of checkout are skipped on purpose: a checkout of this repository, because the
session setup calling it is one of this repository's own and running
`workspace-install.sh --force` in a checkout another manager is working in would
discard that manager's `node_modules` under a live gate; and a call nested inside
another `repos-bootstrap` — a sibling's bootstrap reaching this recipe through a session
setup of its own — which provisions nothing, because the caller above it already is.

What it costs is bounded two ways. It is **memoized per checkout** on that checkout's
`HEAD` plus the bootstrap inputs it can name without executing anything — the justfile
as `just --dump` parses it and every file the `bootstrap` recipe body names — under a
memo root beside the Nx cache and outside every worktree, which the recipe's first line
of output names on every run, so a session start where nothing moved is one read per
checkout; to force one checkout's bootstrap, delete its memo directory under that root
(each records the path of the checkout it is for) and run the recipe again. And it holds a
**per-checkout lock**, so two session starts arriving together never run one checkout's
bootstrap at once — the second waits, then reads the memo the first recorded.

It prints one line per listed checkout, opening with what happened to it, and a summary
counting each outcome; `tests/test_repos_bootstrap_docs.py` holds this list to exactly
the outcomes the script reports:

- `ran` — that sibling's own bootstrap ran and succeeded; the line names the log its
  output was kept in.
- `unchanged` — its memo matches, so nothing ran.
- `skip` — nothing to run, with the reason: not on this host, a checkout of this
  repository, no justfile, or no `bootstrap` recipe.
- `refused` — it could not get as far as running: a justfile `just` could not read a
  `bootstrap` recipe out of, a directory it could not enter, a memo it could not write,
  or a lock another caller held too long.
- `failed` — the bootstrap ran and exited non-zero; the line names the exit status and
  the log.

A `failed` or `refused` line makes the recipe exit non-zero when run by hand; session
setup logs it and continues, since a sibling's gate missing a tool is a report for
whoever runs that sibling's bootstrap by hand, never a reason for this session to be
without a toolchain. It runs each sibling's bootstrap **exactly as that repository
defines it**: what a bootstrap does to the host is that repository's decision, and a
defect there is that repository's to fix.

Two bounds to know. The Claude Code SessionStart hook that runs session setup carries
the timeout `.claude/settings.json` names, and a sibling whose first bootstrap takes
longer than what is left of it is cut off there and left without a memo, so the next
session start runs it again; `just repos-bootstrap` by hand, or `just bootstrap`, has no
such bound. And a journey of session setup names its own checkout list in
`ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS`, so the real script can be driven over
stand-ins the journey registers rather than over this host's siblings.

## 1. Prerequisites nothing here installs

| Tool | Why this repository needs it |
| --- | --- |
| `git` | every checkout, worktree, clone, and merge in the lifecycle |
| `just` | the whole command surface; session setup itself shells to `just sweep` |
| `uv` | installs the pinned onejudge/oneharness into `.venv` and llmlint via `uv tool` |
| `jq` | the only tool that can mark the Claude workspaces trusted |
| `gh` | GitHub publication (PRs, checks) and repository-type inference (`gh api user --jq .login`) |
| `node` / `npm` | how the codex CLI and bun are installed |

Two of those fail quietly enough to call out:

- **`jq`.** Without it the trust marking is a no-op with only a log line, and
  session setup's exit status is unchanged — so a host missing `jq` looks healthy
  until a dispatch stalls on a trust dialog:

  ```
  claude-workspace-trust: jq is unavailable; install jq or add it to PATH, then retry
  session-setup: Claude workspace trust setup failed for ~/.claude-alt/.claude.json; continuing
  ```

- **`npm`.** `bun` is a required dependency, so its absence does fail session setup
  (non-zero exit), while the codex install merely degrades:

  ```
  session-setup: cannot install required bun: npm is not installed
  session-setup: npm not found; cannot install codex (live path can still use claude-code)
  ```

## 2. Accounts and git identity

```sh
gh auth login
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

The identity is not cosmetic: dispatched lifecycle agents commit on this host, and
git refuses to commit without one.

## 3. Clone both checkouts, then bootstrap

The self-dispatch rule in [`AGENTS.md`](../AGENTS.md) requires **two** checkouts of
this repository: the canonical one, which is only ever fast-forwarded after a merge
lands and is never worked in, and an isolated safety clone that dispatched work is
actually cut from.

```sh
mkdir -p ~/projects
git clone https://github.com/nickderobertis/ai-orchestrator.git ~/projects/ai-orchestrator
git clone https://github.com/nickderobertis/ai-orchestrator.git ~/projects/ai-orchestrator-isolated
cd ~/projects/ai-orchestrator
just bootstrap
```

Bootstrap only the canonical checkout. The safety clone needs to exist and stay
clean: every run clones it again, and the lifecycle points that per-run clone at
`.githooks` itself before adding a worktree.

## 4. The six harness logins

Every role's chain — worker, judge, orchestrator, llmlint — names the same six
identities and differs only in their order, so an identity nobody logged into is
quota that role loses once everything ahead of it is exhausted. Log in to all six:

| Identity | Command |
| --- | --- |
| `claude-code:primary` | once, in the canonical checkout: `CLAUDE_CONFIG_DIR="$HOME/.claude" claude`, then `/login` if it is not logged in — it shares the default `claude` login's credentials ([why once](#how-the-primary-identity-authenticates)) |
| `claude-code:alternate` | `CLAUDE_CONFIG_DIR="$HOME/.claude-alt" claude`, then `/login` |
| `claude-code:alternate2` | `CLAUDE_CONFIG_DIR="$HOME/.claude-alt2" claude`, then `/login` |
| `claude-code:primary-backup` | `CLAUDE_CONFIG_DIR="$HOME/.claude-primary-backup" claude`, then `/login` |
| `codex` | `codex login` |
| `codex:alternate` | `CODEX_HOME="$HOME/.codex-alt" codex login` |

Session setup marks trust for all four Claude identities with `jq`, the primary
included: it is last on every chain, so a checkout it does not trust blocks the candidate
that runs once every other subscription is spent. The primary's configuration is
`$ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR/.claude.json` (by default
`$HOME/.claude/.claude.json`), marked exactly like the directories the other three
identities name.

Leaving one unauthenticated is safe but not free of consequence — it reports
`auth` and falls through to the next candidate, costing that role the quota rather
than breaking it. Check a Codex login with
`CODEX_HOME="$HOME/.codex-alt" codex login status`; check every identity with the
probe in [step 8](#8-verify-the-host).

### Where each Claude identity's directory lives

Each Claude identity reads its config directory from one variable, which
`scripts/claude-alt-config-dir.sh` derives for every wrapper and hook:

| Identity | Variable | Default |
| --- | --- | --- |
| `claude-code:alternate` | `ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR` | `$HOME/.claude-alt` |
| `claude-code:alternate2` | `ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR` | `$HOME/.claude-alt2` |
| `claude-code:primary-backup` | `ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR` | `$HOME/.claude-primary-backup` |
| `claude-code:primary` | `ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR` | `$HOME/.claude` |

A host whose directories are not the defaults says so in its **identities file**,
`$XDG_CONFIG_HOME/ai-orchestrator/claude-identities.env`, or
`$HOME/.config/ai-orchestrator/claude-identities.env` when `XDG_CONFIG_HOME` is unset,
empty or not an absolute path. It sits outside every checkout, and every entry point
reads it — the wrappers, the launch verbs, session setup and the `post-checkout` hook —
so it reaches the hooks that source no shell profile.

- It admits those four names and nothing else, one `NAME=value` per line, in the same
  dialect as the credentials `.env`.
- For each name, a non-empty value in the environment wins over the file, and the file
  wins over the default.
- Values are literal: nothing is expanded, so write absolute paths rather than `$HOME/…`
  or `~`.
- Any other name, a malformed line, a relative value, or a path that is not a readable
  file stops every wrapper before oneharness starts, naming the line or the variable and
  echoing no value.

It is **not** the credentials `.env` at the checkout root: that file belongs to one
checkout and carries secrets, while this one belongs to the host and carries none.

On the WSL box whose `~/.claude` is logged in as the primary-backup account, because
that is its remote-control identity, the primary account lives in `~/.claude-primary`,
and the file reads:

```sh
ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR=/home/<user>/.claude
ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=/home/<user>/.claude-primary
```

### How the primary identity authenticates

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] claude-code's on-disk layout has no source to derive from; step 8's probe, named below, is its check. -->
With nothing exported and no identities file, `claude-code:primary` runs with
`CLAUDE_CONFIG_DIR=$HOME/.claude`, which is where the default `claude` login keeps its
credentials. So the primary identity **authenticates on the default login's
`$HOME/.claude/.credentials.json`**: it is the same account, and a token refresh by
either one is read by the other. Its **trust and onboarding state is its own**.
claude-code with `CLAUDE_CONFIG_DIR` set keeps that state in `$HOME/.claude/.claude.json`,
separate from the default login's `$HOME/.claude.json`. A directory an interactive
`claude` trusts is therefore not trusted for a dispatch, and the reverse.

Confirm it on a host with step 8's per-identity probe,
`scripts/oneharness-agent.sh run --harness claude-code:primary`. It names one candidate,
so a fall-through cannot answer for another account. An operator runs it: the pre-push
hook's `just smoke`, spent when a push touches the harness routing files, runs one turn
on the chain's first candidate that runs and never pins `claude-code:primary`, so it
does not confirm this. A turn that ran authenticated
reports `"ran": "claude-code:primary"` with an empty `fell_through`. That probe is also
what shows either path above has moved: the turn then stops running authenticated as
the primary.

That statement rests on one such turn, traced with `strace -f` on claude-code 2.1.272.
It ran on `claude-code:primary` with no fall-through and billed tokens. It opened
`$HOME/.claude/.credentials.json` only for reading. claude-code wrote its state through
`$HOME/.claude/.claude.json`, and no process of the turn opened `$HOME/.claude.json`.
<!-- llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate] -->

#### Its `.claude.json` has to exist before the first dispatch

The trust pass marks a `.claude.json` that exists, and returns without writing when the
file is absent (`mark_claude_config_trust` in `scripts/claude-workspace-trust.sh`). That
covers session setup, the `post-checkout` hook and every dispatch's own mark.

<!-- llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Observed claude-code start-up behaviour with no source or login-free gate; the commands below hold whether or not it changes. -->
claude-code does create that file itself on a non-interactive run, but it creates the
file in the same start in which it decides trust. A probe with an empty config
directory showed this. claude-code created `.claude.json`, reported the checkout
untrusted, named that new file, and ignored the checkout's `permissions.allow`. The
session's own SessionStart pass marked the file only afterwards, too late for the session
it ran in.

That probe held no credentials, so it shows only what happens before authentication. No
run with credentials and no `.claude.json` was made, because staging one would mean
copying a live login's credentials. Nothing, then, shows that a first dispatch under
such a directory is pretrusted. **Pretrusting it requires the file to exist first.** One
interactive `claude` in that directory creates the file before the trust pass reads it,
and that is right whichever way an authenticated first run behaves.
<!-- llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate] -->

#### The commands

**An existing host that exports nothing.** Log in the primary-backup, create the
primary's `.claude.json` with one interactive run, then run [step 5](#5-the-trust-marking-pass-order-matters)'s pass:

```sh
cd ~/projects/ai-orchestrator
CLAUDE_CONFIG_DIR="$HOME/.claude-primary-backup" claude   # /login, then exit
CLAUDE_CONFIG_DIR="$HOME/.claude" claude                  # accept the trust dialog, then exit
just session-setup
```

**The WSL host whose `~/.claude` is the primary-backup login.** A `~/.claude` logged in
by a plain `claude` holds credentials, but its state lives in `~/.claude.json`. That is
the case above, so the same one-time run applies to it. In order:

1. Write the identities file. The unquoted heredoc writes the two lines above with
   `$HOME` expanded, and the helper names the file's location:

   ```sh
   cd ~/projects/ai-orchestrator
   file=$(bash -c 'source scripts/claude-alt-config-dir.sh; claude_identities_file_path')
   mkdir -p "$(dirname "$file")"
   cat > "$file" <<EOF
   ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR=$HOME/.claude
   ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR=$HOME/.claude-primary
   EOF
   ```

2. Log in the Primary account in its own directory:

   ```sh
   CLAUDE_CONFIG_DIR="$HOME/.claude-primary" claude   # /login, then exit
   ```

3. Create `~/.claude`'s `.claude.json` with one interactive run in the canonical checkout:

   ```sh
   CLAUDE_CONFIG_DIR="$HOME/.claude" claude           # accept the trust dialog, then exit
   ```

4. Run step 5's pass: `just session-setup`.
5. Run [step 5's verify loop](#5-the-trust-marking-pass-order-matters). All four lines
   print `true`, and the files it reads are `$HOME/.claude-primary/.claude.json` for the
   primary and `$HOME/.claude/.claude.json` for the primary-backup.

## 5. The trust-marking pass (order matters)

`scripts/session-setup.sh` marks every dispatch identity's `.claude.json` — but only a
config file **that already exists**, and it is step 4 that creates it: each login, and
for the primary its one-time interactive run.
So the session doing the authenticating is always too early, and one explicit pass
afterwards is required:

```sh
cd ~/projects/ai-orchestrator
just session-setup
```

Verify all four identities, keyed on the checkout root. The files are the ones the
resolver names, so the loop honours the identities file:

```sh
cd ~/projects/ai-orchestrator
bash -c 'source scripts/claude-alt-config-dir.sh; source scripts/claude-workspace-trust.sh
  claude_trust_config_paths verify' |
  while read -r config; do
    jq -r --arg root "$HOME/projects/ai-orchestrator" \
      '.projects[$root].hasTrustDialogAccepted' "$config"
  done
```

With nothing exported and no identities file, those four are
`$HOME/.claude-alt/.claude.json`, `$HOME/.claude-alt2/.claude.json`,
`$HOME/.claude-primary-backup/.claude.json` and `$HOME/.claude/.claude.json`.

All four lines must print `true`; jq reports a missing file for an identity nobody has
logged into. `$HOME/.claude.json` is deliberately not marked: it belongs to no chain
identity, and an interactive default-login `claude` shows claude-code's ordinary trust
dialog once per new directory. `null` means the pass has not run since that
configuration was created — rerun the command above. (Session setup marks the git
common directory's parent and the repository root, which are the same path in the
canonical checkout.)

A dispatch's own worktree is marked earlier than any of this, and it has to be.
claude-code reads the trust decision as it starts, so nothing running from inside
that session — session setup included — can mark the directory in time for the
session it is running in: claude-code discards `permissions.allow` and the turn
blocks on an approval that cannot arrive non-interactively. What that looks like in
the history is an identity with quota left doing nothing, and it was measured here as
`claude-code:alternate2` spending 206,547 ms and 0 tokens against a five-hour window
at 45%. `.githooks/post-checkout` closes it by marking the directory as git creates
it, which for a dispatch is the `git worktree add` `onevcs` runs before the dispatch
exists — through the same implementation as everything else in this section, and
without a registration for the parent, which claude-code was measured not to honour
for its children. It never speaks and never fails: git fails the command that ran a
non-zero `post-checkout` hook, so an absent, unreadable, or already-correct
configuration is a silent success there rather than a broken branch switch.

Every dispatch marks its own clone, worktree, and result the same way, in each of
those files, so they are on the hot path of the whole harness and nothing else prunes
them. Two properties
keep it from becoming one: marking paths that are already trusted decides from a
parse and never rewrites the file, and a mark that does write first drops every
entry whose workspace is gone. Only an entry that records **nothing but** its trust
decision is dropped that way — one carrying session state survives its path, since
losing a trust decision costs one dialog and losing a recorded session costs the
session. Left unpruned this reached 34 MB and 167,958 entries here in three days,
165,858 of them dead throwaway test paths, and every mark paid that size behind one
host-wide lock.

## 6. Install allowlister (nothing automates this)

Session setup wires the hook but never installs the tool; a host without it gets
only a log line:

```
session-setup: allowlister not found — codex bypass mode would be ungated (install: https://github.com/nickderobertis/allowlister)
```

That matters here more than the wording suggests. Everything dispatches in
`--oneharness-mode bypass` — no approvals and no inner sandbox — because codex's own
`workspace-write` sandbox needs unprivileged user namespaces this host disallows,
and the container is the boundary instead. allowlister's `repo-write` PreToolUse
hook is the remaining **per-tool-call** guardrail on codex.

Install it from <https://github.com/nickderobertis/allowlister> per its README, then
confirm it resolves and rerun session setup, which wires the hook for you:

```sh
command -v allowlister
just session-setup
```

The wiring itself is not a step you perform: session setup runs
`allowlister init --harness codex --profile repo-write -y --no-history` on every
session once the binary is on `PATH`, and the log line above stops appearing.

## 7. Rebuild the repository registry

The registry lives under `~/.onevcs` (override the whole state root with
`ONEVCS_HOME`) and is **per-machine and untracked**, so a new host starts with none
of it and no lifecycle dispatch can resolve a repository. What it should hold *is*
tracked, in four files, and one recipe applies them:

```sh
just repos-apply
```

- **`config/onevcs.checkouts`** — every checkout to register, one path per line.
  `onevcs register` resolves each path's own `origin` to a repository identity, so
  several checkouts of one origin (this repository's publication checkout and its
  two safety clones) share one identity and therefore one policy.
- **`config/onevcs.rules.yml`** — the rules file, installed to
  `$ONEVCS_HOME/rules.yml`. First match wins; each rule names a repository and sets
  how it publishes (`publication`), whether somebody else has to approve
  (`approvals`), and what verifies it (`gate`). Its `trailer_prefix` is separate
  from the rules and applies to all of them: `Orchestrator-` is the prefix every
  provenance trailer on this host is spelled under, and a preserved branch whose
  marker uses a prefix the rules file does not name is reported unrecognized and
  refused publication — so dropping that key strands every branch preserved before
  the adoption. `just recoverable` is where you would see it.
- **`config/onevcs.releases.yml`** — the release override, installed to
  `$ONEVCS_HOME/releases.yml`. It decides how a node waits on another repository's
  release: this repository's nodes resolve the `published` rung, so one that depends
  on a producer's node waits for the release carrying that work rather than adopting
  its branch, and each producer this host installs — the seven `pyproject.toml` and
  `config/*.version` pin — names the wheel this host installs as its `default_target`,
  which is what a consumer naming no `consumes` waits for. Every rule merges the
  producer's own `release-targets.toml` and restates no target of its own, and the
  file's header says why this repository's rung is `published`. The candidate is
  validated through `onevcs release targets` in the same scratch home
  the rules are, so a document `onevcs` cannot load — a rung it does not know, a
  `default_target` naming no target — is refused by name and replaces nothing.
- **`config/onevcs.workspaces.yml`** — the workspaces file, installed to
  `$ONEVCS_HOME/workspaces.yml`. It sizes each identity's pool of warm worktree slots
  and names what maintains them: the shared default pools every identity at one slot
  with unbounded overflow, deletes this repository's `.logs/` on every return, and
  names `cargo sweep --time 7` as the maintenance of every registered identity whose
  checkout carries a root `Cargo.toml` — the file's header lists them, and
  `tests/test_workspaces_file.py` reconciles the list against this host's checkouts
  and holds each command's first word to `PATH`. **A host sets its own pool size in
  `${XDG_CONFIG_HOME:-$HOME/.config}/ai-orchestrator/workspaces.yml`, never by
  editing the tracked file**: that file — beside `claude-identities.env`, outside every
  checkout — has the same schema and version and is partial, its `default` keys
  replacing the tracked `default`'s key by key and each of its `rules` replacing the
  tracked rule with an identical `match` or being appended; the recipe composes the
  two (`orchestrator/workspaces_overlay.py` states the rule) and installs the tracked
  file alone when the host file is absent. An identity with several execution
  checkouts spends its pool one slot per lender, so size `pool` per lender you
  alternate — this repository alternates two, so a host that wants both warm gives
  it `pool: 2` there. The composed document is what is validated through `onevcs pool
  status` in the same scratch home, so a malformed value, a `delete` climbing out of
  the worktree, or a default admitting no session is refused by name and replaces
  nothing, and a host file off the schema is refused by the composer before that.
  Absent, an `onevcs` before the pool reads a byte-identical registry, so it is safe
  beside live runs of an older engine.

The recipe is re-runnable and idempotent — registration is keyed by alias and the
rules file, the override and the workspaces file are each replaced whole, so a second
run leaves the registry a first one did. Run it again after editing any of the four
files or the host's workspaces overlay, and after cloning a repository onto the host. A path this host does not have is
reported as skipped rather than failing, so a machine holding a subset of these
checkouts still registers what it has. `--dry-run` reports what would change and
changes nothing; `--rules FILE`, `--releases FILE` and `--workspaces FILE` install a
different candidate in place of the tracked one, which is how a journey seeds a
scratch registry — a journey about run roots installs one pooling nothing.

It finishes by resolving every checkout it registered and printing the policy each
one landed on, and it **fails** if any of them matched no rule. That is not
pedantry: an unmatched repository falls through to the file's `default`, which is
the reviewed path — safe, but nobody's configured policy, and a silent pass there
is how a mistyped `owner` goes unnoticed. Below that table it prints one line per
producer this host installs — `orchestrator/host_installs.py` is the list — naming
the `default target` and `adoption` that producer resolves, read back through
`onevcs release targets` rather than composed from the file, so what an operator
reads is what a consumer will get. A producer this registry does not hold reads
`not registered here`; one whose checkout `onevcs` cannot read a declaration out of
right now — on another manager's branch, or with no fetched base — reads
`unresolved:` with `onevcs`'s own reason, because that is the checkout's momentary
state rather than a fault in the file: `onevcs sync` puts it back.

### `just repos` lists; `just repo-policy` reports the routing

`just repos` prints each identity with its stored `gate` — `onevcs register`'s guess
from the build file it finds in the checkout, which nothing on the publication path
reads — and, under `--audit-gate-coverage`, each checkout's resolved `publication:`
and `approvals:` beside the verifier on its merge path. It used to print two more
stored fields, `workflow` and `repo_type`, derived from whether the origin had a host
and unsettable by any surface; onevcs 0.21.0 removed them, because two verbs *did*
read them — `integrate` and `recover` refused every hosted identity on their strength,
this repository's own `local-direct` one included — and what a change actually does
has to come from the rules file alone. So `just repos` answers *which repositories and
checkouts exist*, and this answers what one of them will do:

```sh
just repo-policy ai-orchestrator     # an alias, identity, origin, or path
```

```
repo: ai-orchestrator
identity: github.com/nickderobertis/ai-orchestrator
matched: rule 1 {host: github.com, owner: nickderobertis, name: ai-orchestrator}
publication: local-direct (from rule 1)
approvals: none (from rule 1)
gate: command: just gate (from rule 1)
```

Change the routing by editing the rule and re-running `just repos-apply`. Nothing
here runs a gate before the merge path does, so every check a repository requires is
one only its merge path runs and every one of them can refuse a merge: `just repos
--audit-gate-coverage` names them per identity, read off that repository's own branch
protection and rulesets by `onevcs` at the moment you ask. There is no tracked copy of
that list to keep in step — there was one, and every branch here failed a whole gate
whenever a sibling renamed a check.

### Register with the checkout path, never the alias

An alias is what a path registration **produces** — `onevcs` derives it from the
checkout's directory name — and it is not an input you can feed back in to
`register`. Passing an `owner/name` spelling is parsed as exactly that:

| Spec you pass | Identity it resolves to |
| --- | --- |
| `~/ai-orchestrator` | a checkout at that path, alias `ai-orchestrator` |
| `local/ai-orchestrator` | `https://github.com/local/ai-orchestrator.git` — a different repository entirely |

On this host the alias form fails loudly, because that GitHub repository does not
exist — registration looks for a checkout whose origin matches it, finds none, and
tries to clone:

```
git clone https://github.com/local/ai-orchestrator.git ... failed (exit 128): remote: Repository not found.
```

The dangerous case is the one where such an owner and name *do* exist: the alias
would then register a genuinely unrelated repository under the name you wanted.
Always pass the path, which is what `config/onevcs.checkouts` holds.

### What the migration off `repos.json` could not carry

This host ran on a pre-adoption registry at `~/.ai-orchestrator/repos.json` before
`onevcs` owned it. `tests/fixtures/pre-adoption-repos.json` is that registry, and
`tests/e2e/test_repo_registry_apply_e2e.py` holds `config/onevcs.rules.yml` to
reproducing every identity's publication behaviour from it. Three things changed
shape on the way across, none of them silently:

| Was | Is | Why |
| --- | --- | --- |
| alias `local/ai-orchestrator`, `nickderobertis/crozier` | `ai-orchestrator`, `nickderobertis__crozier` | `onevcs` derives the alias from the checkout's directory name; no surface names one. |
| identity `repo_type` / `workflow` / merge strategy | a rule's `publication` + `approvals` | The registry's own copies were `register`'s derivation and unsettable, and onevcs 0.21.0 removed them; the rules file is what every verb reads. |
| a gate template with `{base}` | nothing — the rules file names no verifier | onevcs 0.11.0 removed the gate concept. What verifies a change is the repository's own merge path, which reads the comparison identity from `$ONEVCS_COMPARISON_REMOTE` / `$ONEVCS_COMPARISON_BASE`. |

## 8. Verify the host

Spend one real agent-harness turn end to end:

```sh
just smoke
```

Then probe each identity individually, **through the wrapper**:

```sh
cd ~/projects/ai-orchestrator
for identity in claude-code:alternate claude-code:alternate2 claude-code:primary-backup \
  claude-code:primary codex codex:alternate; do
  scripts/oneharness-agent.sh run --harness "$identity" --prompt "Reply with OK"
done
```

Use `scripts/oneharness-agent.sh` rather than a bare `oneharness run`: it exports
the portable indirections the variants name in `env_from` — the four Claude ones,
`ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR`, `ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR`,
`ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR` and
`ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR`, and `ORCHESTRATOR_CODEX_ALT_HOME` — and oneharness refuses to start when one of them is
unset in the parent. The wrapper requires `run` as its first argument and supplies
the agent chain's `--config` itself.

A probe that reports

```
"fell_through": [{"harness": "codex:alternate", "reason": "auth"}]
```

is the **not-logged-in** state, not a broken config: finish that identity's login in
[step 4](#4-the-six-harness-logins).

Finally, prove the checkout itself:

```sh
just gate
```

## Checklist

- [ ] `git`, `just`, `uv`, `jq`, `gh`, `node`/`npm` on `PATH`
- [ ] `gh auth login`; global git `user.name` and `user.email`
- [ ] canonical checkout **and** isolated safety clone; `just bootstrap` in the canonical one
- [ ] all six harness identities logged in, and the primary's `.claude.json` created by
      its one-time interactive run
- [ ] one `just session-setup` **after** the Claude logins; all four
      `hasTrustDialogAccepted` values `true`
- [ ] allowlister installed and its codex hook wired
- [ ] registry rebuilt with checkout **paths**; `just repos --audit-gate-coverage` reviewed
- [ ] `just repos-bootstrap` reports every present sibling `ran` or `unchanged`, none
      `failed` or `refused`
- [ ] `just smoke`, the per-identity probes, and `just gate` all green
