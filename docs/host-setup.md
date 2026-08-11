# Host setup

The one-time, **manual** steps that bring a new machine to a working state — and,
just as importantly, what is **already automated**, so nothing here is redone by
hand. Everything below was reconstructed by reading the scripts on a host that had
just been brought up; this document exists so the next host is something to follow
rather than an investigation to repeat.

Add a subscription to an existing host? Only [The five harness
logins](#4-the-five-harness-logins) and [The trust-marking
pass](#5-the-trust-marking-pass-order-matters) apply.

## What is already automated

**Do not install these by hand.** `just bootstrap` runs, in order:

1. `scripts/session-setup.sh` (below),
2. `scripts/workspace-install.sh --force` — the locked Bun install, reapplied,
3. `scripts/nx.sh run-many -t bootstrap` — each project's own setup,
4. `git config core.hooksPath .githooks` — activates the pre-push `just gate`,
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
- sweeps reclaimable scratch (`just sweep-scratch`);
- installs `bun` via npm, and `codex` via npm when it is absent, exposing a stable
  `~/.local/bin/codex`;
- wires allowlister's codex `repo-write` PreToolUse hook — **only if allowlister is
  already installed** (see [step 6](#6-install-allowlister-nothing-automates-this));
- marks this checkout trusted in both alternate Claude config directories —
  **only if those configs already exist** (see [step
  5](#5-the-trust-marking-pass-order-matters));
- persists the session's `PATH` (and `LLMLINT_ONEHARNESS_BIN`) into Claude Code's
  session env file, creating its parent directory when the config directory is new;
- hands off to `scripts/setup-llmlint.sh`, which installs `llmlint-cli` with
  `uv tool` — that one install brings the llmlint judge tier and its bundled
  oneharness.

What automation cannot do is authenticate an account, accept a trust dialog,
install allowlister, or rebuild the per-machine repository registry. That is
exactly what the rest of this document is.

## 1. Prerequisites nothing here installs

| Tool | Why this repository needs it |
| --- | --- |
| `git` | every checkout, worktree, clone, and merge in the lifecycle |
| `just` | the whole command surface; session setup itself shells to `just sweep-scratch` |
| `uv` | installs the pinned onejudge/oneharness into `.venv` and llmlint via `uv tool` |
| `jq` | the only tool that can mark the alternate Claude workspaces trusted |
| `gh` | GitHub publication (PRs, checks) and repository-type inference (`gh api user --jq .login`) |
| `node` / `npm` | how the codex CLI and bun are installed |

Two of those fail quietly enough to call out:

- **`jq`.** Without it the trust marking is a no-op with only a log line, and
  session setup's exit status is unchanged — so a host missing `jq` looks healthy
  until a dispatch stalls on a trust dialog:

  ```
  session-setup: cannot mark alternate Claude workspaces trusted: jq is unavailable
  session-setup: alternate Claude workspace trust setup failed; continuing
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

## 4. The five harness logins

Every role's chain — worker, judge, orchestrator, llmlint — names the same five
identities and differs only in their order, so an identity nobody logged into is
quota that role loses once everything ahead of it is exhausted. Log in to all five:

| Identity | Command |
| --- | --- |
| `claude-code:primary` | `claude` (the default `$HOME/.claude` config directory) |
| `claude-code:alternate` | `CLAUDE_CONFIG_DIR="$HOME/.claude-alt" claude`, then `/login` |
| `claude-code:alternate2` | `CLAUDE_CONFIG_DIR="$HOME/.claude-alt2" claude`, then `/login` |
| `codex` | `codex login` |
| `codex:alternate` | `CODEX_HOME="$HOME/.codex-alt" codex login` |

The **primary** Claude identity is the only one whose trust dialog you answer
interactively: session setup marks trust for the two alternates with `jq` and never
touches `$HOME/.claude`. Accept it for the checkout root when you first run `claude`
there.

Leaving one unauthenticated is safe but not free of consequence — it reports
`auth` and falls through to the next candidate, costing that role the quota rather
than breaking it. Check a Codex login with
`CODEX_HOME="$HOME/.codex-alt" codex login status`; check every identity with the
probe in [step 8](#8-verify-the-host).

## 5. The trust-marking pass (order matters)

`scripts/session-setup.sh` marks both alternates' `.claude.json` — but only a
config file **that already exists**, and it is the login in step 4 that creates it.
So the session doing the authenticating is always too early, and one explicit pass
afterwards is required:

```sh
cd ~/projects/ai-orchestrator
just session-setup
```

Verify both alternates, keyed on the checkout root:

```sh
for dir in "$HOME/.claude-alt" "$HOME/.claude-alt2"; do
  jq -r --arg root "$HOME/projects/ai-orchestrator" \
    '.projects[$root].hasTrustDialogAccepted' "$dir/.claude.json"
done
```

Both lines must print `true`. `null` means the pass has not run since that config
directory was created — rerun the command above. (Session setup marks the git
common directory's parent and the repository root, which are the same path in the
canonical checkout.)

Every dispatch marks its own clone, worktree, and result the same way, so this file
is on the hot path of the whole harness and nothing else prunes it. Two properties
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

The registry lives at `~/.ai-orchestrator/repos.json` (override the state root with
`AI_ORCHESTRATOR_HOME`). It is **per-machine and untracked**, so a new host starts
with none of it. Register each repository you dispatch against; at minimum, this
repository's two checkouts:

```sh
just register-repo ~/projects/ai-orchestrator --workflow local --repo-type single-owner --gate 'just gate'
just register-repo ~/projects/ai-orchestrator-isolated
```

The first registration establishes the shared identity — workflow, repository type,
and gate belong to the identity, so the second alias inherits them:

```
registered checkout=/home/nick/projects/ai-orchestrator alias=local/ai-orchestrator identity=https://github.com/nickderobertis/ai-orchestrator repository_type=single-owner publication_workflow=local gate=just gate
```

Then confirm the routing, and that each identity's merge path actually runs a gate:

```sh
just repos
just repos --audit-gate-coverage
```

### Register with the checkout path, never the alias

`local/ai-orchestrator` is the **alias** a path registration produces; it is not an
input you can feed back in. Passing it is parsed as a GitHub `owner/name`:

| Spec you pass | Identity it resolves to |
| --- | --- |
| `~/projects/ai-orchestrator` | a local checkout at that path, alias `local/ai-orchestrator` |
| `local/ai-orchestrator` | `https://github.com/local/ai-orchestrator.git` — a different repository entirely |

On this host the alias form fails loudly, because that GitHub repository does not
exist — registration looks for a checkout whose origin matches it, finds none, and
tries to clone:

```
git clone https://github.com/local/ai-orchestrator.git ... failed (exit 128): remote: Repository not found.
```

The dangerous case is the one where such an owner and name *do* exist: the alias
would then register a genuinely unrelated repository under the name you wanted.
Always pass the path. `tests/test_registry.py` pins both outcomes above so this
warning cannot rot.

## 8. Verify the host

Spend one real agent-harness turn end to end:

```sh
just smoke
```

Then probe each identity individually, **through the wrapper**:

```sh
cd ~/projects/ai-orchestrator
for identity in claude-code:alternate claude-code:alternate2 claude-code:primary codex codex:alternate; do
  scripts/oneharness-agent.sh run --harness "$identity" --prompt "Reply with OK"
done
```

Use `scripts/oneharness-agent.sh` rather than a bare `oneharness run`: it exports
the portable indirections the variants name in `env_from` —
`ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR`, `ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR`, and
`ORCHESTRATOR_CODEX_ALT_HOME` — and oneharness refuses to start when one of them is
unset in the parent. The wrapper requires `run` as its first argument and supplies
the agent chain's `--config` itself.

A probe that reports

```
"fell_through": [{"harness": "codex:alternate", "reason": "auth"}]
```

is the **not-logged-in** state, not a broken config: finish that identity's login in
[step 4](#4-the-five-harness-logins).

Finally, prove the checkout itself:

```sh
just gate
```

## Checklist

- [ ] `git`, `just`, `uv`, `jq`, `gh`, `node`/`npm` on `PATH`
- [ ] `gh auth login`; global git `user.name` and `user.email`
- [ ] canonical checkout **and** isolated safety clone; `just bootstrap` in the canonical one
- [ ] all five harness identities logged in
- [ ] one `just session-setup` **after** the alternate logins; both
      `hasTrustDialogAccepted` values `true`
- [ ] allowlister installed and its codex hook wired
- [ ] registry rebuilt with checkout **paths**; `just repos --audit-gate-coverage` reviewed
- [ ] `just smoke`, the per-identity probes, and `just gate` all green
