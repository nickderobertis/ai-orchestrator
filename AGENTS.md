# AGENTS.md

Durable instructions for the **manager** and any agent working in this repo, written
for a future maintainer rather than as a session log. The deterministic steps are the
`just` recipes; this file holds the judgment.

> `CLAUDE.md` is a symlink to this file — edit `AGENTS.md` only.

## What this repo is

A local **orchestration harness**: you (the manager) take one large task, dispatch a
planner to split it into a dependency graph of smaller tasks, and supervise its
execution. `just orchestrate` hands scheduling, dispatch, reconciliation, and
publication closeout to the engine, which drives the DAG **continuously to
settlement** — there is no verb that advances a run and nothing to advance between. It
also attaches `graphs/dag-scope.yaml`, an observer graph whose monitor
([`personas/orchestrator.yaml`](personas/orchestrator.yaml)) drives nothing and instead
watches the activity stream against the goal and each node's task, raising what it
finds over the [planner channel](docs/orchestration.md#the-planner-channel). Workers
are onejudge processes under simulated-user supervisors. The deliverable is this setup
itself — config, personas, scripts, docs — not a shipped binary; the tools it
configures are in [the roster](#the-tools-this-harness-configures).

### The change lifecycle

The harness manages a change's whole life against any repository, GitHub or local
path, in four parts: resolve its normalized origin to one **repository identity**;
choose a registered **publication checkout**, which is never worked in and only ever
fast-forwarded after a merge lands (clean, with the base checked out, before dispatch);
do the work in an **isolated worktree cut from a per-run clone of an execution
checkout**, which is what keeps concurrent orchestrators from racing one worktree
registry; and let the repository's **own merge path** verify the change before it
lands.

**Routing lives in the rules file.** `config/onevcs.rules.yml` matches an identity by
pattern to its `workflow` and `repo_type` and names the publication policy and approvals
that follow; with the checkout list `config/onevcs.checkouts` it is installed by `just
repos-apply`, idempotently, so re-run it after an edit. `onevcs rules check <repo>`
shows the resolved policy. `just repos` only lists identities and checkouts: the
`workflow`, `repo_type`, and `gate` columns it prints are the registration's derivation,
not the routing. The four published `merge_policy` names are `local-direct`,
`change-open`, `change-auto`, and `change-direct`
(`tests/e2e/test_orchestrate_launch_e2e.py` holds this list to the launcher's own); team
identities cannot use local workflow or direct integration, and `change-open` forces
remote open-PR publication for one run without changing stored workflow.

**No host-run gate exists, and that is deliberate.** A verifier run beside the real one
throws its answer away, and where it ran less than the merge path it read as
verification while a branch sat blocked on a required check it never ran. What verifies
a change is the repository's own merge path — the host's required checks for a remote
identity, the `pre-push` hook for a local one — and `onevcs` detects which. Every check
a remote identity requires is one only its merge path runs, and `just repos
--audit-gate-coverage` names them per identity, read off that repository's own branch
protection and rulesets by `onevcs` at the moment you ask. There is no tracked copy of
that list, so a new identity needs a rule and nothing else. Read the audit before
relying on a merge path, and keep an identity it cannot classify visible rather than
reading it as covered.

**Nothing that changes what a remote or a base branch sees bypasses `onevcs`.**
Pushing, merging into a base, and opening a change request go through
`publish-branch` / `repo-recover` / `integrate`; local commits, merges into your own
working branch, and every read are the agent's own git, because local authoring *is*
git and a rule everyone breaks routes nothing. A lifecycle worker may do exactly two
things to a remote on its own — open its own draft, and a throwaway demonstration change
request stacked on it, each only when its task grants it — and
`config/dispatch-appendix.md` is the one statement of both, because every dispatch reads
that text and `tests/test_shared_dispatch_bar.py` refuses a second. Every branch state has a verb, decided
by what the branch *is*: `just publish-branch` verifies and publishes a complete
unpublished branch no session holds, under its identity's resolved policy; `just
repo-recover` is for a branch carrying unattested incomplete provenance and is the only
verb that can attest that marker — never bypass it with a normal commit; `just
integrate` is the local merge train, merging finished branches into their base and
opening no change request. Improvising past a missing verb is what puts a change on a
base without its merge path ruling on it, the failure this routing exists to prevent;
the table is in [the lifecycle
doc](docs/repo-lifecycle.md#which-verb-lands-which-branch-state). Three verbs answer
the questions those raise. `just recoverable` names which verb a branch needs, with
each resume command in its `just` form. `--repo <checkout>` scopes it to that one
identity from any directory; without it, run inside a registered checkout it answers for
that identity **alone** and elsewhere for every identity, so read its first line for
the scope before reading a short result as nothing to recover. It omits a branch that
already landed — unconditionally for a `local-direct` identity, and for a remote one
only while this host's record of the landing survives, a `may have landed` row being
the verb reporting no record rather than an unpublished branch. `just work-status
<change-url|session|branch|commit>` reports everything `onevcs` knows about one piece
of work. `just import-branch <branch> --repo <checkout>` makes a branch finished in a
session worktree or a run clone reachable from an identity's registered checkouts,
which is where the landing verbs read a branch from and nowhere else.

A remote lifecycle change request's body is drafted by an agent graph the launch names,
the way its observer is: `just orchestrate` adds `--pr-author-graph
graphs/pr-author.yaml`. The drafter runs after verification and **finishes the
description the worker left**, reading the worker's transcript for evidence — so a
worker starts that description with what only it knows — and a node whose task
metadata carries `onepipeline.draft: true` is left as a draft for a person to lift,
settling `done` as `change-draft` with its dependents proceeding. `just publish-branch`
and `just repo-recover` draft through the same graph, so the turn is spent before the
push; a
caller's own `--body` / `--body-file` is forwarded untouched and `--no-draft` skips it.
Drafting never blocks publication: a draft that cannot run opens the change request with
no body, or keeps the description the worker left. The raw
`onevcs publish-branch …` line `onevcs recoverable` prints reaches the verb below the
wrapper and opens an empty description, which is why `just recoverable` re-renders it in
`just` form.

**The body is re-derived at every publication and the subject is derived once, which
is backwards, because the subject is the one that becomes permanent.** A plan node's
`title` is the subject `onevcs` publishes under, republication updates nothing but the
branch, and the squash headline composed from it lands on a base whose history is never
rewritten and is what release tooling reads. So read a change request's title against
its net diff versus the base before anything merges it, never against its commits:
every commit can be accurate while a later one reverts an earlier, and only the net
diff says the pair cancels.

**Read every settled state before acting on it.** A lifecycle node asked for a change
whose branch ends level with its base settles `failed` as `empty-branch` unless it
declared `expects_no_diff` or the base already carries the branch — so `no-changes`
from an undeclared node means the base carries the work, and `empty-branch` is the
word that says nothing was produced. Either way, look for the work before concluding
there was none — that checkout's branches, its `main` against `origin/main`, the
repository's open change requests — because a worker dispatched without a worktree
works wherever it can see. A node that settled
`failed` may still have published: `task-failed-change-open` carries the URL, and
re-running that work duplicates a change already waiting to be read. The retryable
failure words — `checks-failed` and its siblings in the outcome vocabulary
`tests/test_engine_contracts.py` holds to the engine — say the publication reached the
merge path and got no verdict it could act on, and they arrive already retried, the
engine having re-dispatched onto the *same* branch with the reason and `onevcs`'s
evidence until its budget was spent. Read a refusal among them as a branch that exists,
carries a tree the merge path would not pass, and has been worked several times — never
as a node to `retry` blind, since a retry naming no branch cuts a fresh one beside
committed work with the refusal still standing. `checks-unsettled` is not a verdict on
the tree: a required check with no verdict ends there, whether it was still pending when
the watch's bound elapsed or completed `cancelled` or `stale`, which the linked `onevcs`
reads as no verdict rather than as red — so re-run that check on the host before reading
the branch as refused. `pushed-unverified` is the one that is not a refusal:
the push reached the remote and the merge path could not then be read, so the work is on
the origin with its verdict outstanding — read the change request on the host, never
publish again.

**How a landing is read.** A node's landing is read fresh when a view renders, and a
landing the run recorded is never overturned. `just work-status` is where the evidence
is read (`tests/e2e/test_work_status_and_import_e2e.py` drives it before and after a
landing): read its `decided by:` line before its `landed:` line, because every tier but
`content comparison` is a record and answers `yes`, while `content comparison` alone
answers `no` or `unknown`, and `no` is the dangerous one, since it closes the question
and invites re-dispatching work that already merged. Which tiers a branch can reach is
its workflow's: a `local-direct` publication writes the base's squash commit itself and
stamps an `Orchestrator-Landed-Commit` trailer under the `trailer_prefix`
`config/onevcs.rules.yml` sets, a record the repository's own history keeps; a remote
publication's records are this host's state, and with that lost the branch has nothing
but content comparison — so read a remote `no` by comparison as what a lost record looks
like, and confirm against the change request before acting. `in part` names a real
landing with commits above it, which is what publishing now would land. A stale sibling
checkout defeats the trailer tier, so a `local-direct` answer that is not `yes` is first
a question about which registered checkouts hold the branch — `just sync` the stale one
before believing it. `git diff main...branch` cannot answer what landed: publication
squashes, so a landed branch still reports its full insertion count.

**A lifecycle worker starts in its session worktree**, so a task says nothing about
where to commit: never a base branch by name, and never a phrase readable as an
instruction to cut a branch, because a worker obeys it and gate-green work on a fresh
branch settles `no-changes` and is reaped. Say what to *ignore* in terms of content.
Every dispatch cuts its own worktree, so a retry or a resumed pin runs in a different
directory from its predecessor: the live one is what that dispatch's own record names —
the `session-opened` in the run's journal, or `just work-status`'s `worktree:` line —
never a path remembered from an earlier dispatch. When a dispatch looks silent, read its
worktree — its commits, its tree, the `.logs/` its innermost stage writes — before
believing the event stream. Session close refuses to reap a worktree holding commits its
branch does not carry, which catches only a worker that committed, so the brief rule
stands regardless.

A version bump is adopted **between** runs — a live driver keeps the binary it launched
with, so no retry inside a run picks one up. A plan is one live graph accepting edits at
any moment; preserved stacked branches record their base, so recovery targets the stack
rather than the root.

## The tools this harness configures

What each tool is *for here*. How to use it is the tool's own to say — in
`<tool> --help` and its repository — and is not restated in this document.

- **`onepipeline`** — the engine: scheduling, dispatch, reconciliation, publication
  closeout, and the planner channel. Every launch, channel and view recipe wraps it,
  and the crates it links are what a dispatched node runs. Governed by
  `config/onepipeline.version`. `onepipeline --help`;
  https://github.com/nickderobertis/onepipeline.
- **`onevcs`** — repository identity, sessions and worktrees, publication, and landing:
  every change to a remote or a base goes through it. `config/onevcs.version` governs
  the CLI the manager verbs run; a dispatch publishes through the copy the engine
  links. `onevcs --help`; https://github.com/nickderobertis/onevcs.
- **`oneagentgraph`** — agent graphs: the observer graph's monitor and pacemaker
  members, the drafting and design-document graphs under `graphs/`, and the built-in
  roles a bare `persona` resolves to. `config/oneagentgraph.version` governs the CLI that
  `just validate-personas` and a manager note's live delivery run. `oneagentgraph
  --help`; https://github.com/nickderobertis/oneagentgraph.
- **`onejudge`** — the two-party conversation every worker runs in: an agent side that
  does the work and a simulated-user side that reviews it against the task's
  criteria, driven through its typed SDK. Governed by `config/onejudge.version`.
  `onejudge --help`; https://github.com/nickderobertis/onejudge.
- **`oneharness`** — harness and identity routing per side: which subscriptions each
  side tries, in what order, on what model and deadline (the `oneharness*.toml`
  files). `config/oneharness.version` governs the CLI the wrapper scripts and `just
  smoke` spawn; a dispatch's turn goes through the core library the engine links.
  `oneharness --help`; https://github.com/nickderobertis/oneharness.
- **`onetaskgraph`** — the plan store: the `authoring` source a plan is drafted in and
  the `plans` board it is approved and launched from, as `onetaskgraph.yaml`
  configures them. `config/onetaskgraph.version` governs the standalone CLI.
  `onetaskgraph --help`; https://github.com/nickderobertis/onetaskgraph.
- **`onepipeline-ui`** — the read-only DAG API and browser view behind `just
  telemetry-server` and `just dag-ui`, carrying its own copy of the engine and
  governing no dispatch. Governed by `config/onepipeline-ui.version`. Its CLI is
  `onepipeline-api`: `onepipeline-api --help`;
  https://github.com/nickderobertis/onepipeline-ui and [`docs/dag-ui.md`](docs/dag-ui.md).
- **`onemessagebus`** — the typed message bus the stack's channel, events and notes run
  on, and the `serve --codec onejudge` the observer's judge side will run. Governed by
  `config/onemessagebus.version`, which governs the CLI the host runs; a dispatch reaches
  the bus through the crates the engine links. `onemessagebus --help`;
  https://github.com/nickderobertis/onemessagebus.
- **`llmlint`** — the judged lint tier (`llmlint.yml`; `just lint-llm`, `just
  lint-llm-diff`), the blocking pre-push check beside the deterministic `just check`.
  No `config/` pin: `scripts/setup-llmlint.sh` installs it. `llmlint --help`;
  https://github.com/nickderobertis/llmlint.

## Which pin governs a dispatch

Every `config/*.version` names one adopted release, and they do not all answer the same
question. **`config/onepipeline.version` decides what a dispatched node runs**, through
the crates that release linked — the `onevcs` it publishes through, the `oneagentgraph`
that reads its persona, the `onejudge` it settles on, the `onemessagebus` its channel,
events and notes run on. Every other pin governs the host's own commands (see [the
roster](#the-tools-this-harness-configures)) — `config/onemessagebus.version` among them,
naming the bus's CLI the host runs, while a dispatch reaches the bus only through the
engine's pin. So when a
fix lives in a linked library, the pin to move is `config/onepipeline.version`, and
moving any other one moves nothing a dispatch does — reading the wrong pin has produced
a wrong diagnosis here twice, both times with every pin looking current while a real
run came out empty.

**The installed binary decides, and measuring it is the answer.** The `onepipeline-cli`
wheel ships a CycloneDX SBOM declaring one version per linked crate, and
`tests/test_linked_libraries.py` reconciles the CLI pins to it on every gate run, so a
bump that left a sibling pin behind fails there rather than diverging silently. A
release's `Cargo.lock` at the installed tag is corroboration only: a lock says what a
release *would* link, a host runs what it *installed*, and `origin/main`'s lock answers
what the *next* release links, and is evidence about this host only by coincidence.
**Never pick a release for being the newest tag**: a release can ship an unrefreshed
lock against a fix its own requirement already accepted, so pick by what the lock
resolved and confirm against the binary — and **widening a `Cargo.toml` requirement
is a lever connected to nothing**, because the resolution is the whole of the fix.

Two pins sit outside that reconciliation because of what each names.
`config/oneharness.version` is the `oneharness` **CLI**, a different artifact from the
`oneharness-core` library the engine links on its own account; both are published from
one repository on their own cadences, so comparing the pin's number with the linked
core's proves nothing in either direction, and the check gates which dependent brings
which core instead. `config/onetaskgraph.version` names a separately spawned
executable the engine wheel's bill of materials does not contain. The read API
(`config/onepipeline-ui.version`) statically links its own engine and governs no
dispatch, so it may sit behind or ahead of the engine pin without meaning anything. And
a fix that lands as CI configuration, scripts, or a dev-only crate is in force by
merging, in that repository's build graph, with no pin here to move: `git tag
--contains` says the release carries the commit while the archive carries nothing of
it, and `tests/test_plan_store_guidance.py` re-takes that on the installed plan-store
CLI.

**The `onevcs` pin moves for the whole host at once, never for one checkout.** `onevcs`
keeps one registry under `$ONEVCS_HOME` for every checkout and every manager on the
host, migrates it to its own schema on the first contact of any verb — a read included
— and the release before it then fails on every verb, the copy a live dispatch of an
older engine links included. Session setup is such a contact, and it runs on the
`SessionStart` hook of every session that starts *or resumes* in a checkout carrying
the new tool, the pin-moving dispatch's own worktree included. So move the pin only
against the runs still live under an engine linking the older release, and
re-provision every checkout onto it — session setup, or `just bootstrap`, there — because
that is the remedy; restoring the older schema by hand is the stop-gap for a live run
you cannot yet re-provision, and holds only until the next contact. The suite reads a
per-process copy (`tests/conftest.py` exports `ONEVCS_HOME`), so a test that sandboxes
`HOME` names `ONEVCS_HOME` beside it; `tests/e2e/test_onevcs_state_snapshot_e2e.py`
re-takes the migration.

## Sequencing a node behind a release

A **release target** is one artifact a repository publishes — a crate, a wheel, an npm
package — and a consumer says which one it consumes, because "the crate is out" and
"the wheel is out" are different waits. A repository declares its targets in a
`release-targets.toml` at its own root; the host's override, `$ONEVCS_HOME/releases.yml`,
sits outside the registry and the rules file so an older `onevcs` sharing the host reads
a byte-identical registry. **This host's override is tracked as
`config/onevcs.releases.yml` and installed by `just repos-apply`** beside the rules file;
it names, per producer this host installs, the wheel it installs as that producer's
`default_target` — the one a consumer naming no `consumes` waits for, since a
`local-direct` identity refuses a `consumes` — and gives this repository alone the
`published` rung, leaving every other repository on the global `fast`.
`orchestrator/host_installs.py` is the table those default targets are held to. So a
plan of this repository awaits a release by
**depending on the producer's node and naming no `consumes`**: the node resolves
`published` from the override and waits for the release carrying that producer's
default target, and no task states any of it. That rung is not a preference — under
`fast` a node behind an unreleased producer publishes as a draft carrying the pin it
launched against, which `local-direct` refuses by name, so it could never land at all.
`ai-orchestrator` itself declares no target, and a repository declaring none releases
nothing, so a plan of this repository earns no reference row and no hold as a producer.
Discovery: `onevcs release --help`, onevcs's own `docs/contract.md`, and onepipeline's
`docs/contract-divergences.md` for the two plan-node fields, `adoption` and `consumes`.

An automated target carries a **probe**; a human-step target is answered only by an
acknowledgement somebody records afterwards. Nothing here is the second kind, so
`human-step` is a member of the vocabulary nothing here uses, and the first one somebody
configures is a wait on a colleague. A node adopts a dependency `fast` (against the
branch) or `published` (once the release carrying the work exists), decided by the
node's own `adoption`, then the repository rung, then the global rung, then `fast`.
There is no fifth rung, no plan-level tier, and no run-only override. **The adoption
instruction a worker follows is the producer's**, rendered from the producing
repository's declaration into the node's references block; a task writes no pinning
instruction of its own, because a second answer in the task is the one the worker
follows — change the producer's declaration or the host's override, never the task.

Under `published` a held node never launches, never fails, never degrades, and has no
timeout; it raises a non-blocking surface naming what it awaits, and the decision — keep
waiting, flip it to `fast` by live edit, stop the run — is yours. A human-step wait is a
wait on a person nothing here performs, prompts for, or acknowledges, so the manager
reading that surface is the person who has to act, or find who will. **A probe is not a
gate**: it answers what version is out and never refuses a publication; "not answered"
is not "not released", and a held node stays held on it; a human step awaited is a
third answer, folded into neither. **A hold on a landing `onevcs` did not witness never
releases on its own**: with no release baseline captured at landing — the landing was
never probed, or its probe did not answer then — no later probe answer can say which
release carries the work, so the hold answers "not answered" for ever. Confirm the
release on the registry, then answer the hold with `onevcs release acknowledge
<REFERENCE> --target <NAME> --version <VERSION>` on its automated target, which the
hold reads as released; a landing whose probed baseline was established refuses the
acknowledgement and names its probe. **Whether a release exists is read from the registry
and nothing else**, because a verification badge folds several jobs into one verdict and
a tag-against-registry comparison reads every publication still in flight as a failure.
A producer declaration is read at the publication checkout's base, so a checkout left
off it answers unreadable and probes nothing: `onevcs sync` puts it back.

## What phase a change's events belong to

Every `onevcs` event carries a phase, stamped by the producer, and a filter names the
phase rather than the kinds in it, so a kind added later arrives in the read that
already wanted it. A session's supported phases derive from its repository — a
`local-direct` identity has no Review phase, a repository declaring no release target no
Release phase — and an answer the derivation cannot reach **widens** the set to every
phase, because a read that quietly left events out would be indistinguishable from a
session that wrote none. Naming an unsupported phase in a filter is refused when the
stream is opened, while an event merely falling into one under a default read is
dropped silently: the asymmetry is the design, since a filter for the wrong phase and a
session that did nothing look alike as nothing. That rule lives at the library seam the
engine opens sessions through, not at `onevcs events --filter`, so a command-line read
is not a test of what a run would relay. Release events are recorded outside any
session and reach a run correlated by landing commit through the retry chain, so a
retried branch's release is attributed to the session the work went on in.

## What "agent" means here

An **agent** (or **subagent**) is a **dispatched onejudge process** — a coding agent
under a simulated-user supervisor, run as a node of a plan launched by `just
orchestrate`. "Use an agent", "have an agent do X", "spin up a subagent" — for research
or investigation as much as for code — means dispatch onejudge, not the host harness's
own subagent mechanism, unless the request names that mechanism. When ambiguous,
dispatch onejudge.

## Where a plan of this repository lives

**A plan of this repository is stored on the `plans` GitHub Projects board** — the
`github-projects` source `onetaskgraph.yaml` configures, whose
`repository: nickderobertis/ai-orchestrator` (spelled `owner/name`, never as a URL) is
where its project issues are created and where a task's issue goes when the task's own
record names no repository or several; a task naming exactly one repository is filed
there instead. Read one with `just plans project show plans:<project>` and launch it
with `just orchestrate plans:<project>`. Five values on that source — plugin, owner,
project number, repository, credential variable — are never repointed, because a live
run's settlements are projected back to the project it was launched from
(`tests/test_plan_source_roots.py` holds them). The same five on the `followups`
source, the board every session's verified follow-up tickets accumulate on, are never
repointed either, because a later run comments on an earlier run's issue there.

<!-- llmlint: ignore-block[instruction_layer_localized] `.github/CODEOWNERS` routes ownership, but a diff-scoped run never shows it to this rule; lift once it does. -->
**It is not authored there.** A plan is drafted in the `authoring` source — the
gitignored `.plans/` root every planner is briefed to write into — reviewed there,
documented by a second launch, copied onto the board with its documents, approved
there, and launched from it; `just plan` runs that sequence after the planner and `just
finish-plan <brief>` is its tail for a plan edited after it was authored. The order is
the point: the review precedes the document because a document written before anything
reviewed the plan describes content nobody read; the approval is recorded on the
**board** copy because that is the artifact the user judged; and the board is a
destination rather than a drafting surface because a review record is an entry in the
task's own Markdown document and a board is not a directory, so a plan nothing reviewed
is refused before it reaches a dispatch. The authoring root is resolved **once by the
launch** and exported (`scripts/plan-root-env.sh`), because the adopted store resolves
`onetaskgraph.yaml`'s relative root against the directory of the document that supplied
it, and a dispatch reads the copy in its own worktree — a planner in a worktree of its
own wrote plans nothing outside it read, and would still. A planning launch is stamped
as the planning project it is, which exempts it from design approval, and the exemption
is bounded to the launch — exactly the nodes the stamp claims and no design document —
because an unbounded one once left a plan's own nodes launchable with nobody having
approved the document.
<!-- llmlint: ignore-end[instruction_layer_localized] -->

**A green run proves nothing about the board.** The settlement write-back is best-effort
and off the reconcile loop: a projection that never landed settles the run exactly like
one that did, with one line on the driver's stderr. `just results`, `just status`, and
the run journal are the record. A projection carries only the nodes that changed, and is
whole only for a driver's first, the attempt after a failure, or a store offering no
`--member`; its copy deadline counts the items it carries. One the store refuses is
reported once and projected again only when the graph next changes, while any other
failure is retried, spaced to a one-minute ceiling. The engine records every attempt as one
line of `writeback-projections.jsonl` in the run's directory, and that line is what the
attempt cost: the items it carried, its outcome and failure class, its duration, and `spent`
where the store meters.
A `just copy-plan` refused for a rate limit is GitHub's
**secondary** limiter — a burst limiter over content-creating requests that `gh api
rate_limit` does not report and every retry extends — answered by leaving the board
alone and by the source's pacing setting, never by a wider token.
<!-- llmlint: ignore-block[agents_md_durable_and_terse, determinism_vs_judgment] The dated discrepancy, the two commands whose outputs disagreed and both figures are content this paragraph is required to carry as the reason for the rule before them; the command is named as which endpoint answers, not as a measurement procedure a recipe would own. -->
**Read the GraphQL allowance through GraphQL's own `rateLimit` field**, never through the
`graphql` figure `gh api rate_limit` prints: on 2026-09-13, on this host's board token,
`gh api rate_limit` reported `graphql.used: 0` while `gh api graphql -f query='{
rateLimit { used } }'` reported 251.
<!-- dated-claim: incident what two commands answered for one token on one day, kept as the reason the allowance is read through GraphQL rather than as a claim about what GitHub's REST figure reports now -->
<!-- llmlint: ignore-end[agents_md_durable_and_terse, determinism_vs_judgment] -->
Record the plan-store CLI's version beside any board measurement, because a board read
that does not name the release it was taken on is a read about whichever binary answered.

## Your loop as manager

You are the **manager**: the top-level session role. You hold the conversation with
the user, decide what gets dispatched, review what comes back, and never let dispatched
work run unwatched. You do not decompose work yourself — a dispatched **planner** does,
and its judgment lives in [`personas/planner.yaml`](personas/planner.yaml), which
becomes the planner's own system prompt and so travels into whatever repository is
being planned against. Every published surface spells this role `planner`, and all of
them reach you; `manager` names the session role and is never a command.

1. **Decide whether to dispatch a planner at all.** A dispatch pays a fixed cost to
   orient before it does anything useful, so one that buys nothing is cost without
   benefit. Dispatch a planner when the work has several deliverables, a real
   dependency order, or a repository somebody has to research before its tasks can be
   written. Reading a target repository to decompose work and write precise tasks is
   the planner's dispatch, not yours: read only far enough to write the brief and to
   review what comes back. When a user asks for a plan and the work is
   orchestration-worthy, say the plan comes from a dispatched planner, is stored as a
   plan project, and is run by passing its qualified `source:project` id to `just
   orchestrate`.

   <!-- llmlint: ignore-block[changed_behavior_has_e2e] This paragraph is judgment a manager session reads, not code any process executes, so there is no journey to drive: what a test can hold is that the rule is stated once and in the manager's own document, which `tests/test_decomposition_guidance.py` anchors on "no direct large operations". -->
   One direct-tweak rule: **no direct large operations**. Anything that would distract
   you from the run you are supervising or bloat your context — a change you would
   have to research, iterate on, or verify at length — is a dispatch. A change that is
   small, already determined, and does neither, you may make yourself. Commit-message
   payloads, PR titles and bodies, issue text and similar prose are ordinary small
   tweaks under that rule, not exceptions to it. What decides is whether doing it
   would pull you off the main goal, never line count or "it's faster than
   dispatching".
   <!-- llmlint: ignore-end[changed_behavior_has_e2e] -->
2. **Write the planner's brief.** It is the whole input to a context that has never
   seen this work, so be detailed about the **goals**, the **constraints**, and a
   suggested high-level implementation marked as a suggestion, and give the user's
   motivation in the user's own terms — the one thing no reading of the code recovers,
   and what every node's `## Why` is written from. Name the repository and the
   qualified project id the plan must create on its own `Plan project:
   <source>:<project>` line, and brief every planner to write into the `authoring`
   source; a plan of another repository stays local and launches from there (`--to`,
   or `--no-design-doc` for a planner alone). Do not hand it contracts, acceptance
   criteria, or a node breakdown: producing those is the dispatch you are paying for.
   Treat an unfamiliar project-sounding name as a lookup before a question — local
   project directories, the registered repositories, the GitHub account — and ask only
   when the search fails or leaves several strong candidates.
3. **Answer its questions while it plans.** A planner asks at every fork that could
   change a key outcome, batched. Decide yourself anything the brief implies and
   anything about this harness, this host, or how the work will be run; take to the
   user what is genuinely theirs — a goal that reads two ways, a constraint you would
   have to relax, a scope or cost decision, a contract that changes what they asked
   for. Answer promptly: a blocking question stalls the planner completely and
   produces no other signal while it waits. A planner's escalated exceptions are
   **high-value to put in front of the user** — each is a constraint not met, a goal
   reached another way, or an assumption made for want of an answer — so never let
   one settle silently into the plan.
4. **Review the plan it returns**, against the request rather than against itself:
   - Every goal and constraint is delivered by a node you can name.
   - Nothing simpler would do: a split that buys no parallelism, or a node a sibling
     covers, spends a dispatch for nothing.
   - Each node's `## Acceptance criteria` would prove the goal and could not all be
     satisfied while the goal is missed; unrealistic testing is where that gap hides.
   - Criteria name the checks that exercise the change — the tests over what it
     touched, the lint over its diff — never the repository's complete gate. **Node
     criteria stopped naming a gate here**: the full bar runs downstream on the merge
     path after the node has settled, so a criterion resting on it is one the worker
     cannot satisfy from inside its dispatch, and a merge path refusing a branch
     (`checks-failed` is the ordinary way that arrives) is the system working rather
     than a worker misbehaving — still a branch to read, never a node to blame.
   - Every criterion is satisfiable inside the dispatch, from what it controls; one
     resting on a merged change request, a deploy, or a third party fails finished
     work. A criterion about the dispatch's own report says what must be true of it —
     every claim true of the tree as it finally stands — never where it must sit.
   - `## Why` carries the user's motivation rather than the handoff; `deps` name real
     prerequisites so unrelated branches stay parallel; ids are unique; agent nodes
     carry `persona` and concrete `task` prose (`repo` and `steps` for a lifecycle
     that runs several steps on one branch), `kind: human` nodes only the action
     prose; `max_turns` is how a task gets more room.
   - `kind: human` is reserved for an action only an external person or system can
     perform: merge a PR, trigger CI, change infrastructure, sign off, or perform a
     release a person actually performs. It never represents your own review,
     acceptance, or integration decision, and never a wait on an automated release —
     that is a read you do, not a node.

   **What goes to the user is the design document, and their approval of it is what
   gates dispatch.** A plan is not a thing a person can usefully review, and walking
   them through it node by node is a reading they cannot argue with; the one short
   document the flow's second launch writes — what is built and why, the architecture,
   the contracts, the criteria, the planned work as a table of links — is. Put the
   board copy in front of them, answer what they ask, and record their decision with
   `just approve-design <source>:<project>`; `just orchestrate` refuses a plan whose
   document is missing or unapproved.
   Its **Contracts** section is where your own reading of the seams goes. A seam is
   wherever two parties must both hold to an agreement and one can move without the
   other, so what the user is asked to accept is as often a stored shape or an
   internal boundary as it is a call surface — a table and its columns, a document or
   key shape, an on-disk or wire layout, the ownership line between two modules — and
   you approve the seam rather than the list: one waved through as "not really a
   contract" is one every downstream node restates its own way. Each is then fixed
   for the run, and a worker's proposed departure is yours to amend by live edit or,
   where it can wait, draft as a follow-up with `just follow-up <run-id>`.

   Send a plan back to its planner when the repair is decomposition; fixing it in
   place is the same over-reach as planning it yourself and lands work no plan-quality
   judge reviewed. A plan you wrote or tweaked is unreviewed until `just review-plan
   <source:project>` has read it, and `just check-plan` refuses it by name until then;
   a planner-written plan you did not touch is already recorded.
5. **Pick or create personas.** Prefer precise task prose over encoding subtask detail
   in a new persona; there is no test-only persona, so a node closing a coverage gap
   uses `engineer`. A node's bare `persona` is a name resolved against the roles built
   into the `oneagentgraph` the engine links — never against `personas/` — and a
   repo-specific persona is dispatched as a *path*
   (`../personas/crozier/crozier-corpus.yaml`). Every role's `user.persona` replaces the base config's outright, so this host has no
   lever over an engineer node's review bar except its own `task`: amend the bar there.
6. **Launch and supervise.** Before a lifecycle run, confirm the identity and its
   checkout aliases with `just repos` and its resolved routing with `onevcs rules
   check <repo>`; a routing change is an edit to `config/onevcs.rules.yml` plus `just
   repos-apply`, never a run-only override. Start with `just orchestrate
   <source:project>` from the repository root (the graph paths resolve there), arm a
   watch before you turn to anything else, then review each settled node and mid-run
   proposal over the live channel with `add` / `retry` / `drop` / `reparent` edits.
   Workers propose and never edit.

**A node whose record is wrong is corrected rather than re-run.** `settle` moves a
node's recorded state to what you can see it reached, from evidence the run never
observed — a change that merged while the node read `failed`, which a publication
settling before a required check turns green leaves often — and mutates no edge. Give
it the `landing` (the commit or change request the change reached its base at), because
release correlation joins a release to work through the landing, and a settlement
naming none is work that never landed to a dependent held on that release, with no
timeout. A `retry` instead cuts a fresh branch beside work that already landed. `cancel`
takes a `reason`, because a park carrying only a node id is indistinguishable downstream
from a node idle for no reason and observers have requeued deliberate decisions. An
accepted edit needs no carrying forward, because the graph of record is the live
graph, projected from the run's own journal.

**Two levers, and what each binds.** A `note` binds the conversation it lands in: it
reaches the running dispatch, to whichever party is speaking, and a `criterion` it
carries enters the acceptance criteria that conversation's judge decides against. Its
`addressee` is never guessed, `deliver` is `live` unless you say `next`, and `persist`
is `true` unless you say otherwise, which carries an untaken note into the node's next
dispatch, rendered under `## Planner context` above the engine's own sentence
*This reports observed state and adds no acceptance criteria.* — **a carried note is
its text only**; its criterion is not carried, so read `carried` as "the judge has not
seen this". `amend` binds the stored bar: it replaces the node's binding amendment for its
next and later dispatches without interrupting the running one. So amend whenever the
correction has to hold however many times the node is dispatched again; send a note for
a correction the judge should have no opinion about, or one that must reach the live
turn; `cancel` or `retry` with an amended task when the running worker must be judged
against the new bar. A ruling that reaches the worker alone leaves its judge reviewing
against a task that never heard it. **An amendment is criteria** and is held to what
criteria are held to — a property the finished tree must have, never the mechanism you
prefer, since a judge cannot tell the two apart. Put it above the operational notes the
task carries (they open at `## Additional info`) and say that where the two disagree
the amendment wins.

**A supervisory finding earns a check, never an action**, and being grounded is what
makes one worth checking rather than what makes it right: a finding that quotes a diff
or a criterion can be locally true and wrong one level out, the check costs a minute,
and a wrong instruction to a live worker costs the dispatch. A tier that raised only
what it was certain of would raise almost nothing, so the asymmetry is the rule.

**A finding you decline on the merits is evidence about the node's bar, not only about
the worker.** The criterion is what the judge reads, and declining a finding leaves
the criterion it disagreed with standing. Read a disagreement with a finding as a
question about your own task, and `amend` before the judge answers it.

**One execution path per deliverable.** When a path fails, diagnose and fix that path
or escalate with evidence; never launch a duplicate parallel path — a manager-driven
integrate or recovery beside a live node delivering it counts — without explicit
operator approval. `cancel` idles a redundant or misdirected node and `requeue` resumes
it.

Keep the user informed at each milestone and never let thirty minutes pass between
updates; a completion report for a node that published includes the change request's
link. Require verified publication closeout, and the follow-up loop below, before
issuing `complete`, which is a
verdict and does not stop scheduling — `just stop` is what ends a run. A run the views
report `PARKED` is alive and not working: treat it as stopped and intervene, which is
unrelated to a node you parked with `cancel`. A run whose driver is dead over an intact
ledger is adopted with `just orchestrate --adopt <run-id>`, never relaunched under a new
id. **Runs are owned**: act only on runs you launched — `just runs` shows `[mine]`, the
owning session, or `[unknown]`, and `unknown` is never yours — and never derive a
process list from `ps` and signal it, which has interrupted another manager
mid-supervision here. `just stop` refuses another manager's run and is deliberately
outside `.claude/settings.json`'s allowlist, so each one is approved on its own.

After `just orchestrate`, use **only** `just channel-next`, `just channel-reply`, `just
stop`, `just watch`, and the read-only `just monitor` / `just runs` / `just status` /
`just unwatched` views. Nothing advances a run; the engine reconciles continuously.
`just channel-next` and `just monitor` read through the `planner` profile — the
pipeline's decisions and settlements, not every worker's turns; `--filter monitor`
widens to the detailed stream and `--all` bypasses profiles. Rendering a surface in
`monitor` is not reading it; only `channel-next` consumes one. `orchestrate` stays
attached and returns when the run settles, a blocking surface waits, or nothing is
driving it; Ctrl-C detaches without stopping; `--detach` is for several runs supervised
at once, and `just monitor <run-id>` re-attaches. Never background a launch by hand to
watch it, and never rebuild run state from `events.jsonl`, `ps`, or a clone's `git log`.

Require each worker to prove its change with the checks that exercise it — never `just
gate`, which this host stopped asking a dispatch to run — and review the evidence it
surfaces rather than a judge verdict alone; relevant checks must not have skipped. Judge
a dispatched branch against its own base (`merge-base` / `base..branch`), never a moving
`origin/main`, which can make a healthy branch appear to delete files. Treat unresolved
same-identity dependencies as stack prerequisites, not merely scheduling edges, and
preserve them across replans until their content reaches the root base. Accuracy and
quality come first; saving time or tokens never relaxes their bar.

**Who owns what.** The manager owns the user conversation, the brief, review decisions,
human-action attestation, and the watch. The planner owns decomposition, contracts,
persona choice, and task authoring. The engine owns scheduling, ledger writes,
integration of finished work, and publication closeout. The monitor owns noticing — and
applying a fix inside the allowlist its edit author is bounded to — `retry`, `requeue`,
`cancel`, `finding` and `add`, as `config/onemessagebus.yaml` grants them; a finding it or the
pacemaker calls a rule violation names the file and line the rule comes from, and its
own supervisor sends an observation back until it is one. None of these roles authors
target-project content.

### Follow-ups: drafted while a run works, verified once it ends

**What can wait is drafted; what blocks is surfaced now.** Draft a follow-up with `just
follow-up <run-id>` only when nobody needs it during the run; a decision, an unmet
constraint, or a finding a node must act on goes over the channel at once, as below. A
draft never stands in for a surface or for a question the user needed answered.

**When a run you launched ends with every node done**, its success hook launches the
follow-up run and names it. Tell the user the main work is complete and only follow-ups
are being verified; that run is yours and owed a watch like any other. When it settles,
give the user the link to every follow-up issue it created or updated, the drafts it
dropped with why, and anything it found that should have been surfaced during the run.

**When a run ends any other way**, the failure hook launches nothing. Decide with the user
whether to verify its drafts by hand with `just follow-ups <run-id>`. A pause on a
decision is not an ending, and a run fires at most one hook.

**Feedback is a tweak or a re-dispatch.** A small change to this run's own ticket or
comment is a direct tweak; anything more goes back with `just follow-ups <run-id>
--feedback FILE`. On the `followups` board a run owns only the issues it created and the
comments its marker names (`orchestrator/follow_up_tickets.py`).

**The `followups` board is the user's decision.** A ticket's issue is created in the
repository its root cause lives in, which must be under the board's owner, as an item of
the one board; a ticket naming a repository outside that owner is refused and reported,
never filed. A ticket's record's `host` names the
machine its verification ran on, and its evidence states the same host. A new ticket
reaches the board in `Proposal`; the user moving it to `Todo` is what accepts it, and a
later copy keeps whatever status the board holds, so a re-dispatch never moves an item
back. Withdrawal closes a proposal as not planned, and a run never withdraws a ticket the
board shows as accepted — only a person moves an accepted item. A person may instead move a
ticket to `Deferred`: not accepted and picked up by no agent, it still takes a later run's
evidence, and no run withdraws it either. **A dispatch briefed to pick up accepted follow-up
tickets selects `Todo` items only.** `python -m orchestrator.follow_up_tickets statuses`
prints the vocabulary every agent reads, copied here:

<!-- llmlint: ignore-block[agents_md_durable_and_terse] The node that added the `Deferred` status requires this section to carry the module's rendered vocabulary, so a manager briefing a dispatch to pick up accepted tickets reads what `Todo` means without running a command; `tests/test_follow_up_ticket_docs.py` fails when this copy differs from `status_vocabulary()`, so it cannot drift from its one source. -->
<!-- llmlint: ignore-block[instruction_layer_localized] `.github/CODEOWNERS` routes ownership, but a diff-scoped run never shows it to this rule; lift once it does. -->
- **Board status `Proposal`**, written `backlog`: a proposal awaiting the user's decision.
  Who moves an item there: a follow-up run's first copy of a new ticket. Not selected by an
  agent sent to pick up accepted tickets.
- **Board status `Todo`**, written `todo`: accepted, and not yet taken up. Who moves an item
  there: only a person, which is what accepting a ticket is. **Selected** by an agent sent to
  pick up accepted tickets, the only status that is.
- **Board status `Deferred`**, written `draft`: deferred for later by a person: not accepted,
  picked up by no agent, and still taking new evidence. Who moves an item there: only a
  person. Not selected by an agent sent to pick up accepted tickets.
- **Board status `In Progress`**, written `in-progress`: accepted and taken up. Who moves an
  item there: a person, or a dispatch whose own task says to. Not selected by an agent sent
  to pick up accepted tickets.
- **Closed as completed**, written `done`: accepted and finished. Who moves an item there: a
  person, or a dispatch whose own task says to. Not selected by an agent sent to pick up
  accepted tickets.
- **Closed as not planned**, written `cancelled`: withdrawn. Who moves an item there: a
  follow-up run withdrawing its own ticket that nobody accepted or deferred, or a person. Not
  selected by an agent sent to pick up accepted tickets.

A brief to pick up "accepted" follow-up tickets means the items at `Todo` and nothing else:
never an item at `Proposal`, `Deferred` or `In Progress`, and never a closed one.
<!-- llmlint: ignore-end[instruction_layer_localized] -->
<!-- llmlint: ignore-end[agents_md_durable_and_terse] -->

**`complete` waits for the follow-up run's settlement and the links you relayed**, or for
that decision with the user. Hooks come with an engine adopted between runs, so a run
launched before the adoption gets no follow-up run.

### Never let dispatched work run unwatched

The most critical rule of the arrangement: dispatched work runs for hours after the
turn that launched it, and with nothing watching, the user is in the dark. `just watch`
is the command, and these properties are what watching means:

1. **A watch is armed before you turn to anything else.** A launch is not finished
   until its watch is up; the `Stop` hook `.claude/settings.json` registers refuses to
   end a turn while a run this session owns has nothing watching it, and the answer is
   to arm `just watch` on each run it names, never to answer the hook twice.
2. **The watch emits on every terminal state**, not just the happy path. Silence must
   never be indistinguishable from progress — a watch that greps only for success is
   silent through a crashloop.
3. **A foreground attach alone is not an armed watch.** It dies with the turn that
   started it; run the attached launch plus an independent watch.
4. **`just watch` is invoked directly, with no loop around it.** Every loop written here
   lost the invariant in a different way and no new loop inherits a fix for any of
   them, because the properties hold by construction inside the command and by
   somebody's memory anywhere else; where a watch would have to end on something the
   verb does not return on, report the missing condition rather than writing a loop.
   `--until` says what the wait is for: `settled` and `nothing-driving` are checked
   before any condition a caller named, cannot be skipped, and can each be named, while
   a waiting surface ends a wait only when `surface` — the default — was asked for, so
   name both when you want both endings. Re-arm from the `watch-cursor <cursor>` line,
   never from the sentence around it, and redirect a watch to a file or line-buffer the
   filter, because a block-buffering pipe makes a healthy quiet run and a dead one read
   alike.
5. **The unread-surface line is a HARD REQUIREMENT.** The `N planner update(s) waiting`
   line the views print per run has to reach you; filtering it out as noise is
   forbidden, because a blocking surface produces no other signal until it is read.
6. **A grep over the whole of `just status` is watching two subjects at once**: the
   run's own lines, then `oneagentgraph health`'s report about the **host**, whose words
   are the ones a real death is reported in. Cut at the boundary once, in one snapshot
   the whole watch reads —
   `just status "$RUN" 2>&1 | sed '/^  providers:/,$d'` — and the unread-surface line
   and the free-space reading both sit above it.
7. **A planning run is a dispatch.** Neither launch `just plan` makes attaches a
   monitor, because a planning run's plan is its own output, so each is owed the same
   watch, read to the end and re-armed from its cursor.

**Exactly one exception**: supervising several runs at once, which a per-run watch
cannot serve. An out-of-band poll that emits on state change is acceptable only while
it meets every property above, including 5, which is the one such a poll loses first.
Nothing else is an exception: "the loop I wrote works" is what every silent watch here
was.

### Answering on the channel

- **The channel is `onemessagebus` over `config/onemessagebus.yaml`**, the one place
  this host's messaging policy lives: change the policy there, never in a wrapper.
  `just channel-reply`, the ask `ORCHESTRATOR_ASK_MANAGER` names and the observer's
  judge side are that bus's verbs, so the rules below are the bus's.
- **Read the queue before replying**, and confirm the `pending` surface is the one
  being answered: a reply binds by correlation, never by arrival. A question carries
  the correlation the bus stamped on it, and `just channel-reply
  <run-id> --correlation <c>` answers that ask, while a reply naming none binds to the
  one pending ask and is refused naming them when none or several are. A correlation
  nothing pending holds — unknown, or already answered — is refused naming it, with
  nothing appended. A commands-only envelope answers nothing and leaves the ask
  pending. A blocking surface is handed out first, and reading past it leaves it
  pending.
- **`abandoned` marks a blocking surface nobody is waiting on *now***, never that the
  asker has gone — its next session takes its surfaces back — so read one as a finding
  to look at rather than a question to answer.
- **The verb's one line is a transport receipt** — `{answered, correlation, sent}`
  from `reply`, `{queue, position, id}` from `send` — not a reader acting on the ruling
  or the reconciler applying an edit, which the run records as `edit-committed` or
  `edit-rejected`. The asker's answer is the reply echoing its correlation and nothing else:
  a wait that elapses answers `timeout` at exit 1, never a ruling, so no token goes in
  your prose.
- **Task prose in an envelope is criteria, and is held to the criteria bar before any
  of it is sent** — the one validator `config/onemessagebus.yaml` declares on a reply
  carrying commands reads an `amend`, and the whole task an `add`, `retry` or `requeue`
  states, as the envelope states it and reads no run state, so a `requeue` folding bad
  fields onto a parked node is the engine's and the merge path's to refuse. The bus
  judges the whole offer before appending any of it, so a refusal refuses the whole
  envelope, and the escape it names is a `note`'s `text`, which touches no criterion
  and is never read (a note's `criterion` is). A novel whole task also spends one judged
  turn under the reviewer `just review-plan` uses, because nothing holds a pass for it;
  a correction to a node a review already cleared is deterministic only, because a
  model call there would make every mid-run correction wait and give you a reason to
  route around the guard. A turn that answers nothing is unjudged — `could not be
  judged`, never sent — and re-sent unchanged once the harness answers, never
  corrected. Only a pass is cached, by the bus, keyed on the envelope's bytes and the
  bar's fingerprint, so moving either tier of the bar invalidates every recorded pass.
  A reply accepted while nothing is driving the run is **queued**, not applied, until
  `just orchestrate --adopt <run-id>` attaches the driver that drains it.
- **Steer a running dispatch with a `note`, never with `oneagentgraph interrupt` by
  hand.** A note is journalled against the node; a raw interrupt reaches the worker's
  turn alone and leaves the run unable to explain why a worker changed direction. Ask
  for `persist: false` when the correction cannot wait, because it is refused rather
  than carried when no turn took it.
<!-- llmlint: ignore-block[agents_md_durable_and_terse] The 89/50/39 settlement count and the named journey are required content of the bullet below: they are the evidence a manager reads a hold against, and the adoption that paced the monitor holds this paragraph to carrying both. The block runs to the end of that bullet. -->
- **A quiet monitor is a working monitor.** It reports through the `finding` op and
  nothing else, so read an absence of surfaces as an absence of findings, and read the
  run's own state for whether anything is watching: `OBSERVER DEAD` is the window
  before the driver relaunches the observer and may clear on its own; `OBSERVER NOT
  RESTARTED` is the driver having given up, and is yours to act on. The monitor is a
  **scheduled foreground conversation**: `graphs/dag-scope.yaml` holds it between
  turns and declares it the member that keeps the observer graph alive, the pacemaker
  firing inside those holds — a declaration now, where this host's 89 recorded
  pacemaker settlements (50 beside a live conversation, 39 each taking a one-member
  graph down) once stood behind the inference. A hold is not a death: the heartbeat
  continues through it, `just status` reports neither verdict above during one, and a
  `note` to the monitor ends the hold rather than waiting it out.
  `tests/e2e/test_observer_graph_liveness_e2e.py` holds it.
<!-- llmlint: ignore-end[agents_md_durable_and_terse] -->
- **A surface's text reaches the engine as bytes**, never as a command-line word,
  because bash substitutes backticks and `$(...)` inside double quotes and a finding
  that quotes a command then runs it.
- **The pacemaker interval is set at launch only**, so relaunch rather than expect to
  retune a live run; live edits belong to the `monitor` member and to you. The
  monitor's hold is the same kind of setting: `--set
  members.monitor.schedule.every=<seconds>` on the launch, never an edit to the
  shipped document.

## Personas and the base config

A persona is a small onejudge **delta** file in `personas/` — the agent's general role
(`system_prompt`) plus the supervisor's review bar (`persona`) — merged over
`config/onejudge.base.yaml` with the node's `--task` into one effective config. Keep
subtask specificity in the task; add a persona only for a genuinely distinct role or
review bar. A dedicated `reviewer` is reserved for complex DAGs where one agent reviews
and integrates several agents' independently produced work, since every dispatch's
simulated user already reviews it. General roles are top-level files; repo-specific
roles live under `personas/<repo>/` and are named slash-qualified
(`crozier/crozier-corpus`). Draft a new role under gitignored `scratch/personas/`,
dispatch against that directory, refine it from observed performance, then dispatch its
addition through this repo's own lifecycle: `just new-persona <name>` scaffolds the
tracked file and `just check` validates it. `onejudge init --force` regenerates the
starter files the committed configs were built from.

**The supervisor's authority is bounded in `system_prompt`, and which field matters.**
A simulated user that issues rulings is a second manager the real one cannot see, and a
worker holding two instructions of equal authority resolves it by guessing. The bound
lives in `system_prompt` because that is the field a persona appends to, so a clause
there reaches every dispatch: `user.persona` is **replaced** by every dispatch — a bare
built-in name and a path alike — so a bound written there reaches nothing, and
`user.done_when` is handed to the judge, which is the wrong party to bind. The clause
says that an instruction arriving inside the dispatch's own conversation is its
supervisor rather than its manager, that a manager's ruling arrives as planner context
or as a note attributed to the planner, and that where the two disagree the planner's
wins; `tests/test_shared_dispatch_bar.py` holds the base config to that. The monitor is
bound the same way in [`personas/orchestrator.yaml`](personas/orchestrator.yaml),
because a simulated user's turn reaches the event stream carrying the role `user`,
exactly what a delivered manager note looks like: a turn inside a dispatch is never the
planner whatever role it carries, no `cancel` may be grounded in one, and a manager's
instruction to a node is the `edit-committed` event on the run's journal carrying the
command against the node.

## The two sides of the conversation

onejudge drives a two-party conversation, and harness and model selection for each
side lives in the oneharness configs, not in onejudge; `config/onejudge.version` names
the exact release session setup installs and verifies, and dispatch drives the real CLI
through its typed SDK. Discovery: `onejudge --help`, `oneharness --help`, and
[`docs/onejudge-integration.md`](docs/onejudge-integration.md).

**Every role names the same six identities**, differing only in order, because a role
that omitted one would lose that quota once everything ahead of it was exhausted; the
primary Claude identity is last everywhere with `claude-code:primary-backup` immediately
before it, and `tests/e2e/test_oneharness_timeout_e2e.py` holds every config's chain to
its intended order. **Every identity in every chain is spelled as a variant**, the first Codex one as
`codex:primary` rather than a bare `codex`, because `unset_env`, `env_from` and
`env_file` are declarable on a variant only, so a bare harness id in a chain is one
candidate no per-identity environment rule reaches — the `GH_PROJECTS_TOKEN` mask and
the `XDG_RUNTIME_DIR` repoint the `oneharness.*.toml` files declare, each explained
beside its rule. That variant deliberately declares no `unset_env` for `CODEX_HOME`,
which is ambient configuration a developer may export and this is the identity that
honours it; `tests/e2e/test_dispatch_environment_e2e.py` reads what a turn is handed
off the turn's own provider rather than off the configs. Each side's order is a
decision:

- **Agent side** — `oneharness.toml`: both alternate Claude subscriptions first, because
  the personas are tuned against that model tier, then codex.
  `scripts/oneharness-agent.sh` adds `--stream`, so the tool transcript reaches the
  views as it happens rather than after a turn that runs for many minutes.
- **Judge / simulated-user side** — `oneharness.judge.toml`: codex first, then the
  alternate Claude subscriptions on the cheaper supervisor model.
- **Monitor side** — `oneharness.orchestrator.toml`: both codex identities first, so
  this long-lived process does not queue ahead of the workers, and the one side with no
  per-turn deadline (`timeout = 0`), because it watches for the life of the run. Its
  judge side is the live manager, over `onemessagebus serve --codec onejudge`.
- **Pacemaker side** — `oneharness.check-in.toml`: the monitor's routing verbatim with a
  finite deadline, a separate file for that one reason — an unbounded deadline reaching
  a scheduled member leaves a wedged turn alive forever, silently. Re-merging the two
  is a regression.
- **Drafting side** — `oneharness.pr-author.toml`: no judge side (the review is a JSON
  schema), a finite deadline because it sits between a finished branch and its
  publication, and `stream = false`, forced because a structured answer is validated
  against the complete response.
- **Design-doc writing side** — `oneharness.design-doc.toml`: the reverse pairing.
  Codex writes, because this host prefers its Claude subscriptions on the side that
  judges whether the prose reads plainly to a non-specialist, and its judge —
  `oneharness.design-doc-judge.toml` — is the one supervisory side off the cheaper
  tier, because this review *is* the deliverable's quality bar.
- **LLM lint side** — `oneharness.llmlint.toml`, forced by
  `scripts/llmlint-oneharness.sh`.

Supervisory tiers reaching the workers' subscriptions is the operator's deliberate
trade — a tier that can still run beats one isolated from the quota that is left — so
do not reorder them back to isolation. **Streaming and turn control are independent**,
and a control failure is never a reason to set a member's `stream` off: that trades
away the per-turn visibility a manager supervises with and fixes nothing. Every
codex-first supervisory side here is a controlled turn, and it runs under the
`[harness.codex].model` its side's config names, refusing a thread the server would run
under another model as a classified failure the chain falls through at no cost; the
host-level `model` default in `~/.codex/config.toml` that stood in while the per-harness
model did not reach the server is host state to remove, because it cannot express the
per-side choice that key exists for. `tests/e2e/test_controlled_turn_model_e2e.py`
drives that turn through the pinned CLI, and `tests/test_linked_libraries.py` holds the
linked core at or past the release that carries it.

Pair the sides differently for **one** run with `--node-set
members.worker.agent.oneharness_config=REF` and the corresponding
`members.worker.judge.oneharness_config=REF` on `just orchestrate` (`--set` for a
dag-scope member), where each config declares the intended identity; never edit a config
concurrent runs also read. Choosing the identity chooses neither the **tier** — every
file pins a `model` per harness, which beats `ONEHARNESS_MODEL`, so override
`members.worker.agent.model` and `members.worker.judge.model` — nor the **deadline**,
which has no graph-native field: never point two members at one config to save a copy,
and never set `ONEHARNESS_TIMEOUT`, which is process-wide and moves every member at
once. `scripts/claude-alt-config-dir.sh` and `scripts/codex-alt-home.sh` are the one
source of the identities' directories, sourced by every wrapper and hook so no two drift.
The claude one exports all four Claude indirections — `ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR`
(`$HOME/.claude-alt`), `ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR` (`$HOME/.claude-alt2`),
`ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR` (`$HOME/.claude-primary-backup`) and
`ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR` (`$HOME/.claude`) — taking each from the
environment, then from the host's `ai-orchestrator/claude-identities.env` under its XDG
config home, outside every checkout, then from that default, so a host
whose `~/.claude` holds another account says so without editing a config every host
shares. The codex helper creates its directory because an empty codex home falls through
as `auth` while an absent one hard-fails, and the claude one creates nothing because
claude-code classifies an absent directory as it classifies an empty one.

Dispatch runs in **`bypass`** mode — no approvals, no inner sandbox — because the whole
environment is a container, and codex's own sandbox needs unprivileged user namespaces
this host disables; the `repo-write` allowlister hook stays wired by
`scripts/session-setup.sh` as a second line.

## Command surface

Use the `just` recipes (`just --list` is the index) and never hand-roll equivalents.
`just check` is the deterministic tier and `just gate` the complete pre-push bar. Every
stage that captures its output keeps it at `.logs/<label>.log`, gitignored and with
credential values redacted, so follow the innermost log and never read a live command
through `/proc`. There is no single-dispatch command: one subtask is a one-task project,
so nothing runs outside the ledger.

### The plan flow

`just plan <brief.md>` turns a manager-written brief into the project the planner
authors, launches it, and hands over to `just finish-plan` for everything after the plan
exists. Its decisions rather than its defaults: the persona is the **path**
`../personas/planner.yaml`, because a bare name resolves to a built-in role; the run id
it prints is guaranteed the run's own, so a detached planner's questions reach its own
channel; it names `--dag-graph off`, because a planning run's plan is its own output;
both of the flow's nodes are **direct** nodes in the launching checkout — the one flag
that changes the dispatched task — because no lifecycle shape fits a dispatched node
that commits nothing (`scripts/plan.sh`'s header holds the placement note and the
incident behind it); and it exports `ORCHESTRATOR_DISPATCH_APPENDIX_TEXT` holding
`config/dispatch-appendix.md`'s text rather than its path, because a planner plans
against other repositories too. That file is the operational notes every dispatched
task carries and the one statement of what a worker may suppress; this document states
none of it.

`just check-plan` reads a project against the bar each node will be judged against —
the engine's loader first, then `scripts/plan-check.sh`, each refusal naming its source
— and refuses the shapes that have each failed correct work on procedure: a criterion
naming a procedure instead of a property or resting on something outside the dispatch,
a task carrying no review record or not the appendix verbatim, a lifecycle `title` the
destination's own `commit-msg` hook would refuse, a `consumes` on a `local-direct`
identity. It also refuses the release-adoption shapes a matcher can see will never
complete — a `consumes` naming a target its producer does not resolve, a `published`
node with no target to wait on, a `fast` node behind a release publishing where no change
request opens, a node of this repository waiting on an artifact nothing here installs, a
node elsewhere taking this host's own `default_target` — while whether an adoption is
the *right* one, the goal needing it and the task naming the pin that governs the fix,
is `just review-plan`'s judged question and never this tier's. And it refuses a task
whose issue body, as `orchestrator/task_body.py` composes and measures it, would exceed
the limit GitHub puts on one, warning on stderr from the threshold that module declares,
because `just copy-plan` onto the `plans` board is otherwise the first thing to say so
and the last step of the flow. It is written to **miss** rather than to over-refuse, so a
sound node it refuses — a read-only research criterion
naming a path with a reading verb — is launched precise rather than softened; a node's
criteria are the one block its `## Acceptance criteria` heading opens.

`just review-plan` spends the judged turn that clears a plan's authored content against
`personas/planner.yaml`'s bar, and `just approve-design` records the user's approval of
the document a plan is read as, keyed on the document and on
`config/design-doc-template.md`. Both keep the same four properties: only a pass is
recorded, a record is authoritative, there is no escape hatch, and the key covers the
bar and every step's content as well as the prose, so moving any of those invalidates
the record while a settlement write-back does not. Three questions are the review's
rather than a matcher's: whether a number is the right number, whether the criteria
answer a demand their bar or their `## Additional info` makes, judged by meaning, and
whether a node whose criteria change no repository file declares `expects_no_diff`.
It also spends one plan-level turn on release adoption — whether a plan whose goal needs
a producer's change in force on this host carries a node of this repository adopting the
release, on the right pin, naming no version — once every task carries a record, and
records that pass on the project rather than on any task.
`just plan`'s own closeout records what its planner authored, so a planner-written plan
you did not touch costs nothing; the approval is not a second opinion on the plan, and
every launch is refused without it save the bounded planning exemption above.

### What every launch exports

Every launch hands the engine `--bus-config config/onemessagebus.yaml` and exports
`ORCHESTRATOR_ASK_MANAGER`, with the run id it asks on: `scripts/ask-manager.sh`, a shim
turning a question into one `onemessagebus ask` on that run's channel under this host's
reply window, and the one supported way a dispatched agent asks its manager a blocking
question. Its output is the bus's one-line answer, `reply` alone at exit 0, so an
elapsed wait reads as `timeout` and never as a ruling. The same verbs load this checkout's credentials from the gitignored
root `.env` through `scripts/credentials-env.sh`, never overriding a name the
environment already defines; so do the board recipes — `just plans`, `just check-plan`,
`just copy-plan`, `just approve-design` — through `scripts/plan-store.sh`, which names
that file when the store refuses for a credential this process does not hold, because
a board read needs the board's token where a run view needs nothing and loads nothing.
A dispatch is handed none of it: every `oneharness.*.toml` masks `GH_PROJECTS_TOKEN`
from its chain except the design-document pair, whose composed task reads the plan out
of the store, while the board nomination travels so a lane handed it and no token
skips and says why. That file wins over onetaskgraph's machine-wide `secrets.env` by
intent, and nothing detects the two drifting apart, so a value changed only in the
machine-wide file reads as lost. Its `GH_PROJECTS_*` names nominate the board
**onetaskgraph's own live test lane** writes to — that lane's concern, checked by
nothing here — while this host's board is `onetaskgraph.yaml`'s `plans` source.

### Supervising

`just runs`, `just status`, `just host`, `just results`, and `just transcript` are how
a run is read; never rebuild run state from `events.jsonl`, `ps`, or a clone's `git
log`. Read the **side** a failed node died on before its identity, because the chains
prefer different identities, and a `journal:` line as the one that makes the rest
unprovable. Two readings are this host's own (`scripts/supervision-readings.py`): free
space on every filesystem a run writes to, above the `providers:` cut so a watch still
sees it — read it, and leave acting on a filling disk to whoever owns what is filling
it — and every live `onemessagebus ask` and `onemessagebus serve`, bound to its run by
the channel its `--transport-dir` names, because a passing journey of this
repository's own suite stands up a real rendezvous, and answering that one by hand
puts a manager's verdict into a test. A run's journal holds a dispatch's own evidence —
every tool call and its output — beside the run and outliving the worktree, and `just
transcript` renders it: reach for it when a settled node's evidence seems missing from
its report. These views change no record the run keeps of itself; a reader writes only
the run's derived checkpoint cache.

`just sweep` removes only what no live process references, and passes a four-hour age
floor when you name none, because a host running several dispatches churns
workspaces hourly and almost nothing provably dead is ever a day old; lowering it
weakens no proof. Its trailer names every family neither verb it composes examined,
because `0 B reclaimed` beside an unexamined family reads as an all-clear. Session
setup runs it. The processes meant to outlive their launcher — the driver and the
dispatches and publications it forks — are the engines' to keep alive and reap: never
work around a kill with `nohup` or `setsid` by hand. A run root is pruned by the next
`onevcs session open`, which skips an open session's root and keeps a closed one only
while its clone holds an unpublished commit — so commit a stranded branch early — and a
spawn failure naming a missing `claude` may be a deleted run root: check the run root
before PATH.

`just smoke` spends one real agent-harness turn and is deliberately outside `just
gate`; the pre-push hook runs it only when the pushed diff touches the harness routing
files and scripts (`scripts/pre-push-smoke-needed.sh --print-paths` is the list), so
ordinary pushes consume no harness quota. A candidate that refused the turn with a
classified `quota` or `auth` failure is the chain working.

### The checks

A dispatched change is done when the checks that exercise it are green; the complete
bar is the pre-push hook's, on the push that publishes the branch. An agent iterates
with `just lint-llm-diff <base>` alone rather than paying for the whole gate per
finding. `llmlint.yml` is a deliverable only when a task names it; otherwise a worker
fixes the code and reports a rule that looks wrong or misapplied instead of editing it.

The judged tier is nondeterministic, so the run itself is cached and **only a green is
cached**, keyed on the whole workspace, the resolved base commit, and the judge's
fingerprint (`scripts/llmlint-fingerprint.sh`, taken through the environment the target
judges with); `just lint-llm-diff <base> --skip-nx-cache` is the only re-judge lever.
The comparison base belongs to the **workstream**, not to one dispatch —
`ONEVCS_COMPARISON_REMOTE` / `ONEVCS_COMPARISON_BASE`, exported to every dispatch and
every publishing push — so the `pre-push` hook replays what the worker cleared instead
of re-rolling against findings it never saw; a base its own origin ref has moved past
is refused rather than judged, because a stale name still yields a valid verdict over
commits the branch does not carry.

**A memo may stand in for a verdict only when its key covers everything the check
reads**: each test tier is keyed on what its tests read — an undeclared read of this
repository's prose fails in `tests/conftest.py` — a tier whose subject lives outside
the workspace is uncached, and narrowing a key past what a tier reads makes a
green suite a claim about a tree nobody ran — force one tier with `--skip-nx-cache` on
that invocation instead. Both Nx caches are origin-keyed under
`${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/` through `scripts/nx.sh`, which also
heals a worktree's `node_modules`, `.venv` and plan-store CLI from the locked installs
only (off under `UV_NO_SYNC`), because a missing `.venv` does not fail — its readers
fall through to whatever other checkout is on PATH.

## Dogfooding rule

Use the orchestrator harness for **all tasks of sufficient complexity**, in any
repository. A qualified onetaskgraph project launched by `just orchestrate` is the only
way to dispatch: one subtask is a project holding one task, a larger task the same
project with more, and nothing about plan schema, personas, or node semantics changes
with the count, so a one-node run still gets a journal, an ownership row, planner
surfaces, and a place in the DAG UI. Dispatch smaller project work with a single-node
plan rather than doing it directly; only the direct-tweak exception above applies.

**Self-dispatch rule (this repo).** Never author working-tree changes in the shared
canonical checkout: concurrent orchestrators use it and direct edits race them. Every
change — plans, personas, docs, `AGENTS.md` — is dispatched into an isolated worktree
cut from the registered `ai-orchestrator-isolated` safety clone, with the canonical
checkout as the node's publication repository, only fast-forwarded after integration.
A node names this repository once, in its task record's own `repositories` as the
normalized origin `github.com/nickderobertis/ai-orchestrator` — never by alias on
`onepipeline.repo` — and sets `execution_checkout` on each lifecycle node to the
registered alias; `tests/test_registry_resolution.py` holds the resolution to the
canonical checkout. Confirm `git config core.bare` is `false` before trusting a
self-dispatch result.

## Stack and composition

A configuration layer over published CLIs, kept as an Nx workspace for its computation
cache and uniform target set — closest to a skills-repo shape (determinism-vs-judgment
split, validate-in-gate, narrow allowlist), applied to onejudge configs and personas.
Bash for the recipes' wrappers, the harness routing, and provisioning; Python (uv,
ruff, mypy, pytest) for the small `orchestrator/` package — the label contract, the
redaction rule, and the suite that proves this layer; YAML, JSON, and TOML for
configs. The engine, the lifecycle, and the browser view are not built here.

**CI and branch protection are deliberately deferred**: this is a local
proof-of-concept, and `just gate` at pre-push is the enforcement point — add
`.github/workflows/` mirroring it when this graduates. Nothing versioned is published,
so there is no release tooling, and the committed Bun and uv lockfiles make the
workspace reproducible without asdf or direnv. `.github/CODEOWNERS` is not one of the
deferrals: it declares who owns what rather than enforcing anything — GitHub turns it
into required review only under the branch protection that *is* deferred — so it
enables nothing today and becomes enforceable on its own the day that deferral ends.
The `llmlint` judged tier is composed beside the deterministic gate and enforced at
pre-push because there is no CI.

## Invariants (non-negotiable)

- **The gate is strict**: format check, lint, type check, and tests all fail on issues
  — no warnings-only mode.
- **Coverage is enforced at 100% line coverage on `orchestrator/`**, because what is
  left of the package is all trust boundary. `[tool.coverage.report]` in
  `pyproject.toml` is the floor's one source, and the uncached `orchestrator:coverage`
  read compares what `orchestrator:test` measured to it, so nothing else may name one.
- **The suite runs across four xdist workers**, from measurement rather than `auto`:
  it is latency-bound and the curve is flat past four on a host also running
  dispatches. A test whose subject is a process- or machine-wide resource declares
  that as a scheduling constraint — never a loosened assertion, and never a solo
  re-proof by hand. The resource that has cost journeys is `uv`'s exclusive lock on
  this checkout's `.venv`, which every recipe waits on through `uv run`;
  `SHARED_TOOLCHAIN_GROUP` in `tests/e2e/nx_workspace.py` is its constraint, and one
  name is the whole mechanism, because `--dist loadgroup` co-locates only tests sharing
  a name; a module that launches a run is recognised from its syntax rather than
  listed, because a body that looks inert can take a fixture that spends a launch. Read
  a wait that *expires* as something holding a lock and a wait that merely lengthens as
  load.
- **Tests are realistic, not mocked.** The suite drives the real `just` recipes, the
  real wrapper scripts, the real `oneharness` CLI, and real Nx; only the paid model and
  the published CLIs a recipe delegates to are doubled, at that boundary and nothing
  above it, because each engine is proven in its own repository and a real
  `onepipeline start` here would launch agents. Never double a recipe, a wrapper
  script, or the shell they run in.
- Validate external inputs at trust boundaries: a persona before dispatch (`just
  validate-personas`), and the label contract in `orchestrator/labels.py` before it can
  reach a subprocess.
- No secrets or credentials in the tree: harness credentials live in the environment,
  referenced by name, and the allowlist in `.claude/settings.json` stays narrow.

## Tests are context engineering

This repo runs on agents, so the suite is the only QA loop. `tests/e2e/` proves the
real journeys against the real boundaries; `tests/` covers what this layer decides on
its own and the drift gates that reconcile this document's claims against something
real. A recipe is not done until a journey drives it end to end.

**A date in this document names the test that re-takes it, or says it is history.** A
dated measurement of somebody else's software reads as current for as long as it
stands, so every ISO date here is a claim about an external tool until its paragraph
names a test under `tests/` that fails when the release behind it moves, or classifies
the date as stamping something that *happened* with `<!-- dated-claim: incident
<reason> -->`. `tests/test_dated_claims.py` triggers on the bare date rather than on
how the sentence is written, because there is no closed vocabulary for asserting
something. A claim with neither goes, and what may stand in its place is what a
release did, cited to something a reader can open.

## Commits and merging

Squash-merge via PR; PRs follow `.github/pull_request_template.md`. With no CI, the
**pre-push hook** (`.githooks/pre-push`, activated by `just bootstrap`) runs `just
gate`, so nothing reaches the remote unproven, and every dispatched agent clears its
own findings before committing. Local-first is not local-only: keep the registered base
in sync with its origin and push every change that reaches it immediately (`just sync`
fast-forwards a publication checkout). Never force-push or rewrite history on the
registered base. Keep the `.claude/settings.json` allowlist current with routine
commands rather than re-approving them each session.

`core.hooksPath` activates the whole directory. **`post-checkout`** marks whatever git
checked out trusted for every claude-code identity a dispatch can run as: claude-code
keys trust on the exact project path, so an untrusted dispatch discards its allowlist
and blocks on an approval that cannot arrive, which reads from outside as an identity
with quota left doing nothing. It never fails and never speaks, because a non-zero
`post-checkout` would break every `git worktree add` here. **`commit-msg`** holds the
subject to a Conventional Commit of a type this repository releases from (`feat`,
`fix`, `perf`, or a `!` breaking type) within onevcs's publication limit, because every
commit here changes the deliverable and a `docs:` change merges green and never cuts a
release. It reads the subject alone — no index, diff, or branch — because the
publication path puts its composed subject to the same hook; git's generated subjects,
autosquash markers, and the `(incomplete step)` marker and its attestation are exempt.

Every path that advances the base — publication, `repo-recover`, and the `integrate`
train — leaves **one** commit on it. The `(incomplete step)` marker and its attestation
are branch state, recorded on the base only as `Orchestrator-` trailers on the squash
commit (`config/onevcs.rules.yml`'s `trailer_prefix` is their one source, and a marker
under another prefix is refused publication rather than read). Provenance commits
already on `main` stay where they are: base history is never rewritten.

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time (a
real e2e for any journey a bug slipped through, a script for a step you did by hand, a
terse note here for what the code doesn't show); (2) keep the codebase and environment
clean and reproducible. Fold either in when it's the lowest-error path to the ask;
otherwise draft it with `just follow-up <run-id>` against the run it came out of. Skip busywork.
