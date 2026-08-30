<!-- llmlint: ignore-file[determinism_vs_judgment] Repo discovery requires judgment across existing interfaces. -->
<!-- llmlint: ignore-file[no_redundant_instruction_pointers] The manager/monitor split requires a direct pointer to its live-channel operating contract. -->
<!-- llmlint: ignore-file[agents_md_durable_and_terse] Human-node eligibility and the watch invariant are durable manager judgment and need concrete guidance here. -->
<!-- llmlint: ignore-file[contracts_have_one_source_or_a_drift_gate] Human-node misuse is intentionally rejected at planning, node-definition, and reviewer boundaries. -->

# AGENTS.md

Durable instructions for the **manager** and any agent working in this repo.
Write for a future maintainer, not as a session log. The deterministic steps are
the `just` recipes, each a thin wrapper over one of the published CLIs this
repository configures; this file holds the judgment.

> `CLAUDE.md` is a symlink to this file — edit `AGENTS.md` only.

## What this repo is

A local **orchestration harness**: you (the manager) take one large task, dispatch
a planner to split it into a dependency graph of smaller tasks, and supervise its
execution. `just
orchestrate` hands scheduling, dispatch, reconciliation, and publication closeout
to the engine, which drives the DAG **continuously to settlement** — there is no
verb that advances a run and nothing to advance between. It also attaches
`graphs/dag-scope.yaml`: an **active monitor** using
[`personas/orchestrator.yaml`](personas/orchestrator.yaml), which drives nothing
and instead watches the detailed activity stream, compares it against the goal and
each node's task, and raises what it finds over the channel described in
[`docs/orchestration.md`](docs/orchestration.md#the-planner-channel). Worker
onejudge processes still run under simulated-user supervisors. The deliverable is
this setup itself — config, personas, scripts, docs — not a shipped binary.

Beyond dispatching at a directory, the harness manages a change's **full
life cycle** against any repo (GitHub or a local path): resolve its normalized
origin to one **repository identity**, choose a registered publication checkout,
do the work in an **isolated worktree cut from a per-run clone of an execution
checkout**, let the repo's own merge path verify it, and merge it. That per-run clone
shares the execution checkout's object store and is what keeps concurrent
orchestrators from racing one worktree registry. Checkout aliases share one identity,
and its `workflow` and `repo_type` (`single-owner` or `team`) come from the **rules
file** the identity matches rather than from anything stored per identity. This host's
copy of it is tracked as `config/onevcs.rules.yml` beside the
checkout list `config/onevcs.checkouts`, and `just repos-apply` installs both —
idempotently, so it is re-run after an edit rather than migrated. Editing that file
is how routing changes, and `onevcs rules check <repo>` is what shows the resolved
policy an identity ends up with. `just repos` only lists registered identities and
checkouts: the `workflow`, `repo_type`, and `gate` it also prints are `onevcs
register`'s derivation from the origin and the checkout — no flag sets them, and
none of them is the routing.

**Nothing this host configures runs a gate any more, and that is the point.** The
rules file carried a third field until **onevcs 0.11.0 removed the concept**: a
`gate:` named a command `onevcs` ran in the branch's own clone before it would
publish. It was a verifier beside the real one that threw its answer away — for this
host's one locally-published repository it was literally the work the `pre-push` hook
then does again, and for every other one it front-ran CI and discarded the verdict.
Where it ran *less* than the merge path did it was worse than nothing, because it read
as verification: a `nick-derobertis-site` branch passed it, published as PR #77, and
sat blocked on a required check that gate never ran. At rules `version: 3` a `gate:`
anywhere is refused by name; at `1` and `2` it is accepted, ignored, and reported once
on stderr. What verifies a change now is the repository's own merge path — the host's
required checks for a remote identity, the `pre-push` hook for a local one — and
`onevcs` **detects** which rather than being told: `store::merge_path_coverage`
answers `PrePushHook(path)`, `RequiredChecks`, or `None`, and `onevcs register`, `just
repos --audit-gate-coverage`, and `just integrate` all read that one answer.

But **"unsettable" is not "unread"**, and the stored `gate` — a different thing from
the rules gate, and the one that survives — is read where it matters least obviously:
`onevcs recover`'s `attests_nothing` (`crates/onevcs/src/recover.rs`) refuses a
recovery outright when the stored identity gate is `store::NOOP_GATE` **and**
`merge_path_coverage` is `None`, because an attestation with nothing behind it attests
nothing. `repo_type` and `workflow` decide which verb that file's refusals hand an
operator: `complete_branch_verb` reads both to offer `publish-branch` for a team or
remote identity and `integrate` for the rest.
Team repositories default to an ordinary ready-for-review
open PR; explicit `change-auto` or `change-direct` merges their remote PR.
Single-owner repositories preserve local direct or remote auto behavior, while
explicit `change-open` forces remote open-PR publication for that run without
changing stored local workflow. Team identities cannot use local workflow or
direct integration. Those four names — `local-direct`, `change-open`,
`change-auto`, `change-direct` — are the published `merge_policy` vocabulary; the
older `direct` / `none` / `auto` spellings are refused by name at launch.
**No operation that changes what a remote or a base branch sees may bypass
`onevcs`.** Pushing, merging into a base, and opening a change request go through
`publish` / `publish-branch` / `recover` / `integrate`. Local commits, merges into
your own working branch, and every read are the agent's own git to do. The line is
drawn there rather than at "never use `git`" because local authoring *is* git: a
rule forbidding that is one every agent has to break, and a rule everyone breaks
routes nothing. Every branch state has a verb, so none of them is a reason to reach
for raw `git push` or `gh`. Three land a branch, and which one is decided by what
the branch *is*: `just publish-branch` verifies and publishes a **complete
unpublished branch no session holds**, under the policy its identity's rules
resolve; `just repo-recover` is for a branch carrying **unattested incomplete
provenance**, and is the only verb that knows how to attest that marker — do not
bypass one with a normal commit; `just integrate` is the **local merge train**,
merging named finished branches into their base and opening no change request.
`just recoverable` names which of the three a given branch needs, and prints each
resume command in its `just` form — the raw `onevcs publish-branch …` line that
listing renders reaches the verb below the wrapper that drafts, so a copy-pasted one
opens its change request with an empty description. **The two verbs that open a
change request give it a body**, drafted from the branch's own diff before the verb
runs: onevcs 0.7.0 took `--body <TEXT>` / `--body-file <PATH>` on `publish-branch`
and `recover`, and until this repository passed them, every branch landed by hand
opened with an empty description — and those are the branches whose context is least
recoverable from the diff. See the drafting paragraph below for the two escapes and
why the turn is spent before the publication. `integrate` takes neither, because the local
train opens no change request to describe. Two more verbs
answer the questions those three raise. `just work-status <ref>` — a change
request's URL, a session token, a branch name, or a commit — reports everything
`onevcs` knows about one piece of work, and is how a planner or a worker asks what
became of it instead of inferring it from a run. `just import-branch <branch>
--repo <checkout>` makes a branch reachable from an identity's registered
checkouts, which is what the landing verbs need: they read the branch from the
**publication checkout**, never from wherever a session happens to be working, so a
branch finished in a session worktree or a run clone is refused as being in none of
the identity's checkouts until it is imported. Improvising past a missing verb is
what puts a change on a base branch without its merge path ever ruling on it, which is
the failure this routing exists to prevent; the table is
[in the lifecycle doc](docs/repo-lifecycle.md#which-verb-lands-which-branch-state).
The selected
publication checkout is **never worked in directly and only ever fast-forwarded**
after a merge lands; it must be clean with the selected root checked out before
dispatch. So `no-changes` from a node whose task was to change code means **look
for the work elsewhere** before it means there was none: check that checkout's
branches and its `main` against `origin/main`, and the repo's open PRs. **Read
every settled state that way, not only that one outcome.** A node that settles
`failed` may still have published a change: a dispatch that ran `onevcs publish` in
its own final turn settles `task-failed-change-open` carrying the URL, and
re-running that work would duplicate a change already waiting to be read. **Five
more failure words each say the publication reached the merge path and got no
verdict it could act on**, and they arrive already retried: `checks-failed`,
`checks-unsettled`, `push-rejected`, `sync-conflict`, and `pushed-unverified` are the
endings a further attempt could answer, so the engine dispatches the node again onto
the *same* branch with the failure's reason and `onevcs`'s own evidence — three
attempts by default — and settles only when that budget is spent. Read one as "this
branch exists, carries the tree the merge path would not pass, and has already been
worked three times", never as a node to `retry` blind: a retry naming no branch would
cut a fresh one beside committed work, and the thing that refused it is still there.
**`pushed-unverified` is the one of the five that is not a refusal at all**, and
reading it as one is expensive: onevcs 0.12.0 added it for a publishing push that
**reached the remote** and a merge path that could not then be read, so the work is
already on the origin and its reason names both the commit it landed at and what
stopped the read. Every other word on that list describes work that did not land.
This one describes work that did, with the verdict on it still outstanding — so the
move is to read the change request on the host, never to publish the branch again. `just work-status` on the branch, and the evidence
the detail points at, are what say whether the next move is a person's. And a
node's **landing is dated to the moment it settled** — nothing in the run re-reads
it, so `just results` and `just status` render a change that merged an hour later
as not landed *as of that settlement*. The re-read is `just work-status
<change-url|session|branch|commit>`, and it is the only thing that answers where a
piece of work is now — but **read its `decided by:` line before its `landed:`
line**, because only one tier of the four is a record. `a recorded landing`, `the
change request's number in the base`, and `a landing trailer on the base` each name
the commit that is their evidence and each answers `yes`; `content comparison` is
the only tier that answers `no` or `unknown`, and it is a comparison rather than a
record. What it compares is the paths the branch touched rather than
the whole tree, so unrelated work landing on the base beside it does *not* move the
answer — but it is still a comparison and never a `yes`, because publication squashes
and no patch id survives that.

**Which of the four tiers a landing can reach at all is decided by its publication
workflow, and that is the half of this advice a reader most needs and the document
longest left out.** `onevcs` stamps the commit *it* writes, and each workflow only
writes one of the two. A `local-direct` publication builds the base's squash commit
itself, so the base carries `Orchestrator-Landed-Commit: <the branch's tip>` under the
`trailer_prefix` `config/onevcs.rules.yml` sets — and that is `a landing trailer on the
base`, a record kept in the repository's own history, which outlives everything this
host stores. A remote publication writes no such commit: the host writes it, GitHub's
squash carries no trailer, and `onevcs` records the landing the only place it still
writes — as a `chore: record the landing of <branch>` commit **on the branch**, whose
trailer names the *base* commit instead. Nothing reads that one back, because the
trailer tier looks at the base. So a remote landing's only records are `a recorded
landing` and `the change request's number in the base`, and **neither of those survives
the loss of this host's state**: the first *is* that state, and the second needs a
change request `onevcs` holds a record of before it can look for its number, so under
`change request: none recorded` the `(#51)` sitting in the base's own subject goes
unread. Lose the record and a remote landing has
nothing left but `content comparison`, where a `local-direct` landing still has its
trailer. Read `decided by:` before `landed:` for either — and for a remote branch read
it knowing that `content comparison` there is what a lost record looks like, not what
an unlanded branch looks like.

**Re-measured 2026-08-29 on the pinned onevcs 0.16.2, over two landings held identical
but for the workflow their identity resolves.** Asked in this host's own state root
both answer `landed: yes`, `decided by: a recorded landing`, and `just recoverable`
offers neither: the `local-direct` `onevcs/s-60938a07cb2e`, which landed on
`ai-orchestrator`'s `main` as squash commit `5e723477` carrying
`Orchestrator-Landed-Commit: 54475abf…` — that branch's own tip — and the `change-auto`
`onevcs/s-14e04d0b57e2`, which merged as
https://github.com/nickderobertis/onepipeline/pull/156 at `e1d73716`. Asked against a
throwaway `ONEVCS_HOME` holding this repository's own rules file, a checkout of each
identity, and **no session records**, they part: the `local-direct` branch answers
`landed: yes`, `decided by: a landing trailer on the base (5e723477…)`, and `recoverable
--all` marks it landed with *"Nothing to resume"*, while the `change-auto` branch —
whose own tip is `chore: record the landing of onevcs/s-14e04d0b57e2` carrying
`Orchestrator-Landed-Commit: e1d73716…` — answers `landed: no`, `decided by:
content comparison`, `change request: none recorded`, and `just recoverable` lists it
again with a paste-ready `publish-branch` command beside it, reporting *"no session
record names this branch"* while `(#156)` sits in that base commit's own subject and the
trailer sits on the branch it was asked about. Rewrite that rules file's
`trailer_prefix` and the `local-direct` branch falls to `content comparison` too — it
answers `landed: unknown` there — which is what says the trailer tier is read under this
host's configured prefix rather than under a name `onevcs` knows by itself.
**That remote answer moved between adoptions and moved the wrong way**: taken over a
different pair on onevcs 0.15.4 the same question answered `landed: unknown`, and `no`
is the dangerous one — it closes the question rather than leaving it open.
`tests/test_phase_and_landing_guidance.py` re-takes this: it holds the stamp, the two
refs, the condition, and both quoted answers to the release this checkout pins, so the
day that pin moves the check fails and the measurement comes due rather than standing
here describing a build nobody runs.

**That inverts what this passage was asked to record, and the inversion is the useful
part.** The correction was briefed from a manager's reading in which the `local-direct`
branch answered `landed: no` by `content comparison` while the trailer sat on the base,
and the remote one answered `a recorded landing` — so the lesson would have been that
`local-direct` is the weak side. Every one of those four answers was re-taken here on
2026-08-29 and the `local-direct` half no longer reproduces at all: with the session
record present it answers `a recorded landing`, with the record gone it answers the
trailer, and it is offered by `recoverable` in neither state. What does reproduce is
the trailer on `5e723477` and the remote landing's `a recorded landing`. The weak side is
the remote one, for the reason above — its evidence is all in this host's state root —
and this is stated rather than quietly amended because the earlier reading is the one a
manager would otherwise re-derive from the same symptom.
The same check re-takes this half, because it is the same re-take: what
`tests/test_phase_and_landing_guidance.py` holds is the paragraph above, and the
correction only survives while that paragraph is the one being re-measured.

**That warning has two halves, and onevcs 0.14.0 moved exactly one of them.**

*The squash-merge half stands untouched in substance, and this is the sentence to read
before trusting a blob hash.* `crates/onevcs/src/landed.rs` was one blob — `f8529d72` — at
v0.11.0, v0.13.0 and v0.14.0, and it is **not** that blob at the releases since: `1b86bb66`
at v0.15.4 and `ec3bea7c` at v0.15.8 and at the pinned v0.16.2. Re-read at v0.16.2 on 2026-08-29, the four
tiers, their order, and its own rule that the last one *"must never answer `yes` — it is a
comparison, not a record"* are nonetheless exactly what they were, so what moved is a
constraint added beside them rather than any of the above. The addition is worth knowing
because it narrows the very hazard this passage is about: tier 4 must now also never answer
`no` from a base history that stops short of the base this host knows, since a checkout that
has not fetched since before a landing scans a history with the evidence cut off — so it is
asked through the object store of the checkout every publication fast-forwards, and answers
`unknown` when even that leaves it behind. The dangerous `no` is therefore rarer than it was;
it is not gone, and nothing below is relaxed on the strength of it. Publication squashes and a landed branch is
afterwards an ancestor of nothing, so `unknown` still means undecidable from history:
it is what a branch that landed with no change request and not through this crate
leaves behind, and equally what somebody else making the same change leaves behind.
Do not try to settle it with `git diff main...branch` either — that measures from the
fork point, so a landed squash-merged branch still reports its full insertion count
and reads as proof the work is missing. Only the files' presence on the base, or the
squash commit's own stat, answers.
`tests/test_phase_and_landing_guidance.py` re-takes it, holding the blob at the pinned
release and the never-yes rule together, so a `landed.rs` that moved again is a failing
check rather than a paragraph quietly describing an older one.

*The retry half is fixed, and only forward.* onevcs 0.14.0
(https://github.com/nickderobertis/onevcs/pull/80) links each session of a branch to
the one that continued it: `workspace::supersede` writes a `retried_by` token onto the
**older** record when a new session takes over a branch name, and `status::answering`
follows every chain to *"the newest session of the chain, whose evidence is the
branch's"*, marks the rest superseded, and stops a superseded run clone from deciding
the branch. A chain it cannot follow — a hop naming no record, an edge across
identities, a cycle — stops rather than falling back to the last record that read, so
a superseded session answers in neither direction. That is the whole of the failure
this passage was written from: two copies under one name is what a **retry** leaves.

**But that link is written at `session open`, so it exists only for sessions opened
from this adoption forward.** Re-measured 2026-08-29 on the pinned onevcs 0.16.2:
`just work-status onevcs/s-107f9b4639a1` — a branch two session records share the
name of, whose change request https://github.com/nickderobertis/onepipeline/pull/142
merged the day before as `eedac38 fix(views): say a judge rejected the work … (#142)` —
still answers `landed: no`, `decided
by: content comparison`, and offers `publish-branch` as the next step, with that squash
commit sitting in the base's own history after this branch's own fork point.
`tests/test_phase_and_landing_guidance.py` re-takes this one too, holding the ref, its
change request, and the answer to the release this checkout pins.

**Which tier decides is not the same for every branch, and that is the reading to
carry.** Asked of `onevcs/s-a37f615ff961`, whose change request
https://github.com/nickderobertis/onetaskgraph/pull/15 merged as `63ce4f83`, onevcs
0.15.8 answered `landed: yes`, `decided by: the change request's number in the base
(63ce4f8330ab…)` where 0.15.4 had answered `no` by comparison — a real improvement, and
the one that most reads like the hazard being fixed. It is not: that ref cannot be asked
on this host at all any more, since no checkout of its identity is registered here, while
the ref above answers `no` on 0.15.8 and 0.16.2 alike with its own change request's
squash sitting in the base. The dangerous answer is rarer and is not gone.

**What has changed since the previous adoption is that the retry chain now exists
here.** 56 of the 461 session records under
`$ONEVCS_HOME/sessions/` on this host carry a `retried_by`, this branch's older record
among them — `s-107f9b4639a1` names `s-5e2b616a9729` as the session that continued it,
and `status` answers from that newer one — so the chain is followed and the tier beneath
it still says `no`. So wherever a landing this host holds no record of is in question,
read the old
warning unchanged: treat **both** of that tier's answers as unknown and confirm against
the change request before acting, because `no` is the dangerous one — it closes the
question and invites re-dispatching work that already merged. Where a record does
exist, the chain is what lets `decided by:` name it instead, which is why that line
is still read before `landed:` rather than instead of it.

A worker
dispatched without a worktree does the work in whatever checkout it can see, and
the empty session branch the node watched is then a truthful report about the
wrong directory — settle it only once you have looked. But a worker dispatched
**with** one starts there, and that is now measured rather than assumed: a
lifecycle dispatch against onepipeline 0.3.1 reported `pwd` as
`/home/nick.guest/.onevcs/workspaces/github.com-nickderobertis-ai-orchestrator-c2fddf4e28b4/runs/s-cec0174198d8/worktree`
as its first action, which is the directory `onevcs` recorded cutting for that
node's branch. So a task need not tell a lifecycle worker where to commit; that
paragraph is redundant and a planner may drop it from its templates without
re-deriving this. `tests/e2e/test_worker_start_directory_e2e.py` is what keeps it
answered — it launches a lifecycle node for real and reads the directory out of
the run's own journal. **Redundant is the mild half of that.** A brief that says
anything about where the work goes is one wording slip from telling the worker to
leave the branch it was given, and the worker obeys: a task that read *"Work from
`main` … do not resume onto `<branch>`"* — meaning only "ignore the stranded
commits" — was read as the instruction it looks like. The dispatch cut a fresh
branch from `origin/main`, committed there, and reported the commit and a green
gate. The session branch stayed empty, the node settled `done (no-changes)`, the
worktree was reaped, and that object does not exist today: roughly thirty minutes
of correct, gate-green work gone, reported as though there had been none to do. So
a lifecycle task says **commit on the branch the session opened**, or says nothing
at all about it — never a phrase that could be read as an instruction to cut one,
and never a base branch by name. Say what to *ignore* in terms of the content to
leave behind. The same rule is stated where briefs are written, in
[`personas/planner.yaml`](personas/planner.yaml).

**The destroying half of that incident is now caught, and the rule stands
regardless.** onevcs 0.11.1 made session close look for commits the clone holds that
the session's branch does not: a worker that cut `my-own-fix` from `origin/main` inside
the session worktree and committed there has its close **refused** — *its worktree …
holds work its branch "onevcs/s-…" does not carry — 1 commit on "my-own-fix" — and
removing the worktree is what would have made it unreachable* — with the commit copied
into the execution checkout first and `onevcs recoverable` named as the way to land it.
onevcs 0.11.0 reported `closed`, removed the worktree, and copied nothing. That is
written as what those two releases do rather than as a dated measurement of the pin this
host runs, because **nothing here re-takes it** and the rule below is that a dated claim
about an external tool names the check that re-takes it or goes. Should you take it
again, take it under a throwaway `ONEVCS_HOME`: `session open`'s first act is reclaiming
run roots under the identity's workspace, so a measurement against the real state root
races the live dispatches on it. So the work survives now; what does not
change is the brief, because the refusal only catches a worker that *committed*, and
because a task that sends a worker off its branch has already produced the wrong tree
whether or not the object survives.
**That worktree belongs to the dispatch, not to the node or the branch**: every
dispatch cuts its own, so a retry, a requeue, or a resumed pin runs in a
*different* directory from the one before it. The live directory is
whatever the dispatch's own record names — the `session-opened` the run's journal
carries for it, or the `worktree:` line `just work-status` renders for that
session — never a path remembered from an earlier dispatch, which is how a
supervisor came to read a stale tree and report no progress while the dispatch was
committing. onevcs 0.8.0 and later take up the session a stopped run left on a
pinned branch instead of cutting a second one on the same name, and **continue** a
pinned branch nothing holds — opening the worktree at that branch's tip and
merging the base into it — rather than refusing the pin. Both are what let a retry
reach the work its predecessor stranded. **What carries that fix into a
plan node is the adopted onepipeline 0.17.5**, never `config/onevcs.version`:
onepipeline links onevcs, oneagentgraph, and onejudge as Rust libraries, so a
dispatch runs the copy that release resolved, while `config/onevcs.version` pins
the onevcs *CLI* the manager verbs run — `publish-branch`, `recoverable`,
`work-status`, `integrate` — and moving it moves nothing a dispatch does. **For
anything onepipeline links, the adopted CLI version is not the version in force**,
and **when a fix lives in something onepipeline links, the pin to move is
`config/onepipeline.version`**.

**The deciding artifact is the installed binary, and measuring it is the answer.**
The `onepipeline-cli` wheel this host installed ships its own built `onepipeline`,
and that binary carries the registry path of every crate it was compiled against:

```sh
strings -a "$(readlink -f "$(command -v onepipeline)")" \
  | grep -oE '(onevcs|oneagentgraph|onejudge|oneharness[a-z-]*)-[0-9]+\.[0-9]+\.[0-9]+' \
  | sort -u
```

On the adopted release that answers `oneagentgraph-0.3.13`, `oneharness-core-0.12.1`,
`onejudge-0.6.2`, and `onevcs-0.16.2` — **one** line for `oneharness-core`, where every
adoption up to the one on 2026-08-25 answered two, which is the answer no pin in
`config/` can give and still the reason the table above sends `oneharness` to a
different gate. A
second published source says the same without `strings`, without a network and
without a clone: the same wheel ships a CycloneDX SBOM under its `dist-info/sboms/`
declaring one version per linked crate, and `tests/test_linked_libraries.py` — whose
docstring records where that lives and why — reads it on every gate run to hold this
repository's prose to it. Both were re-taken on this host's installed artifacts on
2026-08-29 and agree line for line.

**Read the locks after that measurement, and in this order.** `git show
v0.17.5:Cargo.lock`, at the tag of the release actually *installed*, is
corroboration that should agree: onepipeline's requirement is
`onevcs = "0.16.2"` at v0.17.5 and its lock still resolves onevcs 0.16.2.
**At this tag the requirement and the lock can no longer be told apart, and that is the
state to read most carefully.** All three reconciled requirements — `onevcs = "0.16.2"`,
`oneagentgraph = "0.3.13"`, `onejudge = "0.6.2"` — name the exact release their lock
resolves, so nothing at this tag says which of the two constrained the resolution, and a
reader who checked only one of them would be right by luck rather than by evidence.
The previous cycle is kept here because it is what that distinction looks like when it is
still visible: at v0.16.3 the *requirement* decided for `onevcs = "0.15"`, since pre-1.0
the minor is the breaking position and `"0.15"` admits no 0.14.x at all, while
`oneagentgraph = "0.3.11"` and `onejudge = "0.5.4"` were caret requirements admitting
every later 0.3.x and 0.5.x, so the lock resolving exactly those two was the lock's doing
and not the declaration's. Which of the two constrains changes per bump, their agreement
proves nothing about this host on its own, and a bump that collapses the distinction —
this one, and the one before it — removes the only signal that told them apart, which is the reason the
installed binary is measured first rather than last. The fourth requirement is the one
that moved an earlier adoption: `oneharness-core = "0.8"` at v0.14.0 became `"0.12"` at
v0.14.2, which is the whole mechanism behind the two-version split collapsing —
onepipeline's own workspace was holding a core four minors behind the one its dependents
resolved — and it is still `"0.12"` at v0.17.5.
`origin/main`'s
lock answers a different question — what the *next* release would link — and is
evidence about this host only by coincidence. The ordering matters because a lock
describes what a release *would* link while a host runs what it *installed*, and
the two part company exactly when something was rebuilt, repaired, or installed
from somewhere else, which is the case a reader most needs to catch and the one no
lock can report. It is written from a real cost: a manager read that lock from
`origin/main`, concluded a fix was adopted, overrode a documented constraint on
the strength of it, and lost a dispatch.

Three worked examples of what the measurement catches, all from this host.
**onevcs:** onepipeline's `Cargo.toml` declared `onevcs = "0.4.1"`
byte-identically in v0.7.1 and v0.7.2 — a caret requirement, so it permitted 0.4.2
all along and was never the constraint. Only `Cargo.lock` moved, resolving 0.4.1
in v0.7.1 and 0.4.2 in v0.7.2; onevcs 0.4.2 published half an hour before v0.7.1
was tagged, so that release shipped an unrefreshed lock against a fix its own
requirement already accepted. **Widening that declaration is therefore a lever
connected to nothing** — the resolution is the whole of the fix, and a reader who
edits the requirement instead observes no change and wrongly concludes the bug is
open. **oneagentgraph:** `config/oneagentgraph.version` read 0.3.3 — the release
that added the session-conversation producer the DAG Observatory's transcript
route reads — and a real run under it emitted nothing, because onepipeline
v0.8.1's lock resolved oneagentgraph **0.3.0**. The `Cargo.toml` declares
`oneagentgraph = "0.3.0"` at v0.8.3 as well, and that tag's lock resolves
**0.3.4**; adopting onepipeline 0.8.3 here is what put the producer in force.
**onepipeline, in the previous adoption:** the producer that publishes tool results, live
turn text, and per-turn usage is oneagentgraph 0.3.6, and onepipeline v0.10.0 — the
newest tag at the time — was cut before the lock that resolves it and still linked
0.3.4. Its `Cargo.toml` said `oneagentgraph = "0.3.0"` at both tags, so the
declaration answered nothing; v0.10.1 is the release whose *lock* moved, and it is
the one this host adopts. **Never pick a release by being the newest tag** — pick it
by what its lock resolved, and confirm that against the binary once it is installed.
So never regress that floor either, and adopt a bump like any of these **between**
runs: a live driver keeps the binary it launched with, so no retry inside a running
run can pick one up.

One failure this block used to explain is worth reading for what it was **not**. A
retry pinned to a preserved branch could fail with `branch ... already carries N
commit(s) that main does not`, and it was not academic — it stranded four nodes
across three runs, three on 2026-08-16 and one on 2026-08-18, each needing an
out-of-band `just publish-branch` to recover. **An unadopted onevcs was never the
cause.** onevcs 0.4.2's fix is a *resume* path, reached only when `resumable()`
answers `Some`, and that filter's first condition is
`record.state == Lifecycle::Open`: a settled branch-preserving node leaves a
**closed** session record, and a reclaimed run root — the record names a
`run_root` and a `clone` that must still be directories — declines the same way.
Resume then declined, and control fell through to `honour_or_refuse`, which is
byte-identical in 0.4.1 and 0.4.2 and emitted that exact refusal at either.
Session `s-bbb59ee283af` closes the case: it opened 02:52:54Z and closed
03:25:59Z, its four refusals are stamped 03:38:18Z, 03:38:29Z, 03:41:55Z and
03:42:06Z — twelve to sixteen minutes after the close — and its record still reads
`"state":"closed"` with both its run root and its clone still on disk. **Under the
onevcs 0.11.0 the adopted release links there is no `honour_or_refuse` and no such
refusal at all**, so read that message as history rather than as something to
plan around.
<!-- dated-claim: incident the dates stamp when four nodes were stranded on this host
and when one session opened and closed; every claim here about what onevcs does is stated
by release, and the refusal it is about does not exist at the adopted one -->

Preserved stacked branches record their PR base so recovery targets the stack
rather than the root. A plan is the one tracked hierarchical graph: its
top-level DAG may mix direct agents, lifecycle agents, and explicit
human actions; a lifecycle node may itself run **several agent and human steps in
sequence on one branch**. Its reconciler accepts graph edits at any moment, because
there is no moment at which the graph stops being live. Review surfaced proposals
and use the [live-edit protocol](docs/orchestration.md#live-graph-edits) to change
the desired frontier.

## Which pin governs a dispatch

Every file in `config/` ending `.version` names one adopted release, and they do
**not** all answer the same question. Exactly one of them decides what a dispatched
node runs; the rest decide what this host's own commands run. Reading the wrong one
has produced a wrong diagnosis here twice, both times with every version file on the
host looking current while a real run came out empty.

| Pin | What it governs | What it does **not** |
| --- | --- | --- |
| `config/onepipeline.version` | The engine CLI every `just` launch verb runs — **and, through the crates that release linked, the oneagentgraph, onevcs, and onejudge a dispatched node actually runs.** This is the pin to move when a fix lives in any of them. | Nothing a dispatch does is decided anywhere else. |
| `config/oneagentgraph.version` | The CLI `just validate-personas` runs, and the `oneagentgraph` a `context` edit's live delivery invokes. | Which oneagentgraph reads a persona **at dispatch** — that is the linked one. |
| `config/onevcs.version` | The CLI the manager verbs run: `publish-branch`, `repo-recover`, `recoverable`, `work-status`, `integrate`, `repos`, `sweep`. | Which onevcs a dispatched node **publishes through** — that is the linked one. |
| `config/onejudge.version` | The PyPI `onejudge` distribution session setup installs and verifies, `onejudge_sdk` import included. | The `onejudge` crate a dispatched two-party member settles on — that is the linked one. |
| `config/oneharness.version` | The `oneharness` CLI the wrapper scripts and the smoke spawn. | `oneharness-core`, the library that CLI is itself compiled against and that the engine links on its own account — a different artifact this pin does not name, and which is at a different release from it today. |
| `config/onepipeline-ui.version` | The read API wheel and the browser bundle `just dag-ui` serves as one release. | Anything a dispatch runs; nothing in a run reads it. |
| `config/onetaskgraph.version` | The standalone `onetaskgraph` CLI this host spawns to read and write plans. | Any library linked into `onepipeline`; the engine wheel's bill of materials does not contain this separately spawned executable. |

**The CLI pins are reconciled against the engine's own resolution, not moved one at
a time.** `tests/test_linked_libraries.py` reads the CycloneDX SBOM the
`onepipeline-cli` wheel ships and fails when a pin here names a different release
from the one that wheel linked — so a bump to `config/onepipeline.version` that left
a sibling pin behind is a failing check rather than a silent divergence, and the
manager verbs stay on the same `onevcs` a dispatch publishes through. That is a
narrower guarantee than it sounds and worth stating exactly: it makes the two
*agree*, and it is the SBOM that decides which value they agree on.

**One hazard comes with that archive, and it is a property of where it lands.** Session
setup installs `onetaskgraph` into `$HOME/.local/bin`, which every checkout of this
repository on the host shares — so two checkouts at different pins overwrite each
other's copy, and a bare read of that path measures whichever provisioned last rather
than anything about the checkout doing the reading. Measured on 2026-08-29 while this
pin moved: the canonical checkout, an adoption behind, reinstalled its own release over
this branch's within a minute and repeatedly mid-gate, byte-identical to the archive its
pin names. Nothing warns; the version simply changes underfoot, and a check that reads
the path is answering about the host rather than about its own tree. That is why
`tests/e2e/test_onetaskgraph_host_e2e.py` provisions before it asserts — the claim that
survives a shared path is that *this* checkout's installer honours *this* pin. Every
sibling tool avoids the hazard by living in the checkout's own `.venv/bin`; giving the
archive a per-checkout directory too would end it, and is a change to provisioning
rather than to any check.

`config/onetaskgraph.version` is deliberately outside that reconciliation, for the same
artifact-boundary reason as the `oneharness` CLI pin: session setup installs and verifies
the release archive of a program this host spawns. It answers which standalone plan-store
CLI is installed here; it says nothing about libraries compiled into the engine, and the
engine wheel's bill of materials has no onetaskgraph entry to compare it with.

**`config/oneharness.version` is the one pin that cannot be reconciled that way,
and that is a property of what it names rather than a hole in the gate.** The crate
beside it is `oneharness-core`: `oneagentgraph` 0.3.13 brings `oneharness-core` 0.12.1
and `onejudge` 0.6.2 brings `oneharness-core` 0.12.1, so the adopted engine links
**one** release of it, as it has since the adoption on 2026-08-25 and did not before.
**That collapse does not make the pin reconcilable, and the check that used to say it
would has been re-shaped rather than retired.** The reason was never the count: this pin is the
`oneharness` **CLI** the wrapper scripts and `just smoke` spawn, which is a different
artifact from the core whatever the core's version graph looks like. Re-measured on
this host's own installed wheels on 2026-08-29, the two disagree by a whole minor —
`config/oneharness.version` reads 0.11.2 and *that CLI wheel's own SBOM declares the
`oneharness-core` it was compiled against as 0.12.1* — so reconciling them would
assert that two artifacts carry one number. What
`tests/test_linked_libraries.py` gates instead is the property the count was only ever
a proxy for: which dependent brings which core, and that nothing named `oneharness` is
a crate the engine links at all. Read a dated oneharness claim accordingly: the
wrapper's behaviour, the fallback chain, and the smoke are the CLI's, while a turn a
*dispatch* runs goes through the linked core its member's engine carries.

**How this pin's number compares with the linked core's is not evidence about
anything, in either direction.** They are separate artifacts published from one
repository on their own cadences, and what tells them apart is what each one *is* —
this pin is the release `scripts/session-setup.sh` installs as the `oneharness-cli`
wheel and `scripts/oneharness-agent.sh` and `just smoke` then spawn; a linked core is
a Rust library compiled into the engine binary by whichever dependent resolved it,
and reaches this host only through a dispatched member's turn. No comparison of
version strings can establish that, so do not try to read one off the numbers. They
have already been equal and unequal within five adoptions: `0.10.2` against `0.10.1`,
then `0.10.2` against `0.10.2`, then `0.10.3` against `0.10.2`, then `0.11.0` against
`0.12.0`, and `0.11.2` against `0.12.1` today. **The sharpest form of that today is
inside one wheel**: the
`oneharness-cli` 0.11.2 this host installed ships its own CycloneDX SBOM, and it
declares the `oneharness-core` it is built from as 0.12.1 — one artifact, its own
library, two numbers, and this time the core it names is the same release the engine
links, which is a coincidence of cadence and still not a reconciliation. Read the pin
as the CLI's release whatever number it wears, and
take the linked core from the SBOM read this document already makes — that one goes to
the engine wheel, which is the only artifact that knows.

**One thing the adopted CLI changed is operator-visible, and it decides how you read
a stopped chain.** A fallback chain has always stopped at a candidate whose failure it
could not classify — spending the next identity's quota on a failure nothing explains
is the one thing it must not do — but through 0.10.2 it reported that stop with the
sentence a genuine task failure gets, `ran but did not succeed`. From 0.10.3 a
candidate that showed nothing for itself says so instead: *failed with nothing to show
for it — no tool call, no billed usage, and no cause it could classify — so the chain
stopped there and tried no candidate after it*, with `fallback.stopped_without_work`
and `results[].work` of `none` carrying the same reading into the report and the
history record. Name the two rather than counting them, because which is which is the
whole reading. *Nothing to show for it* is the **untried chain**: that candidate
produced no tool call and no billed token, so an unrecognised startup refusal at the
front of the chain — a quota or an auth problem the classifier could not name — is
still the thing to look for, and every identity behind it is untouched. `ran but did
not succeed` survives as its opposite, and on 0.10.3 it is what a candidate that
*produced* something gets: a genuine task failure, or an unclassifiable one with
`results[].work` of `done` and billed usage behind it, which is work re-running would
spend twice. Both still stop the chain, and this is the CLI's own summary, so it
reaches a wrapper-spawned turn and `just smoke` rather than a dispatched member's.

What holds all of it is `tests/test_linked_libraries.py`, and it is written to the
two-version reality rather than as an equality — it reads the SBOM's own dependency
edges and fails when either core moves, when a dependent stops bringing its own, or
when a third appears.

A divergence is allowed only where the linked release is not installable, and only
as a **declared** one naming both versions, so the next bump fails on it rather than
inheriting it. **There is none today.** The one this repository used to declare was
`onejudge`, pinned at 0.4.0 against a linked 0.5.0 on the ground that the tag was
published and the PyPI distribution was not; both halves of that ground are gone —
the registry carries every `onejudge` from 0.5.0 to 0.5.4, and the adopted engine links
0.5.4 — so the declaration is retired rather than re-dated, and every reconciled pin
now equals what the engine wheel resolved. The machinery that would carry the next
one is deliberately kept: `DECLARED_DIVERGENCES` is an empty registry with its type
and its gates intact, so declaring one is an entry rather than a rewrite.

## Sequencing a node behind a release

**The surface, the modes, and the view are in force here; nothing on this host uses
them.** Those are two different sentences and the distinction is the whole of this
section. `config/onevcs.version`, `config/onepipeline.version`, and
`config/onepipeline-ui.version` now carry the releases that add the `onevcs
release` verbs, the two plan-node fields, and the rendering of what each one answered,
and all three were driven on this host's installed binaries rather than read off a
change request. **What changed under this adoption is the other half of that
sentence**, and it changed without anybody configuring anything here: onevcs 0.16.x
reads a target from the **repository's own** `release-targets.toml` as well as from the
host's document, and six of the fourteen repositories registered here have since landed
one — `oneagentgraph` (3 targets), `oneharness` (6), `onejudge` (3), `onepipeline` (3),
`onepipeline-ui` (4), and `onevcs` (4). So a dependency landing in one of *those* now
has a release to await. **`ai-orchestrator` declares none**, and a
repository that declares none releases nothing as far as this mechanism is concerned —
so a plan of this repository still earns no reference row and no hold, and one
naming neither field gets exactly the run it got before the pins moved. Read every
behaviour below as one this host can now perform, and none of them as one it currently
performs, because no plan here names either field yet. **The view is the one where that
distinction is invisible**, because a
surface with nothing to render looks exactly like a surface that is not there: an
operator opening a node in the DAG Observatory today sees no release row, and that is
the absence of a declared target rather than the absence of the release. What was
measured and what a target would take are the last two paragraphs.

A **release target** is one artifact a repository publishes: a crate, a wheel, an npm
package, a browser bundle. A repository has a *set* of them on cadences that need not
coincide, so a consumer has to say which one it consumes — this repository installs
`onepipeline`'s wheel while `onepipeline` itself links `onevcs`'s crate, and "the
crate is out" and "the wheel is out" are different waits. Per onevcs's own
`docs/contract.md` there are **two** declarations and the producer's wins. A repository
declares what it publishes in a `release-targets.toml` at its own root, which is the
canonical answer and travels with the repository; the *host's* half lives in a document
at one conventional path
under `onevcs`'s state root — `$ONEVCS_HOME/releases.yml` — rather than in the
registry or the rules file, deliberately, so an older `onevcs` sharing a host is
handed a byte-identical registry whether or not a target is configured. That host
document is what says which adoption mode a repository gets and can stand a target in
for a repository whose own declaration this host cannot read. Six verbs
read the pair: `onevcs release targets`, `release discover`, `release latest`, `release
status`, `release acknowledge`, and `release declaration`, the last of which reports
what one repository's own document says without consulting the host's at all.

**Two release styles, because they are two different waits obtained two different
ways.** An *automated* target carries a **probe** — a script checked into the released
repository, or a one-liner configured on the host — and is answered by running it
under a bounded timeout: a version on its stdout is a release, no output is "no
release yet", and anything else is *not answered*. A **human-step** target carries **no
probe at all**, because the release is something a person does, and is answered only
by an explicit record somebody writes afterwards: `onevcs release acknowledge`,
naming the landed reference, the target, and the version. That records the version
against the landing commit with a timestamp and an actor. Repeating it with the same
version is a safe no-op — it succeeds, changes nothing, and re-reports the original
timestamp and actor, so a retried command and a second person doing the same thing
are both harmless — while a **conflicting version is refused**, naming the version
already recorded,
until `--supersede` explicitly supersedes it and keeps the replaced one in that
record's own history. The style is the shape of the configuration rather than a label
on it: a human-step target naming a probe, and an automated one naming an action, each
fail to load.

Nothing on this host would be the second kind. Every repository here releases
automatically through release-plz, so `human-step` is a member of the vocabulary
nothing here uses — worth recording rather than omitting, because the first one
somebody configures behaves unlike everything else in this document: it is a wait on a
colleague.

**Two adoption modes, resolved over exactly four rungs.** A node adopts a dependency
either `fast` — launch now, against the branch that carries the work — or `published`
— launch only once the release carrying that work exists. Which mode a node gets is
decided in this order and no other: the node's own `adoption` field, then the
repository rung, then the global rung, then `fast`. There is no fifth rung, no
plan-level tier, and no run-only override, so a plan that wants a mode says so on the
node or in the repository's own declaration. `onepipeline`'s
`docs/contract-divergences.md` entry 40 specifies those two node fields — `adoption`,
and `consumes`, which names the target per **dependency node id** rather than per
repository, because two nodes in one repository can legitimately want different
targets — and its `src/release.rs` is where the chain is implemented. A repository
that declares **no** release targets releases nothing, so a dependency landing there
earns no reference row and no hold whatever mode its dependents resolve to; that is
`ai-orchestrator` and seven of the other repositories registered here, and it is why
a plan of this repository naming neither field gets exactly the run it gets today.

**Under fast adoption the framework writes the reference block, so a task must not.**
A dependency landing outside the node's own repository gains a row naming that
dependency, its repository, its branch, the landing commit, and the release target, in
a trailing `## Cross-repository references` block appended to that node's rendered
task by the same rendering that appends `## Planner context`. When those releases
arrive the node is sent one `context` note naming the versions — into the live turn
where the dispatch has a controllable one and onto its next dispatch where it does
not — framed as observed state and adding no acceptance criteria. So a task must
**not** instruct a worker to go and find and pin a dependency's commit: that
instruction competes with a block the framework has already written and a note it
will deliver, the two answers disagree, and the worker follows the one in its task.

**Under published adoption the node does not launch at all** until every such
dependency answers released. The hold is absolute — no timeout, no deadline, no retry
budget, and no automatic degrade to fast adoption — and it **never fails the node**.
What it does instead is raise a **non-blocking planner surface** naming what it awaits,
for how long, and in which style, repeated on its own interval so the wait cannot go
silent. That surface is a decision put to you rather than a report: keep waiting, flip
that node to fast adoption by live edit, or stop the run.

**A human-step wait is a wait on a person, and reads that way.** The scheduler does
nothing different for one — the same indefinite hold, never failing — and what differs
is where the answer comes from and what is reported: the surface carries the action
somebody has to perform, and the run sits there until they perform it and acknowledge
it. **Nothing in this harness performs a human release step, prompts anybody for one,
or acknowledges one on anybody's behalf.** A manager reading one of those surfaces is
the person who has to act, or find who will.

**A probe is not a gate.** It answers what version is out there; it never rules on a
change, never refuses a publication, and never stands between a branch and its merge
path. The host-run verifier onevcs 0.11.0 removed is not coming back under another
name — [what that cost](#what-this-repo-is) is the section this document opens with.

**"Not answered" is not "not released", and a held node stays held on the first.** A
probe that timed out, failed to spawn, or printed something unusable has said nothing
about the world, and treating it as evidence that a release has not happened is the
single most damaging thing this could get wrong. **Awaiting a human step is a third
answer** and is folded into neither: reported as an unanswered probe it would read as
a broken tool, where the truth is a healthy wait on a colleague; reported as "not
released" it would claim a probe answered when none ran.

**What carries each half, and what was measured of it.** Read each pair below as *the
release that carried the capability* and *the pin this host runs*, which stop being one
number the moment an adoption moves past the release that introduced something — as
both of the first two now have. The release-targets surface — those four verbs and the
`release-probed` / `release-acknowledged` / `release-observed` event kinds — is the
**version-control CLI** at onevcs 0.13.0
(https://github.com/nickderobertis/onevcs/pull/78);
`config/onevcs.version` reads 0.16.2, past that floor, and what the adopted engine
links is past it too, so a dispatch resolves a release over the surface as well. The
adoption modes merged as https://github.com/nickderobertis/onepipeline/pull/113 and are
carried by the **engine CLI** at onepipeline 0.13.0, the first release cut after it;
`config/onepipeline.version` reads 0.17.5, past that floor too. Those two carrying
numbers are equal and are about different tools; nothing here should be read off the
number alone. The two **pins** beside them are not equal: they carried one number for
the two adoptions ending 2026-08-24, parted at the one on 2026-08-25, and have moved
independently at every adoption since — so a bare number in this section may be any of
four things, and every one of them is written with its tool beside it. The view that shows
which release carried each landed node, and every release event, is **onepipeline-ui
0.6.3** (https://github.com/nickderobertis/onepipeline-ui/pull/36), and
`config/onepipeline-ui.version` reads 0.6.4, past that floor — a release with **two** artifacts, the
`onepipeline-api-cli` wheel behind `just telemetry-server` and the `onepipeline-ui` npm
bundle behind `just dag-ui`, which move together or serve one release's view against
another's data. What that third half changes on this host is almost nothing, and saying
so is the point: with no target declared there is no release event in any run, so the
only thing the *reader* answers differently is a bumped `timeline_schema_version`,
re-measured with the rest of that shape in
[`docs/telemetry.md`](docs/telemetry.md#seeing-the-supervisory-tier). Everything else
the release adds is a field that stays absent and a view that stays unrendered until
something declares a target. `tests/test_release_adoption_guidance.py` re-takes every
number above: it reads each pin this section names out of `config/` and holds it at or
past the floor carrying its half, so an adoption that moved one of them fails there
rather than leaving this paragraph describing the pins before it.

Five things were driven rather than read, on 2026-08-29. The pinned `onevcs --help`
lists a
`release` verb group whose six subcommands are `targets`, `discover`, `latest`,
`status`,
`acknowledge`, and `declaration`; `onevcs release targets ai-orchestrator` answers
`adoption: fast`,
`default target: none`, `declaration: undeclared: no release-targets.toml in …`, and
`targets: none`, and `release latest` on the same repository
refuses with *the repository … declares no release targets, so there is nothing to ask
about; declare some in its own release-targets.toml, or under `repositories:` in the
release-targets file*. That second file is
`$ONEVCS_HOME/releases.yml` — one conventional path under the state root, deliberately
outside the registry so an older `onevcs` sharing this host is handed a byte-identical
registry either way — and **this host does not have one**; what it has instead is the
producer half, which is why the same verb against `nickderobertis__onepipeline` answers
`declaration: declared: 3 target(s)` and lists `crate`, `pypi`, and `npm`. That surface
runs end to end here rather than merely loading: `onevcs release latest
nickderobertis__onepipeline --target pypi` runs the release probe that repository
checks in beside its own declaration and answers `released: 0.17.5`, which is the
release this change adopts. And a plan node's two fields
reach the engine's own loader: `onepipeline start` refuses `adoption: "bogus"` with
*unknown variant `bogus`, expected `fast` or `published`*, and refuses a `consumes` key
that is not a dependency with *node 'b': `consumes` names 'nosuch', which is not one of
this node's deps* — both before anything is dispatched. And the view was driven as the
pair an operator starts and opened the way an operator opens it: `just telemetry-server`
and `just dag-ui` over a runs root of recorded runs, in a browser, which renders the
run's goal, its counters and its failed node — and **no release row**, in either the
Overall or the Graph view, with no span those runs serve carrying a release either.
That is a statement about those runs rather than about this host: they are checked-in
fixtures and can never grow a release event. What fires the day this host declares a
target is the journey that asks every registered identity, not the one that renders a
frozen tree.
`tests/e2e/test_release_adoption_in_force_e2e.py` is what re-takes all five, against the
installed artifacts rather than against this paragraph: the CLI's verb group, what it
answers for this repository, the `onevcs` the engine links, the loader's two refusals,
and the read API behind the view.

**So what remains is configuration, not adoption.** Six of the repositories dispatched
against from here now declare their own targets, so putting the mechanism to work no
longer starts with declaring one: it is writing a plan whose node names `adoption:
published` and a `consumes` naming one of those targets, and deciding whether this host
also wants a `releases.yml` — for a repository-level adoption mode, or to stand a target
in for a repository whose own declaration it cannot read. That file would want a first
decision about whether it belongs in this repository's tracked `config/` the way
`onevcs.rules.yml` does, since nothing here installs one today and no `just` recipe
wraps `onevcs release`. Until a plan names a mode, a node's
`adoption` field is read, validated, and resolved to `fast` with
nothing to await, which is the run this host already gets.

## What phase a change's events belong to

Every `onevcs` event carries a **phase**, and it is the bucket a reader names instead
of enumerating the kinds inside it. Four of them, over one change: **Development** —
the session, its clone, its lock, its commits, the repair of a branch that carried an
incomplete step, and the push of the session's own branch; **Integrate** — the host's
merge queue, the merge it completed, a base that moved out from under the publication,
and a push of any branch but the session's own; **Review** — the change request opened,
its checks moving, and it merging; **Release** — a probe run, a person's
acknowledgement, and a landing observed as released. It is spelled `Phase` rather than
`Lifecycle` because that word is already taken by where a *session* is in its life, and
the two are different questions: a session that has closed still has a change in
review. Naming the phase rather than the kinds in it is the whole of the value — a kind
added to a phase later arrives in the read that already wanted it, where a `kind` glob
would have to be widened by hand.

**The producer stamps it, and one kind is why.** A phase is not a fact about a kind: a
`push` of the session's own branch is Development and a push of anything else — the
base a `local-direct` squash lands on, the base a merge train advanced — is Integrate,
and only the process that pushed knows which it did. So every other kind is classified
from the kind, and `push` is stamped by whoever emitted it. An envelope an older build
wrote carries no phase at all and is read as the phase its kind decides — falling
through to Development for a `push`, where the kind decides nothing and there is
nothing left to recover the producer's classification from. That fallback is a reading
of a record rather than a claim about the push, which is why the phase is stamped from
here on.

**Phase is a field of the event-filter grammar this host already writes.** A matcher
takes `phase` beside `source`, `kind`, `run_id`, `node`, `step`, `member`, and
`persona`; every field it sets must match, a field it leaves unset is not consulted,
`exclude` still beats `include`, and an empty `include` still admits everything. So a
launch's `filters.vcs` narrows a run to a phase without restating the kinds in it.

**The default is every phase the repository supports, and that is not always four.** A
session's supported set is derived where its stream is opened, from what this host
knows about its repository, rather than named by the consumer: a `local-direct`
identity opens no change request and so has **no Review phase**, and a repository
declaring no release targets releases nothing and so has **no Release phase**. Both
exclusions are live here — `ai-orchestrator` and `spanish-language-tutor` are this
host's `local-direct` identities, and `ai-orchestrator` declares no release
target, so a session of *this* repository supports Development and Integrate and
nothing else. **The second exclusion is no longer host-wide**, which is what this
adoption moved: six repositories registered here now carry their own
`release-targets.toml`, so a session of `onepipeline` or `onevcs` does have a Release
phase and a filter naming one is answered rather than refused. Every answer that
derivation cannot reach **widens** the set rather than narrowing it: a session this
host keeps no record of, or one whose repository it can no longer resolve, takes all
four, because a read that quietly left events out would be indistinguishable from a
session that never wrote them.

**Naming an unsupported phase is a validation error; merely being in one is a silent
drop.** That asymmetry is the design rather than an inconsistency. A filter that
*names* a phase the session cannot produce is refused when the stream is opened,
naming the phase, the session, and the phases it does have — because it would be
answered with nothing, and nothing is what a filter for the wrong phase and a session
that did nothing look alike as. An event that merely *falls* into an unsupported phase
under a default-everything read is dropped without comment: nothing was asked for and
nothing was denied, so there is nothing to say.

**That rule lives at the library seam, not at the command line**, and reading one for
the other will mislead you. `onepipeline` opens every session through
`EventStream::open_filtered`, so a run's `filters.vcs` gets both halves. `onevcs
events --filter` does not — it reads the file's own lines and matches them, consulting
no supported set — so it neither refuses an unsupported phase nor drops one. Re-measured
2026-08-29 on the pinned onevcs 0.16.2, against two streams because
the two halves need different ones. On a stream a phase-stamping build wrote —
`onevcs events s-37d91e5f2b7f --filter '{"include":[{"phase":"review"}]}'` — the verb
exits 0 with no output on this `local-direct` identity rather than refusing, and
unfiltered the same stream's first two events carry `"phase":"development"` outright.
On a stream written before the field existed (`s-a456ae52b6eb`, opened 2026-08-16) the
same `review` filter is likewise a silent empty exit 0, and unfiltered its events print
with **no `phase` key at all** — yet `{"include":[{"phase":"development"}]}` returns
all three of them, classified by the fallback above. Any session token this host still
holds a stream for re-takes
whichever half its stream is old or new enough for. So do not read a command-line
events read as a test of what a run would relay.
`tests/test_phase_and_landing_guidance.py` re-takes the claim, holding it to the pin it
was measured on so a release that gave the command line a supported set brings this
paragraph due.

**A session's release events reach a run through the public reader, correlated by
landing commit.** `release-probed` is written on the session's own stream and always
was. The other two — `release-acknowledged` and `release-observed` — are recorded on
the **identity's** own release record, outside every session, because a release
happens long after the dispatch that made the work has ended; nothing on that record
names a session, and the landing commit is the only thing that correlates one to a
piece of work. onevcs 0.14.0 makes that join itself: handed the session token a
consumer already holds, `EventStream::open_filtered` returns that session's releases
beside the session's own records, and the address of that second stream is never handed
out, named in a refusal, or derivable. That is the point rather than a detail — the
alternative was for every consumer to recompute a private naming scheme, and the day
the scheme moved it would have relayed nothing, silently. The landing it joins on is
the one `onevcs status` would report with retries followed, so a **retried** branch's
release is attributed to the session the work went on in rather than to the superseded
copy.

**What carries this, and what it changes here.** The phases, the retry chain, and the
release correlation arrived in **onevcs 0.14.0**
(https://github.com/nickderobertis/onevcs/pull/80). What carries them into a run — a
phase on every relayed envelope, a watermark per producing stream so the release series
cannot hide the session series, and the paced read that picks up a settled node's
releases — arrived in **onepipeline 0.14.0**
(https://github.com/nickderobertis/onepipeline/pull/117). Both floors are behind this
host: `config/onevcs.version` reads 0.16.2 and
`config/onepipeline.version` reads 0.17.5, and the adopted engine links onevcs
0.16.2, so a dispatch resolves a phase over the same release the manager verbs do.
**Those two numbers are not one number**, which they were for the two adoptions ending
2026-08-24 and have not been since — read each with the tool beside it. What
it changes for a plan of **this** repository today is the vocabulary and nothing else:
`ai-orchestrator` declares no release target, so no session of it has a Release phase
and no run of it relays a release
event, and a launch naming neither `adoption` nor `consumes` gets exactly the run it
got before the pins moved. Elsewhere the Release phase is live rather than theoretical:
`onevcs release latest nickderobertis__onepipeline --target pypi` wrote a
`release-probed` envelope carrying `"phase":"release"` onto that identity's own release
record on 2026-08-29, which is the first release event this host has ever held.
`tests/test_phase_and_landing_guidance.py` re-takes the pins and the floors, and
`tests/e2e/test_release_adoption_in_force_e2e.py` re-takes the probe itself against the
installed `onevcs`.

## What "agent" means here

In this repo, an **agent** (or **subagent**) is a **dispatched onejudge process** —
a coding agent run under a simulated-user supervisor as a node of a plan launched
by `just orchestrate`. This is the default
sense of the word everywhere below and in requests to you. When a task says "use
an agent," "have an agent do X," "dispatch an agent," or "spin up a subagent" —
including for research or investigation, not just code changes — dispatch
onejudge. Do **not** reach for
the host harness's own built-in subagent mechanism (its own agent/task/fork tool)
unless the request names that mechanism explicitly. When the wording is ambiguous,
dispatch onejudge.

## Where a plan of this repository lives

**A plan of this repository is stored on the `plans` GitHub Projects board** — the
`github-projects` source `onetaskgraph.yaml` configures at owner `nickderobertis`, project
number 2, filing its project and task issues in `nickderobertis/ai-orchestrator`. Author one
there, read one with `just plans project show plans:<project>`, and launch one as `just
orchestrate plans:<project>`.

**That board held one plan at a time until 2026-08-29, and the retreat that worked around it
is over.** Two defects made a second plan on the board corrupt every plan on it, and both
were repaired upstream and adopted here rather than worked around again.
`tests/test_plan_store_guidance.py` re-takes that claim: it holds the release named below
against `config/onetaskgraph.version`, holds the tag the live lane's source is cited at to
that same release, and requires both defects to stay named — so a pin that moved under
this passage fails there rather than leaving it describing a board this host can no longer
write. The two defects:

- onetaskgraph's `github-projects` source **discarded the query it was handed**, so a read
  scoped to one project answered with every project's tasks. Fixed in **onetaskgraph
  0.2.12**, which also declares each capability field honestly.
- onepipeline's settlement write-back **renamed a destination project to its own native
  identifier and wrote no labels**, degrading the record it projected onto. Fixed in
  **onepipeline 0.17.2** and carried by the adopted **0.17.3**.

Both are measured rather than assumed, on this host and against the real board with two
projects on it, and both halves were re-taken on 2026-08-29 with a throwaway second project
— `board-multiproject-proof`, issue #74 with its one task #75 — standing beside
`plan-store-capability-repair` (#42, tasks #43-#46) and removed again afterwards.
`tests/test_plan_store_guidance.py` re-takes both halves against the release this
checkout pins and the source `onetaskgraph.yaml` actually configures, so a pin that moved
under this paragraph fails there rather than leaving it describing a board this host can
no longer write.

*The read.* On the pinned onetaskgraph 0.2.12, `task list --source plans --project` answered
`#43 #44 #45 #46` for the first project and `#75` alone for the second. **The contrast was
taken by accident and is the better half of the measurement**: the same two reads twenty
minutes earlier answered all five tasks for *both* projects, because the shared
`$HOME/.local/bin/onetaskgraph` had been overwritten with 0.2.11 by the canonical checkout
a minute after this one provisioned 0.2.12 — the hazard recorded two paragraphs below,
firing live and silently in the middle of the measurement it would have falsified. **So
read that pin's version at the moment of each board measurement and record it beside the
answer**, and take the answer through a copy of the binary this checkout provisioned rather
than through the shared path; a board read that does not name the release it was taken on
is a read about whichever checkout provisioned last.

*The validation.* `just check-plan` on the first project reported
`github-projects-capabilities/honour-the-query` — a step of that project's own node — and
named nothing of the second; on the second it reported `0 dispatched node(s) … and every
task carries a review record`, its one node being a `kind: human` action, and named nothing
of the first. That second answer also says the review record `project copy` carried onto the
board was read back off it.

*The projection.* That throwaway settled `complete` with **no agent dispatched** — an empty
`dispatches/` directory and a journal of seven events whose kinds are `run-started`,
`decision-pending`, `node-settled`, `human-attested`, `edit-committed`, `decision-cleared`
and `driver-adopted`, none of them a dispatch or a turn. Its project record came through the
settlement byte-identical in title, labels and body; its task moved `Todo` → `Done`, and the
other project was left at `Todo` with its own four tasks untouched. **One field did move that
the write-back did not move**: the project's own status read `Todo` before the launch and
`Backlog` by the time the write-back read it at settlement, so something between the launch
and the settlement set it — worth knowing before reading a project's status column as the
run's verdict, since the node's status is where the verdict actually landed. See [Which pin
governs a dispatch](#which-pin-governs-a-dispatch) for why the engine pin is what carries the
second fix into a dispatch.

**What that repair does not buy is a projection you may take on trust.** The write-back is
still best-effort and off the reconcile loop, so a settlement that never reached the board
settles the run exactly like one that did — measured again here, where that same throwaway
plan settled `complete`, `just status` reported `1/1 done SETTLED complete`, and its
projection had failed outright on a GitHub secondary rate limit. The only thing that said so
was one line on the driver's own stderr; adopting the settled run again re-ran the write-back,
which then landed. So a settled run is evidence about the run and none at all about the board. Read [what the write-back owns,
and what a green run proves](docs/orchestration.md#what-the-write-back-owns-and-what-a-green-run-proves)
before treating a settled board as the record of a run.

**One value on that source is never repointed.** A live run's settlements are projected back
to the project it was launched from, so changing `plans`'s plugin, owner, project number,
repository or credential variable moves a running plan's store out from under it.
`tests/test_plan_source_roots.py` holds all five, and it is what let the retreat be undone by
pure subtraction: `plans` was left untouched throughout rather than repointed, so retiring
the workaround deleted a source instead of migrating one back.

## Your loop as manager

You are the **manager**: the top-level session role. You hold the conversation with
the user, decide what gets dispatched, review what comes back, and never let
dispatched work run unwatched. You do not decompose work yourself — a dispatched
**planner** does, and its judgment is deliberately not restated here. It lives in
[`personas/planner.yaml`](personas/planner.yaml), because that file becomes the
planner's own system prompt and so travels into whatever repository is being
planned against, where this document is not.

Every published surface still spells that role `planner` — the planner channel,
`--filter planner`, planner surfaces, `awaiting-planner`, `onepipeline next`, and
`onepipeline reply`. All of them reach **you**; `manager` names the session role
and is never a command.

1. **Decide whether to dispatch a planner at all.** A dispatch is a fresh agent
   that pays a fixed cost to prepare its context — reading the repository,
   orienting — before it does anything useful, so a dispatch that buys nothing is
   cost with no benefit. Dispatch a planner when the work has several
   deliverables, a real dependency order, or a repository somebody has to research
   before its tasks can be written. When a user asks for a plan—through a harness
   plan mode or informally—and the work is orchestration-worthy, say that the plan
   comes from a dispatched planner, is stored as a local Markdown project, and is
   run by passing its qualified `source:project` id to `just orchestrate`.

   Reading a target repo to decompose work and write precise tasks is the
   planner's dispatch, not yours. Read only far enough to write the brief and to
   review what comes back; investigation past that point is dispatched.

   One narrow direct-tweak exception remains: **the complete gate can prove it**.
   If you already hold the context and the change is already determined, you may
   apply it directly only when a check in the target repo's complete gate
   exercises the changed artifact and demonstrates the fix; report that passing
   gate result. A mechanically checked rename can qualify. If no gate check proves
   the payload, dispatch it as authoring; commit-message payloads, PR titles and
   bodies, changelog prose, and release-note prose are in this category. Line
   count and urgency are irrelevant. “It's just a commit message,” “the diff is
   empty,” “it's only integration coordination,” and “it's faster than
   dispatching” are not exceptions.
2. **Write the planner's brief.** It is the whole input to a context that has
   never seen this work, so be detailed in three things and let the planner derive
   the rest: the **goals** — what the user wants true afterwards; the
   **constraints** — what it may not change, break, or spend; and a **suggested
   high-level implementation** where you have one, marked as a suggestion rather
   than a decision. Give it the user's motivation in the user's own terms, because
   that is the one thing no amount of reading the code recovers and it is what
   every node's `## Why` is written from. Name the repository and the qualified
   project id the plan must create. A plan of **this** repository is stored on the
   `plans` GitHub Projects board, for the reasons in [Where a plan of this repository
   lives](#where-a-plan-of-this-repository-lives). For a
   plan of any other repository, name the `authoring` source rooted at the gitignored
   `.plans/` that `just plan` writes. Either source's project and task records are
   launchable in place, and a local one need not be copied to GitHub Projects first. Do not hand
   it contracts, acceptance criteria, or a node
   breakdown: researching the code and producing those is the dispatch you are
   paying for.

   Treat an unfamiliar project-sounding name as a lookup, not a question: search
   local paths such as `~/projects`, then `just repos`, then the current GitHub
   account with `gh search repos <name>` and `gh repo list <owner>`. A hit whose
   description matches the prompt's other clues resolves the reference; ask only
   when the search fails or leaves multiple strong candidates.
3. **Answer its questions while it plans.** A planner asks you at every fork that
   could change a key outcome, batched rather than one at a time. Decide yourself
   anything the brief already implies and anything about this harness, this host,
   or how the work will be run; take to the user what is genuinely theirs — a goal
   that reads two ways, a constraint you would have to relax, a scope or cost
   decision, a contract that changes what they asked for. Answer promptly: a
   blocking question stalls the planner completely and produces no other signal
   while it waits, which is the failure [the watch
   invariant](#never-let-dispatched-work-run-unwatched) exists to catch.

   A planner also escalates the exceptions it recorded, as a non-blocking check-in
   naming them. Those are **high-value to put in front of the user**: each is a
   constraint that could not be met, a goal reached a different way, or an
   assumption made because no answer came, and each is a decision the user would
   want back. Never let one settle silently into the plan.
4. **Review the plan it returns.** This is your review and not a second run of the
   planner's: you are asking whether this plan gets the user what they asked for,
   judged against the request rather than against itself.

   - Every goal and constraint in the request is delivered by some node, and you
     can say which one.
   - Nothing simpler would do. A split that buys no parallelism, or a node a
     sibling already covers, spends a dispatch for nothing.
   - Each node's `## Acceptance criteria` would actually prove the goal, and could
     not all be satisfied while the goal is missed. Unrealistic testing is where
     that gap usually hides.
   - Each node's `## Acceptance criteria` carries its own verification demand,
     and for a node that changes code that demand is the checks which exercise
     the change — the tests over what it touched, the lint over its diff — never
     its repository's complete gate. Nothing else asks for one: the completion
     clause every dispatch shares states only that the task's criteria are met
     and the change is committed whole, so a demand no node's list names is a
     demand no judge looks for. **Node criteria stopped naming a gate here**, and
     a plan that names one again is asking a worker for something this host no
     longer dispatches it to do: the full bar runs downstream on the merge path,
     after the node has settled, so a criterion resting on it is one the worker
     cannot satisfy from inside its own dispatch — the failure the bullet below
     is about.
   - Every criterion is satisfiable by the worker inside its own dispatch, from
     what that dispatch controls. One resting on a merged change request, a
     deploy, or a third party describes state that arrives after the worker is
     gone, and finished work is failed against it. How that list is written is
     [the planner's](personas/planner.yaml); whether it would prove this node is
     yours.
   - **A criterion about the dispatch's own report says what must be true of it,
     never where it must sit.** This host demanded that a completion report come
     after everything else the dispatch produced, and that anything found later be
     repaired and the whole report written again; that demand is **withdrawn** — a
     dispatch's conversation does not end when the worker reports, the supervisor
     keeps asking, and answering well means running things, so every good answer
     invalidated the report and only restating it whole complied. It failed six
     nodes of one run in one night, each with complete committed work, a green
     deterministic tier, and no acceptance criterion found unmet, and two of those
     carried an escalated warning about it in their own task and failed anyway;
     managers on other runs hand-published branches to get around judges that
     would not accept them for it, which leaves the node settled `failed`, its
     dependents skipped, and the run's own record no longer describing what
     happened. What a criterion asks for instead is the property that ordering was
     serving — **every claim the dispatch makes about the finished work is true of
     the tree as it finally stands** — satisfied by a correct delta stated in the
     turn that changed something as fully as by restating the whole report, and
     not by silence. A *false* claim still fails on its merits: a report asserting
     a check passed on a commit where no such run occurred is wrong on the facts,
     and one node was correctly failed for it. `config/dispatch-appendix.md` is
     where a dispatch reads this, and `orchestrator/criteria_guard.py`'s demand is
     what makes every task state it.
   - Each node's `## Why` carries the user's own motivation rather than the
     handoff.
   - `deps` names real prerequisites, so unrelated branches stay parallel.
   - Nodes have unique IDs; agent nodes carry `persona` + concrete `task` prose
     (and optionally `repo`/`steps` for a lifecycle that runs several steps on one
     branch); `kind: human` nodes carry only the action prose. Start from the records
     `examples/projects/tracked-release.md` and `examples/tasks/tracked-release/`,
     which state all four shapes. A node carrying `done_when` is refused
     while the plan loads, at any declared `schema_version`. `max_turns` is how a
     task gets more room; at `schema_version: 2` it reaches the dispatch, which v1
     never did.
   - `kind: human` is reserved for an action only an external person or outside
     system can perform: merge a PR, trigger CI, register or change
     infrastructure, provide external sign-off, or perform a release **a person
     actually performs**. It never represents your own review, acceptance,
     validation, or integration decision. Two nodes are the same modeling error:
     one you would attest yourself, and one parked on a release — releases on this
     host are automated, so waiting on one is a read you do rather than a node.
     Keep the node only when the action is genuinely external; otherwise perform
     that coordination live with no node. See [Node
     shapes](docs/orchestration.md#node-shapes).

   Where the plan cuts at a contract seam, get **explicit user approval on that
   contract** before dispatch — the route plus request and response fields and
   types, the exact signature, or the field name, type, and default. It is then
   fixed for the run, and a worker that later proposes a departure from it is
   yours to decide: amend it by live edit, or defer it as a follow-up.

   Send a plan back to its planner rather than repairing it yourself when the
   repair is decomposition. Fixing it in place is the same over-reach as planning
   it yourself, and it lands work no plan-quality judge ever reviewed.

   **A plan you wrote or tweaked yourself is one nothing has reviewed**, and `just
   check-plan` refuses it by name until `just review-plan <source:project>` has read
   its authored content against `personas/planner.yaml`'s bar. That refusal is the
   whole of the answer to "I already know what this node has to do": the two plans it
   exists to catch were both written under time pressure, both produced finished,
   gate-green work, and both had that work failed by a judge for satisfying the
   repository instead of the criterion. A plan a planner wrote and you did not touch
   is already recorded and costs nothing — see [Command
   surface](#command-surface).
5. **Pick or create personas.** Each node names a general role and review bar in
   `personas/`. Prefer precise task prose — a specific `## Acceptance criteria`
   list — over encoding subtask details in a new persona, and note that there is no
   test-only persona: a node whose whole job is closing a pre-existing coverage gap
   uses `engineer`. A node's bare `persona` is a *name* resolved against roles built
   into the tool, not against this repository's `personas/` directory, and
   `oneagentgraph` builds in exactly five: `docs-writer`, `engineer`, `planner`,
   `researcher`, and `reviewer`. Every one of them is reviewed under
   `config/onejudge.base.yaml`'s shared acceptance-criteria clause *and* its own
   built-in bar — but those are **two different fields**, and reading them as one
   sentence is what hid the field this host deleted. The shared clause is
   `user.done_when`: a role that declares its own is enforced alongside it, as `Both of
   these must hold:`, and replaces it only by declaring `user.done_when_replaces_base`,
   which none of the five does. Only three declare one at all — `planner`, `researcher`,
   and `reviewer` — so an `engineer` or `docs-writer` node's completion bar is the shared
   clause and nothing else. The role's own review contract is the other field,
   `user.persona`, and that one is **replaced** outright and never merged, by a **bare
   built-in name** exactly as by a file in `personas/`. Which is why the base config
   carries no `user.persona` at all: whatever was written there reached no dispatch. See
   [Personas and the base config](#personas-and-the-base-config). Any other name is read
   as a path relative to `graphs/`, so a repo-specific persona cannot be dispatched by
   its bare catalog name — it dispatches when named as a *path*
   (`../personas/crozier/crozier-corpus.yaml`) — and neither `orchestrator` nor
   `check-in` is built in at all, which is why the dag-scope graph names both by path.
   All re-measured on the adopted stack; see
   [Which of these files a dispatch actually reads](personas/README.md#which-of-these-files-a-dispatch-actually-reads).
   Because that bar is upstream's, this host has **no lever over it**: amend an
   engineer node's review bar in its own `task`, not in `personas/`. `engineer.yaml`
   was deleted rather than kept for exactly that reason — it read like the bar and was
   read by nothing — and `just check-plan` now resolves each node's real bar out of the
   `oneagentgraph` `onepipeline` links and refuses a task whose criteria are silent
   about a demand it makes.
6. **Launch and supervise.** Before a lifecycle run, use `just repos` to confirm
   its registered identity and available checkout aliases, and `onevcs rules check
   <repo>` for its resolved publication and approvals — `just repos`'s type,
   workflow, and gate columns are not the routing. Durable routing is the **rules
   file**'s: a rule matches a repository by pattern and names the publication
   policy and approvals that follow, so a routing change is an edit to
   `config/onevcs.rules.yml` plus `just repos-apply` rather than a command that
   writes a policy. Change it there rather than reaching for an accidental run-only
   override. **A rule names no verifier, because this host runs none.** Since onevcs
   0.11.0 the merge path is the whole of the verification: the host's required checks
   for a remote identity, the `pre-push` hook for a local one. Those
   required checks are inventoried per identity in `config/merge-path-checks.json`,
   so a new identity gets a rule *and* an entry there. Run `just repos
   --audit-gate-coverage` before relying on that verification: it names, per
   identity, every required check on its merge path, each of which can refuse the
   merge — and it reports an identity it cannot classify as unknown rather than as
   covered. Keep missing and unknown coverage visible.

   Start the graph with `just orchestrate <source:project>`,
   which stays attached and hands the run back when it settles (see below), and
   arm a watch on it before you turn to anything else,
   then review each structured boundary and mid-run proposal surfaced by the
   orchestrator. Issue valid live edits when the running frontier should change;
   workers propose but never edit. You review each settled node over the live
   channel and issue `add` / `retry` / `drop` / `reparent` edits. An accepted edit
   needs no carrying forward: [the graph of record is the live
   graph](docs/orchestration.md#the-graph-of-record-is-the-live-graph), projected
   from the run's own journal rather than re-read from the launch file, so a
   retry's replacement id, a branch pin, an amended `task` — which is how a node's
   review bar is amended — or `max_turns` are simply what is executing. What you
   learn about a node that keeps running belongs in a `context` edit, and that edit
   reaches the dispatch **already running** — its `deliver` mode is `auto` unless you
   say otherwise, which interrupts the live turn where there is one and falls through
   to the next dispatch where there is not. Use `deliver: live` when the correction
   cannot wait: it is refused, naming why, rather than deferred in silence. A note the
   running turn read is spent; a note that merely rode a dispatch lasts exactly that
   one, so state worth keeping is state attached again. See
   [Carried planner context](docs/orchestration.md#carried-planner-context).

   **Which lever binds a node's judge, and which only steers its worker.** A node is
   judged against `config/onejudge.base.yaml`'s shared clause, its resolved role, and
   its own `task` — so the only text that changes what "done" means for it is the
   `task`, and on the engine `config/onepipeline.version` names, the only edits that
   reach one mid-run are `amend`, which replaces the node's binding amendment for its
   next and later dispatches, `retry`, whose replacement node states the amended task,
   and `cancel` plus a `requeue` whose `amend` is merged onto the node before it is
   redispatched. `amend` does not interrupt a dispatch already running, whose effective
   task was composed before the ruling existed; cancel or retry when the running worker
   must be judged against the new bar. A `context` note is the other lever
   and it binds nothing. A carried one is rendered
   into the task under `## Planner context`, above the engine's own sentence
   *This reports observed state and adds no acceptance criteria.* — and a
   `deliver: live` note is an interrupt against the running turn that is stored
   nowhere, so it reaches neither that node's judge nor its next dispatch. Amend
   whenever the correction changes what the finished tree must contain or what "done"
   means; send a note for anything the judge has no opinion
   about — which sentinel to poll, which run to measure, that a diagnostic is
   unbounded, that a retry reason is stale. Getting that backwards costs the dispatch:
   a manager ruled a change out of scope at 15:50:23Z, the worker complied and re-ran
   its complete gate green, and the node's own judge instructed at 15:57:19Z to
   **restore** it — reviewing against a task that never mentioned the ruling. The
   worker then held two instructions of equal authority, and resolving it took a
   `retry` with an amended `task`, killing a live gate-green dispatch in order to
   change its bar.

   **An amendment is criteria, so it is held to what criteria are held to.**
   `personas/planner.yaml` tells a planner to *"State the outcome the node owes, never
   the procedure for reaching it"*, and that rule is not the planner's alone: an
   amendment written mid-run under time pressure is the one most likely to name a
   mechanism, and a judge cannot tell a mechanism you preferred from a property the
   node owes. `adopt-oneharness-cli-2` settled `task-failed` on a **green** complete
   gate for exactly that: the amendment asked it to assert that
   `scripts/oneharness-agent.sh` *refuses* an invalid inherited label, and the
   implementation drops the offending pair and continues — which is better, refusing
   would kill a dispatch over a malformed label, and dropping is what
   `orchestrator.labels.parse_labels` already does with an inherited value. The judge
   was right about the task and the task was wrong. So say what the finished tree must
   contain and leave the mechanism to the worker. Put the amendment **above** the
   operational notes the task carries — those open at `## Additional info`, and
   `config/dispatch-appendix.md` is their one source — and open it by saying that
   where it and the notes below it disagree, the amendment wins. Both supervisory
   conflicts in that session traced to an instruction's authority being written down
   nowhere.

   **A green run proves nothing about the plan store**, so do not read a settled
   board as the record of what happened. Each settlement is *projected* back to the
   source project on a best-effort worker: it preserves the description an operator
   authored there and refuses rather than defaulting when it cannot read the
   destination, but a projection that never landed settles the run exactly like one
   that did, and the only thing that says so is one line on the driver's own stderr —
   which a detached run writes to a log nobody opens. The engine's own `just results`,
   `just status`, and the run journal are the record; see [what the write-back owns,
   and what it does not
   guarantee](docs/orchestration.md#what-the-write-back-owns-and-what-a-green-run-proves).

   Triage follow-ups, keep the user informed at
   each milestone, and never let more than 30 minutes pass between updates. When
   a completed task published a PR, include the relevant PR link in its completion
   report. Require verified publication closeout before issuing `complete`. A run
   the progress views report as `PARKED` is alive and not working — no child process,
   no surface, no ledger write — so treat it as stopped and intervene rather than
   waiting on it; that liveness verdict is unrelated to a node you *parked*
   with `cancel`, which is a deliberate idle. A run whose *driver* is dead but whose
   ledger is intact is not lost and must not be relaunched under a new id: `just
   orchestrate --adopt <run-id>` attaches a fresh driver to it, keeping the run
   id, journal, ledger, and anchors, and refuses another session's run and one
   something is still driving — see [Adopting a run whose driver
   died](docs/orchestration.md#adopting-a-run-whose-driver-died). While the run
   works, the engine surfaces a **non-blocking** update for a dispatch that has
   recorded nothing past its stall threshold, and the monitor surfaces what it sees
   drifting from the plan; both are evidence to act on rather than verdicts, so
   decide between `cancel`, `retry`, `context`, and letting it run.
   `channel-reply` refuses an edit it
   cannot apply, with the reason, and every edit it accepts reaches the graph; a
   non-zero reply is a rejection to correct, never a command to resend.

   **A supervisory finding earns a check, never an action**, and being grounded is
   what makes one worth checking rather than what makes it right. Two in one night
   were grounded exactly as the rules ask — one quoted a diff, one quoted a
   criterion — and both were locally true and globally wrong, each missing a fact
   one level out: the first called a committed rename residue from a timing probe
   when it was the worker clearing a judged-tier finding raised against its own
   branch, and the second called a declaration edit unnecessary when the file says,
   twelve lines above the pin, that every pin in that block is declared at the
   newest release the registry carries. One `git show` of a commit message and one
   `grep` of the surrounding comment answered them, under a minute each; acting on
   either would have told a nearly-finished dispatch to undo correct work. Neither
   was noise and neither should have gone unraised — a tier that raised only what
   it was certain of would raise almost nothing — so the asymmetry is the whole
   rule: the check costs a minute, and a wrong instruction to a live worker costs
   the dispatch.

   **A finding you decline on the merits is evidence about the node's bar, not only
   about the worker.** Declining the second of those left its criterion standing —
   *"`Cargo.toml`'s requirements are changed only if the bump actually requires
   it"*, a mechanism the manager preferred, contradicting a convention the target
   repository states in the file itself — and the criterion is what the judge
   reads. Forty minutes later that node settled `task-failed` on a green complete
   gate while its judge's own `completion_reason` recorded that every acceptance
   criterion was met. A `context` note cannot reach that, for the reason the
   amendment paragraph above gives: `amend` binds the next dispatch without interrupting
   the current one, while `retry` with a replacement task or a `cancel` plus a `requeue`
   carrying an `amend` makes the changed bar govern newly dispatched work. So read a disagreement with a finding as a question about your own
   task, and answer it before the judge does.

   **One execution path per deliverable.** When a path fails, diagnose and fix that
   path or escalate to the operator with evidence; never launch a duplicate parallel
   path for the same deliverable — a manager-driven integrate or recovery beside a
   live node delivering it counts as a duplicate — without explicit operator
   approval. `cancel` is the tool for idling a redundant or misdirected node and
   `requeue` for resuming it; see [Parking a node, and picking it up
   again](docs/orchestration.md#parking-a-node-and-picking-it-up-again).

### Never let dispatched work run unwatched

This is the most critical rule of the arrangement. Dispatched work runs for hours
after the turn that launched it; with nothing watching, the project runs
unsupervised and the user is in the dark. It is an **invariant, not a mechanism**,
and more than one mechanism satisfies it:

1. **A watch is armed before you turn to anything else** — not after the next
   step, not when convenient. A launch is not finished until its watch is up.
2. **The watch emits on every terminal state, not just the happy path**: a
   blocking surface waiting, a worker gone quiet, `DRIVER DEAD`, `UNDRIVEN`,
   `PARKED`, and settlement. Silence must never be indistinguishable from
   progress — a watch that greps only for success is silent through a crashloop,
   and that silence reads exactly like work in flight.
3. **A foreground attach alone is not an armed watch.** It dies with the turn that
   started it. Run the attached launch **plus** an independent watch, neither
   depending on the other.
4. **Any mechanism meeting 1-3 is acceptable** — a `just channel-next` loop, an
   out-of-band poll on `just status` that emits on state change, a scheduled
   wake-up. The invariant is what is named here; the tool is yours to pick.
5. **The watch must emit on the unread-surface line specifically.** This is a
   HARD REQUIREMENT: the `N planner update(s) waiting (…kinds…), unread for T` line
   that `just runs` and `just status` add per affected run has to reach you, and
   filtering it out as noise is
   forbidden. A manager watch that filtered it went silent while 26 updates queued
   and one blocking question was asked three times, with every other indicator
   green throughout. A blocking surface produces no other signal until it is read
   — the run reports plain `ACTIVE`, never `awaiting-planner` — so dropping that
   one line removes the whole question channel invisibly.
6. **A grep over the whole of `just status` is watching two subjects at once.**
   That view is two documents in one stream: the run's own lines, then
   `oneagentgraph health`'s JSON, which describes the **host** and not the run —
   three identities this host has never configured carry `"reason":
   "no_plan_quota"`, and a Copilot line reads `no GitHub token to read Copilot
   quota with`. A watch matching `quota` anywhere in that output reported
   `terminal: quota` eleven seconds into a healthy dispatch. Cut at the boundary
   before you look for words in it —
   `just status "$RUN" 2>&1 | sed '/^  providers:/,$d'` — and cut once, in one
   snapshot the whole watch reads, rather than at each grep that reads it. The
   reusable half is that **a view embedding another tool's report is not a
   line-oriented document, and a grep over the whole of it matches two subjects at
   once**. This is rule 2's converse and it costs the same thing: the words a false
   match fires on — `quota`, `failed`, `refused` — are the words a real death is
   reported in, so the first firing spends the correct instinct on nothing and the
   second weakens it. Rule 5 survives the cut, because that block is the last thing
   the view prints and the unread-surface line is above it.

### Answering on the channel

Two measured channel defects make reply discipline part of the job rather than a
detail:

- **Read `runs/<run-id>/channel/queue.json` before replying**, and confirm the
  `pending` surface is the one being answered. A reply binds to whatever is
  pending at that instant, not to the surface you just read. What it binds to is
  decided by the halves it carries: a verdict answers the pending surface, a
  commands-only envelope answers nothing and leaves it standing, and one carrying
  both does both. So an edit issued while a question is waiting no longer consumes
  that question — and no longer kills the monitor either.
- **A blocking surface may have no asker.** A `channel serve` that timed out exits
  without withdrawing its surface, so `awaiting-planner` does not prove anybody is
  still waiting for the answer.

**A blocking surface is handed out first, and reading past it no longer clears
it.** `just channel-next` hands out any blocking surface ahead of every
non-blocking one, however much older those are, so a worker's question can no
longer sit behind a pile of observations. And once one is pending, reading the
non-blocking surfaces behind it leaves it pending: only a *verdict* answers it. So
a manager who reads a queue down does not accidentally consume the question, and
`pending` staying put across several reads is the mechanism working rather than a
stuck queue. Measured on a real run: with a blocking surface queued fourth behind
three older non-blocking ones, the first read handed out the blocking one, and
three further reads handed out the others while `pending` stayed on it throughout.
Nothing changed about what a queue-jumping surface *is*: everything the monitor and
the pacemaker raise is still non-blocking by construction.
- **Steer a running dispatch with a `context` edit, never with `oneagentgraph
  interrupt` by hand.** These are not alternatives: a `context` edit *is* an
  interrupt against that dispatch's control socket, wrapped so the lever's own events
  reach the run's journal stamped with the node. An interrupt is not journalled, which
  leaves the run's own record unable to explain why a worker changed direction — so
  prefer the edit because it records what the raw verb does not, and reach for
  `deliver: live` when the correction cannot wait, since its refusal tells you the note
  did not land instead of leaving you to assume it did. See
  [Carried planner context](docs/orchestration.md#carried-planner-context).

After `just orchestrate`, the manager uses **only** `just channel-next`, `just
channel-reply`, `just stop`, and the read-only `just monitor` / `just runs` /
`just status` views. `channel-reply` carries both legacy verdicts and [versioned
live edits](docs/orchestration.md#live-graph-edits). There is no verb that advances
a run, so there is nothing left for the manager to drive: the engine reconciles
continuously and two writers would race the ledger lock anyway.

`just channel-next` and `just monitor` read through the `planner`
[profile](docs/orchestration.md#read-profiles) — the pipeline's own decisions and
settlements, not every worker's turns. That narrowing is the point: the detail is
the monitor's to read, through `--filter monitor`, and the same flag is here when
you want it. `--all` bypasses profiles entirely.

`orchestrate` **stays attached by default**: it prints the launch record, streams
the run's merged events, and returns when the run **settles** — the
graph completed, a blocking planner surface is waiting on you, or nothing is
driving the run any more (exit 3, and the state to intervene in). Ctrl-C detaches
without stopping the run. Pass `--detach` when a run should go unattended — several
runs supervised at once, where you launch each one and come back to it — and
`just monitor <run-id>` re-attaches to any of them, streaming the same events
without the settle-and-return contract the foreground launch has. Do **not**
background a launch by hand to watch it; that is
what the foreground default replaced. Rebuilding run state
from `events.jsonl`, `ps`, or `git log` in a run clone instead is the omission
the read-only views now prevent: `just runs` and `just status` name how many
surfaces are unread, **which kinds** they are, how stale the oldest is, and the read
that consumes them, so an update nobody read can no longer hide behind a row that
says only `ACTIVE`. The command they name is the published verb — `onepipeline next
<run>`, which is what `just channel-next <run>` calls — and the kind breakdown is
what separates a pile of `monitor` narration from the one `finding` inside it. Rendering a
surface in `monitor` is not reading it — only `channel-next` consumes one.

**Runs are owned.** Several managers share this host, each supervising its own
workstreams, so a run belongs to the session that launched it. `just orchestrate`
records that session automatically and `just runs` shows it per row: `[mine]`, the
owning session (`[claude-code:3f9a1c2e]`), or `[unknown]`. `just runs --mine` lists
only yours. Act **only** on runs you launched. A run you cannot attribute belongs to
another manager until proven otherwise — `unknown` is never yours, and a run
launched before its session was recorded stays `unknown` forever. Never derive a
process list from `ps` and signal it: that pattern knows nothing about whose work it
matched, and it has already interrupted another manager mid-supervision here.
`just stop <run-id>` is the supported way to stop a run; it refuses another
manager's run and an unattributable one, naming the owner, and `--force` reports
that owner before overriding. `just orchestrate --adopt <run-id>` attaches a fresh driver
to its intact ledger. `complete` is a completion
verdict on the channel and deliberately does **not** stop scheduling; use `just
stop` when a run must actually end. A stopped run is left reclaimable exactly as a
run whose driver died is. `stop` is deliberately **not** in
`.claude/settings.json`'s allowlist: it ends live work, and `--force` overrides the
ownership check the incident above is about, so each one is approved on its own.

An agent-written, non-blocking per-workstream status also arrives when the durable
planner-update pacemaker becomes due (30 minutes by default). That pacemaker is the
`check-in` member of `graphs/dag-scope.yaml`, and it carries its own `task` that
opens with `{task}` — which is why that document declares schema 4. The task is what
keeps the member reporting rather than editing: `onepipeline` composes one task for
the graph and `oneagentgraph` gives it to every member that claims none, so a member
whose job is not the run-level task must state its own — and must interpolate the
composed one back in, because that composed task is this graph's own way of naming
the run. The environment names it too, as `ONEPIPELINE_RUN_ID` — measured against
onepipeline 0.17.5 on a real launch and gated in `tests/e2e/` — but that is a
per-release export rather than a contract, so members here are written against
`{task}`. Never let this one reach `onepipeline
reply`; live edits belong to the `monitor` member, which stays for the whole run,
and to you. Every planner-visible surface resets that clock. The interval is set
once, at launch, with `just orchestrate ... --heartbeat-interval SECONDS`, and
there is no way to change it afterwards: the reply envelope `just channel-reply`
sends is closed to unknown fields and accepts exactly `version`, `author`,
`completion`, `message`, `reason`, and `commands`, so a reply carrying
`"heartbeat_interval"` is refused whole and its verdict and graph edits go with it.
`--heartbeat-interval` is on `onepipeline start` alone — not on `adopt` either — so
choose the interval when launching, and relaunch rather than expecting to retune a
live run. The run continues without waiting for a reply to these surfaces.

Judge a dispatched branch against its own base (`merge-base` / `base..branch`),
never a moving `origin/main`; concurrent advancement can make a healthy branch
appear to delete files.

Treat unresolved same-identity dependencies as stack prerequisites, not merely
scheduling edges, and preserve them across replans until their content reaches
the root base. The deterministic mechanics live in `docs/repo-lifecycle.md`.

Accuracy and quality come first; saving time or tokens never relaxes their bar.
Subject to that, minimize both.

The manager owns the user conversation, the brief, review decisions, human-action
attestation, and the watch. The planner owns decomposition, contracts, persona
choice, and task authoring — its judgment is in
[`personas/planner.yaml`](personas/planner.yaml), and the operational half of it
under [Decomposition and
scheduling](docs/orchestration.md#decomposition-and-scheduling). The engine owns
scheduling, ledger writes,
integration of finished work, and publication closeout. The monitor owns noticing —
and, where a fix is unambiguous and inside its
[allowlist](docs/orchestration.md#who-issued-an-edit-and-what-that-bounds), applying
it. A finding it or the pacemaker calls a **rule violation** quotes the file and line
that rule comes from; what neither can ground that way is an **observation**, and its
own supervisor sends it back until it is one. See [A finding names the rule it is
grounded in](docs/orchestration.md#a-finding-names-the-rule-it-is-grounded-in).
**A quiet monitor is a working monitor**, and this is the one place the watch
invariant below does not apply: both supervisory roles are now told to report findings
rather than intentions, because every turn they produce prose on becomes a planner
update. One run queued twenty-eight, twenty-four of them content-free — fourteen
variants of "I'll identify the active run, then attach to its detailed stream" — and a
worker's blocking question sat unread behind that pile for fifteen minutes with the
frontier stopped. So read an absence of surfaces as an absence of findings, and read the
run's own state for whether anything is watching.

**The monitor says its silence rather than performing it**, and the difference is the
whole supervisory tier. A turn with no finding in it replies with exactly `NOTHING TO
REPORT` and nothing else; `scripts/channel-serve.py` raises no surface for that, queues
nothing for anybody to read, and answers onejudge with a non-completion the member goes
on watching after. Producing **no** output at all is a different answer and still fails,
because from outside it is indistinguishable from a turn the agent side lost, which is a
real provider defect worth reporting — its refusal names the sentinel so the two are
told apart at the point of refusal rather than by reading this file. Until the filter
learned that difference the documented correct behaviour was fatal on the **first**
quiet turn, which for a healthy run is usually the first turn:
`spanish-language-tutor-upgrade` lost its observer five minutes in and ran roughly two
hours at `OBSERVER DEAD` while every other indicator stayed green, because a run with no
observer still reports plain `ACTIVE` and the loss announces itself nowhere.
`personas/orchestrator.yaml` states the sentinel in the same paragraph that tells the
monitor to stay quiet, so the instruction and the contract cannot drift apart, and
`tests/e2e/test_monitor_quiet_turn_e2e.py` drives all three answers through the real
filter onto a real published channel.

**That monitor is a conversation because a conversation is what keeps the observer graph
alive**, and the repair its early settlement invites is refused by the pinned reader.
Making it a scheduled single-sided member — the `check-in` pacemaker's shape, which no
completion bar is scored against — leaves no member outside the schedules, and
`oneagentgraph` refuses that document outright; the all-`start_after: 0` shape its
refusal offers instead loads and then settles the whole observer graph after one turn
per member, with nothing relaunching it. So **the pacemaker does not survive because of
its kind**: it survives because it fires inside the monitor's conversation, and a run
whose monitor has settled gets no pacemaker update either. What ends that conversation
is onejudge settling a repeated no-op exchange, which a quiet `NOTHING TO REPORT`
satisfies by construction — the same sentinel that stopped the flooding. The evidence is
this host's own 89 recorded pacemaker settlements, and they split 50/39. The fifty taken
under a two-member observer document — the shipped `graphs/dag-scope.yaml`, and the older
revision spelling the same member `orchestrator` — each fired while that conversation was
live, the two that settled after it died being turns already in flight when the graph
tore down under them. The thirty-nine taken under a one-member document with **no**
conversation member at all each took the whole observer graph down with them, in 0.100s
to 0.112s: not an exception to the rule but the directly observed form of the failure the
scheduled repair would introduce, already run thirty-nine times on this host by accident.
The measurements, the reader's own words, and the two upstream changes
that would lift the constraint are in [The observer graph is alive only while one of its
members
is](docs/orchestration.md#the-observer-graph-is-alive-only-while-one-of-its-members-is),
held by `tests/e2e/test_observer_graph_liveness_e2e.py`.

Both supervisory roles are also required to hand a
surface's text to the engine as **bytes** — the `finding` op in a reply envelope,
or the `surface` verb reading a single-quoted heredoc off stdin — rather than as a
command-line word: bash substitutes backticks and `$(...)` inside double quotes,
and a finding that quoted a command ran it, twenty-five minutes of this host's CPU
with the surface's own text mutating into that command's output. That incident is
why the form is what it is, and it is now closed at the verb rather than by a
quoting rule an agent has to get right: the text is read from a file the verb is
named, or from stdin when it is named none, and the inline `--message` is kept only
as the form for text a person typed. **The monitor's structured way of reporting is
the `finding` op**, which is on its allowlist beside `retry`, `requeue`, `cancel`,
`context`, and `add`: it carries a required non-empty `message`, an optional `id`
naming a node the graph has, and a `blocking` flag defaulting to false. It compiles
to a `finding-raised` operation that mutates no graph, and it is the one op that
raises no second `monitor applied an edit` surface beside the finding itself — so a
finding arrives once rather than twice.

**One supervisory observation is open, and is recorded so the next occurrence is
recognised rather than re-diagnosed.** A manager `context` edit delivered at the default
`auto` interrupted a live turn; `turn-activity` journalling then showed member
heartbeats only, for about eleven minutes, while that turn kept running. Measured in
that dispatch's own worktree at the time: a fresh commit, a clean tree, a filling
`.logs/nx.log` and a live `nx run-many` — while the views reported five events eleven
minutes old and the run's own monitor escalated to "not progressing toward its
acceptance criteria". The **leading candidate explanation** is onepipeline #127, merged
and queued for release in that repository's open change request #124, which fixes
reconcile-loop starvation in which relayed envelopes drained one per pass at roughly
3.8 s each and held settlement behind them. Read that as a candidate and not as the
cause: the mechanism is not isolated, and nothing here ties the interrupt to the
starvation. The interim guidance is the part to act on — **when a dispatch looks silent,
read its worktree before believing the event stream**: its commits, its tree, and
whichever `.logs/<label>.log` its innermost stage is writing say what the stream cannot,
and a supervisor that escalated on the stream alone escalated wrongly here.

None of these roles authors target-project content; dispatch implementation and
research to workers.

Require each worker to prove its own change with the checks that exercise it — the
tests over what it touched, the lint over its diff — not with `just gate`, which this
host stopped asking a dispatch to run. Review the evidence it surfaces rather than a
judge verdict alone; relevant checks must not have skipped.

**So the merge path is what refuses a bad branch now, and `checks-failed` is the
ordinary way that arrives.** A worker that ran less will sometimes hand over a branch
its repository's own bar then rejects, and the engine dispatches that node again onto
the same branch with the failure's reason — three attempts by default — before settling
on one of the five retryable failure words. Read one as the system working rather than
as a worker that misbehaved: what it costs is a retry on a branch that already exists,
against what a dispatch used to spend twenty to forty minutes of every node learning in
advance. What it does not license is a settled `checks-failed` left unread; that is
still a branch carrying a tree the merge path would not pass, and still yours to look
at.

## Personas and the base config

A persona is a small onejudge **delta** file in `personas/` — the agent's general
role (`system_prompt`) plus the supervisor's review bar (`persona`). General
cross-repo roles are top-level files; repo-specific roles live under
`personas/<repo>/` and are dispatched with `<repo>/<name>`.
Common settings live once in `config/onejudge.base.yaml` (the base config);
`dispatch` merges base ⊕ persona ⊕ the CLI `--task` into one effective config and
runs `onejudge run` on it. Keep subtask-specificity in the node controls described
above; add a persona only for a genuinely distinct role or review bar. A dedicated
`reviewer` is reserved for complex DAGs where one agent reviews and integrates
several agents' independently produced work, since the simulated-user supervisor
already reviews every dispatch.

**That supervisor's authority was bounded in a field no dispatch ever read, and the
field is gone.** `config/onejudge.base.yaml` carried a `user.persona` saying the
simulated user verifies against the criteria the task states and stops there: that it
does not issue rulings on the manager's behalf, redirect the dispatch onto other work, or
direct a handoff to another dispatch. It said none of that until two incidents in one
session did all three — a planner's simulated user composed a four-point "manager ruling"
in-conversation and instructed the worker to post it over the run channel, and the worker
complied; a worker's simulated user redirected a dispatch off its stated task onto
unrelated lint findings and then told it to stop work and report the finding to an owning
dispatch that did not exist. A simulated user that issues rulings is a second manager the
real one cannot see, and a worker holding two instructions of equal authority resolves it
by guessing.

**But `user.persona` is replaced rather than merged, and every dispatch replaces it.** A
bare persona name resolves to a role built into the tool and replaces it; a path resolves
to a file and replaces it; a node names one or the other. So the bound applied to nothing
from the day it was written, and nobody noticed for as long as it existed — this host was
operating as though a protection were in force that never was. The field is therefore
deleted rather than left standing: one that reads like a bound and reaches no dispatch is
worse than no field at all, because it is the kind a manager stops checking. Saying that
plainly is worth more than the sentence was. What a dispatch does share is
`user.done_when`, which merges, and a role that needs this bound states it in its own
review contract or the node's `task` states it. `tests/test_shared_dispatch_bar.py` holds
the absence, and the two incidents may in any case be fully explained by the upstream
context corruption fixed in onejudge 0.5.2 and oneharness 0.11.0, both in force here.

Draft a new role under gitignored `scratch/personas/`, dispatch against that
directory, and refine it from observed performance. Once proven, dispatch its
addition through this repo's isolated self-lifecycle; `just new-persona <name>`
scaffolds the tracked file and `just check` validates it. General personas stay
flat; repo-specific personas use slash-qualified names and subdirectories
(`crozier/crozier-corpus` maps to `personas/crozier/crozier-corpus.yaml`).

## The two sides of the conversation

This repository adopts the exact onejudge version declared in
`config/onejudge.version`, and session setup installs the pinned PyPI
`onejudge` distribution and verifies both its `onejudge_sdk` import and matching
CLI wheel. Dispatch calls the typed Python SDK, which drives and validates the
real CLI. onejudge drives a
two-party conversation, and harness/model selection for each
side lives in oneharness config, not onejudge:

**Every role names the same five identities** — `claude-code:alternate`,
`claude-code:alternate2`, `codex`, `codex:alternate`, `claude-code:primary` — and
they differ only in order. A role that omitted one would lose that quota entirely
once everything ahead of it was exhausted, which is the failure this arrangement
exists to prevent. The primary Claude identity is last everywhere:

- **Agent side** (does the work) — `oneharness.toml`, discovered from the repo root;
  it prefers both alternate Claude subscriptions, in order, because the personas
  are tuned against that model tier, and only then falls back to codex. Every agent
  turn also carries the normalized tool transcript, selected by
  `scripts/oneharness-agent.sh` because it is a `run` flag with no config key: the
  planner-visible views already build `just status`'s `Commands:` line and `just
  history-show`'s detail from each record's `events`, and claude-code's default
  output format carries no tool transcript at all, so those were empty for every
  claude-code dispatch. A dispatched turn takes **`--stream`**, which delivers those
  same events *as they occur* and implies `--events`' format selection; `--events`
  alone says only that the transcript is in the report at the end, which left a node
  invisible for the 600-2000 seconds a turn runs here. Streaming a
  `run_mode = "fallback"` chain has been allowed since oneharness 0.6.5, so nothing
  is traded for it. See [Streaming the agent
  side](docs/onejudge-integration.md#streaming-the-agent-side) for the filter that
  reconciles a stream with onejudge's single-report contract, the probe that decides,
  and why `--events` remains the degrade path.
- **Judge / simulated-user side** (supervises) — `oneharness.judge.toml`, passed
  as the base config's `provider.judge_config`; codex first, then the alternate
  Claude subscriptions. It keeps its cheaper-supervisor intent through `model`
  (`claude-sonnet-5` for all three of its Claude variants) rather than by staying
  off those subscriptions.
- **Monitor side** (watches a tracked graph) — `oneharness.orchestrator.toml`,
  named by `graphs/dag-scope.yaml`'s `monitor` member as its **agent** side. That
  member's judge side is not a harness config at all: it is the live manager, over
  `scripts/channel-serve.py`, so replying to a monitor surface steers its next turn.
  Deliberately the reverse of the worker order: both codex identities carry the
  role first, so this long-lived supervisory process does not queue ahead of the
  workers while Codex can still run it. It is also the **one** side here with no
  per-turn deadline (`timeout = 0`), because it watches for as long as the run
  lasts and a deadline would end the watching.
- **Pacemaker side** (the `check-in` planner update) — `oneharness.check-in.toml`,
  the monitor's routing verbatim with a finite deadline. It is a separate file
  for exactly one reason, and re-merging the two is a silent regression: see
  [Choosing a deadline per
  side](docs/onejudge-integration.md#choosing-a-deadline-per-side).
- **Drafting side** (the `pr-author` body a change request opens under) —
  `oneharness.pr-author.toml`, named by `graphs/pr-author.yaml`'s one member as its
  **agent** side; it has no judge side, because the review is a JSON Schema. The same
  supervisory order and a third copy of it, for the same per-deadline reason the
  pacemaker's is a copy: a drafter sits between a finished branch and its publication, so it
  keeps a finite `timeout`. It is also the one side here with `stream = false`, which
  is forced rather than chosen — oneharness validates a structured answer against the
  complete response, so `stream = true` and `schema_file` cannot both hold.
- **LLM lint side** — `oneharness.llmlint.toml`, forced by
  `scripts/llmlint-oneharness.sh`; the same supervisory order. It is **no longer
  codex-only**.

The judge and llmlint reaching the workers' subscriptions at all is the operator's
deliberate trade: those tiers can now contend for that Claude quota, and that is
accepted because a supervisory tier that can still run beats one isolated from the
quota that is left. Do not reorder these to restore the old isolation.

**Streaming and out-of-band turn control are independent, and a control failure is
never a reason to stop streaming.** `--stream` decides *when* a turn's transcript
arrives; `--control` opens a socket a separate process can interrupt the live turn
over. Both are supported together on a multi-identity fallback chain as of oneharness
0.8.0, whatever harness families it mixes: 0.7.2 stopped the control validator
counting *candidates* where it meant concurrent turns, and 0.8.0 stopped it demanding
one turn-control mechanism across the whole chain, binding the mechanism to the
candidate that serves the turn instead. That was the constraint that bit here — every
chain mixes claude-code with codex, which declare different mechanisms — so the
committed chains are now planned with each candidate on its own. The other constraint
was the socket **address**, capped at 108 bytes on Linux; oneharness 0.10.0 bounds it
at construction, and a real launch on the adopted stack settles a dispatched worker
whose report names a bound control session with `control_unavailable` null. So turn
control is live on every dispatch here rather than held in reserve — at an address
measured 107 bytes long, which is the whole budget.
Turning a member's `stream` off to dodge a control refusal trades away the per-turn
visibility a manager supervises with and fixes nothing; that workaround was written
against this repository and rejected, and no member carries a `stream` key today. See
[Streaming and turn control are independent
concerns](docs/onejudge-integration.md#streaming-and-turn-control-are-independent-concerns).

Those files decide the defaults for every run on this host. Pairing the two sides
differently for **one** run is a property of that run's agent graph: pass
`--node-set members.worker.agent.oneharness_config=REF` and the corresponding
`members.worker.judge.oneharness_config=REF` override to `just orchestrate`, where
each referenced config declares the intended identity. Use `--set` instead of
`--node-set` to override a dag-scope member. Do not edit a config concurrent runs
also read.
The adopted graph invokes oneharness directly, and since oneagentgraph 0.2.18 that
is literal rather than loose: a single-sided `kind: oneharness` member — this host's
`check-in` pacemaker — runs its turn through `oneharness_core` on a thread of the
graph process, so **no `oneharness` CLI is spawned for it** and
`ONEAGENTGRAPH_ONEHARNESS_BIN` does not reach it. Every `member-started` on the
adopted stack reports `runner: library`. What is still a process is the provider the
turn selects. `scripts/oneharness-agent.sh` is
still the smoke/manual boundary — the one path that deliberately spawns the CLI — but
its `ORCHESTRATOR_WORKER_HARNESSES` /
`ORCHESTRATOR_JUDGE_HARNESSES` compatibility variables are not on this launch
path. oneharness's `ONEHARNESS_HARNESSES` is process-wide and cannot express a
per-side choice. See
[Choosing a harness per
side](docs/onejudge-integration.md#choosing-a-harness-per-side).

Choosing the identity does not choose the **tier**: every one of those files pins a
`model` per harness, and the judge's three Claude identities are pinned to the
cheaper supervisor model deliberately. The model half of the per-side seam is
graph-native: override `members.worker.agent.model` and
`members.worker.judge.model`, and it applies to whichever candidate the chain
selects — `fallback` does not fall through a task failure, so a model a side's
selected identity rejects dies on that rejection rather than degrading. Note what
the seam does **not** buy — a config's per-harness `model` beats `ONEHARNESS_MODEL`,
so a model exported into the environment is inert against
`oneharness.llmlint.toml`'s own pins; overriding which *identity* that tier judges
on is what changes its model. See [Choosing a model per
side](docs/onejudge-integration.md#choosing-a-model-per-side).

Nor does it choose the **deadline**, and that half of the seam has no graph-native
field at all: a member takes `oneharness_config`, `model`, and `stream`, so the
config file is the only place a per-member `timeout` can live. Since oneharness
0.7.0, absent means no deadline; the worker, judge, and llmlint configs intentionally
take that default. `timeout = 0` also means no deadline and remains explicit in
`oneharness.orchestrator.toml`, because the monitor watches for the life of the run.
That is why the `check-in` pacemaker no longer shares that file: an unbounded
deadline reaching a scheduled member would leave a wedged turn alive forever,
which fails silently, so it reads
`oneharness.check-in.toml` instead. Never point two members at one config to save a
copy, and never set `ONEHARNESS_TIMEOUT` to fix a deadline — it is process-wide for
the whole graph run and beats every file, so it moves every member at once. See
[Choosing a deadline per
side](docs/onejudge-integration.md#choosing-a-deadline-per-side).

`scripts/claude-alt-config-dir.sh` is the one source of **both** alternate config
directories (`ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR` → `$HOME/.claude-alt`,
`ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR` → `$HOME/.claude-alt2`), and all three
wrappers source it, so no role needs either exported by hand and no two can drift
apart. Authenticate the second plan with `CLAUDE_CONFIG_DIR="$HOME/.claude-alt2"
claude`; until then it costs nothing, because claude-code classifies an absent
config directory exactly as it classifies an empty one — `auth`, which falls
through — so unlike the codex helper this one creates nothing. See
[The second alternate Claude
subscription](docs/onejudge-integration.md#the-second-alternate-claude-subscription).

Every one of those chains also names `codex:alternate`, a **second codex
identity** that absorbs an exhausted quota without changing which subscription a
role competes for. `scripts/codex-alt-home.sh` is its one source — it derives
`ORCHESTRATOR_CODEX_ALT_HOME` as `$HOME/.codex-alt` and all three wrappers source
it, because oneharness refuses to start whenever that indirection is unset.
Authenticate it with `CODEX_HOME="$HOME/.codex-alt" codex login`; until then the
candidate costs nothing, since the helper guarantees the directory **exists** and
an empty codex home falls through as `auth` while an absent one hard-fails. That
asymmetry is the whole reason the helper creates it — see
[The second Codex identity](docs/onejudge-integration.md#the-second-codex-identity).

`onejudge init` scaffolds both files plus a starter `onejudge.yaml`. The adopted
exact oneharness release is declared in `config/oneharness.version`, installed as
the `oneharness-cli` PyPI wheel, and verified by `scripts/session-setup.sh`. Session
setup also installs and verifies Bun for oneharness's SDK gate. The
committed configs are that output with auth-variant routing, a cheaper Claude
judge fallback, and the `IS_SANDBOX` env, and
`config/onejudge.base.yaml` supersedes init's starter `onejudge.yaml`. Regenerate
with `onejudge init --force`.

**Live dispatch** picks a harness via `oneharness.toml`'s fallback (alternate
Claude subscription primary, codex secondary).
The lifecycle *and the orchestrator process itself* dispatch in **`bypass`** mode by
default — the no-approval mode. It is correct here because the **whole environment
is a sandbox** (a container):
codex's own `workspace-write` sandbox (`auto` mode) needs unprivileged user
namespaces this host disables, so `bypass` (no approvals, no inner sandbox) is the
working no-approval mode and the container is the boundary. The **allowlister**
`repo-write` hook stays wired (`scripts/session-setup.sh`) as belt-and-suspenders.
Full rationale, the merge strategies, and the claude-code caveat:
`docs/repo-lifecycle.md` and `docs/onejudge-integration.md`.

## Command surface

Use the `just` recipes (`just --list` is the index); do not hand-roll
equivalents. `just bootstrap` sets up from a clean clone (installs the toolchain,
activates the git hooks); `just check` is the deterministic tier, while `just gate`
is the complete pre-push bar: `check` plus the llmlint diff tier. Both report the
line-coverage total they measured, and every stage that captures its output keeps
it at `.logs/<label>.log` (`nx`, `workspace-install`, `python-install`, `check`,
`upgrade`, `gate-check`, `gate-llmlint`) — gitignored, owner-only, credential values redacted, truncated
per run. Each log fills as its own stage runs, so follow the innermost one:
`.logs/nx.log` while the Nx targets run (the long part), `.logs/check.log` for
the stages after them. Read a finished run from the same paths, and never read a
live command through `/proc`. Those paths are safe to tail because the truncation
is per *invocation*, not per path: a run records the log it is writing in the
exported `ORCHESTRATOR_PRESERVED_LOGS` claim list, and a nested run that finds its
path already claimed by a live enclosing one writes `.logs/<label>.<pid>.log`
instead and names that path in its own failure. This matters here because the
suite runs `just lint-llm-diff` against this checkout from inside `just check`.
There are no engine verbs left to invoke: `onepipeline start` drives the DAG to
settlement on its own, so `just orchestrate <source:project>` is the whole launch and
the manager supervises the surfaces and proposals it raises over the [live
channel](docs/orchestration.md#the-planner-channel). There is no single-dispatch
command: one
subtask is a one-task project (`examples:scheduler-research`,
`examples:health-endpoint`), so no running work falls outside the
run ledger and the views built on it.
`just plan <brief.md>` is that same launch for one shape of node: it writes the
one-task project a manager-written brief becomes
(`examples/planner-brief.example.md` → `authoring:<generated-project>`)
and launches it. The brief is the dispatched task verbatim, so it is written in
the `## What` / `## Why` / `## Acceptance criteria` template and refused when it is
not. Two things about that launch are the reason the recipe makes it rather than a
manager: the persona is the **path** `../personas/planner.yaml`, because the bare
name resolves to a role built into the tool and this repository's file is never
read; and the run id it prints is guaranteed to be the run's own, by refusing a name
whose run root is already taken rather than by predicting the `<name>-2`
`onepipeline` would mint instead. That refusal is what lets the recipe also hand
that id to its dispatch as `ONEPIPELINE_RUN_ID`, the run whose channel a blocking
question goes to — without it, a detached planner's questions would queue on a live
run belonging to somebody else's workstream. A third thing about it is a
difference from `just orchestrate` worth knowing before you go looking for a
watcher: **`just plan` names `--dag-graph off`**, so a planning run has no monitor
attached. A monitor watches the run for drift from the plan, and a planning run's
plan is its own output, so there is nothing yet to compare it against; the journal,
the ownership row, the surfaces and the DAG UI place are `onepipeline start`'s own
and arrive either way. A caller who names a graph keeps it, exactly as `just
orchestrate` keeps a caller's own.

**`--direct` is the one flag that changes the dispatched task**, and it is the one
exception to "the brief is the dispatched task verbatim" above. A `--direct` launch
puts its planner in the shared canonical checkout rather than a worktree of its own,
where it may write only to gitignored paths and may not commit — while the shared
completion clause in `config/onejudge.base.yaml` demands every change committed of every
dispatch alike, and a planner that did correct, verified work settled `task-failed`
against exactly that. So the recipe appends the placement *and* the clause it is exempt
from after the brief, which is the only text both that worker and its judge read. The
brief still comes first and untouched; a launch with a `repo` appends nothing.

**`just check-plan <source:project>` reads a project against the bar each node will
actually be judged against**, and is the cheap read to make before launching one. It
refuses a node whose `## Acceptance criteria` name a procedure instead of a property,
and one whose criteria are silent about a demand its resolved bar — or its own
`## Additional info` — makes of it. Both are how correct, gate-green work gets failed
on procedure rather than on its work: the judge reads a demand nobody wrote as a
criterion and supplies its own reading of it, the node settles `failed`, and its
dependents never schedule. Two things it does are worth knowing before trusting or
arguing with it. It resolves a node's `persona` the way a dispatch does — a bare name
is a role compiled into the `oneagentgraph` **`onepipeline` links**, not the pinned
`oneagentgraph` CLI and not `personas/` — so a name nothing ships is refused here
instead of costing a scheduled node, and a demand it reports is one the run's own
judge will make. And it holds every task to `config/dispatch-appendix.md`, this host's
**one source** for the operational text a task's `## Additional info` carries: that
text was gitignored scratch propagated by copy-paste, which is why it contradicted
itself about the complete gate for long enough to fail a node. Rebuild a task's
appendix from that file rather than from an older builder's copy.

**It also refuses the one pairing no wording of the criteria rescues**: a resolved bar
that forbids the dispatch changing project files, under criteria that require a
tracked file to change. The judge is then required to fail the work the task is
required to produce, and that is not hypothetical — three nodes of one plan carried
`persona: researcher` while their tasks were to edit a document, and the first settled
`task-failed` with the judge citing a file that does not exist, costing a run and a
relaunch. `researcher` reads like the right role for a node whose job is measurement,
and its *"no project files were changed"* clause is invisible from `personas/`, which
is not where a bare name resolves; the refusal names the node, that clause, the
criterion that contradicts it, and a shipped role whose bar does not carry it. It
reads both halves out of the resolved bar and the criteria rather than off a list of
persona names, and it is written to **miss** a criterion that names its file in prose
rather than to refuse a sound plan — a false refusal blocks correct work and gets
worked around, which is worse than the gap. **That prediction has since been paid
out**, and how the refusal was worked around is the part to learn. A read-only
research node — no repository, no branch, its deliverable entirely in its completion
report — was refused for a criterion telling its worker to *read*
`.github/pull_request_template.md` in the publication checkout, reported as criteria
that require that file to change. The way past it was to stop naming the path in the
criterion and move it into `## Additional info`, which makes the criterion vaguer to
satisfy a check whose purpose is precision and leaves the worker following a pointer
to learn which file to read. Do not pay that price: the pairing that genuinely cannot
be satisfied is one whose verb **changes** a tracked file, and a criterion whose verb
is a reading verb — read, quote, cite, follow the shape of — is the opposite case.
Launch the plan whose criterion is precise, and treat the refusal as the check being
wrong about a sound node rather than as a wording to soften.

**Two more refusals are about criteria nothing has reviewed rather than criteria a bar
disagrees with.** A criterion never carries a **version literal** — a release number, a
dependency version — because that number perishes between the task being written and
its node being dispatched, and the criterion then fails finished work for doing the
right thing. That one is deterministic *because* a judge would pass it: the criterion
that shipped required a lockfile to resolve a sibling to an exact version, the sibling
published a newer one in between, the worker resolved the newest as that repository's
own manifest demands, and its judge failed a green gate. State the property the version
stood in for. And a task carrying **no review record for what it currently says** is
refused outright, naming each such task and `just review-plan <source:project>`, which
is what records one.

**`just review-plan <source:project>` spends the judged turn that clears a plan's
authored content**, reading each unreviewed task against `personas/planner.yaml`'s own
bar — the judge every planner-written plan already passes through. Detecting *who
typed* a node is the wrong question: it catches the plan an operator wrote by hand and
misses the planner's plan an operator then tweaked, and both of those have shipped
here. What is detected instead is content nothing has reviewed, which covers both with
one rule — a task carries a digest of its own authored content, and a task whose
content does not hash to its record has not been reviewed. Four things about it are
the same properties this repository already defends for its judged lint tier. Only a
**pass** is recorded, so a refusal leaves nothing to replay. A record **is
authoritative** — `check-plan` accepts it and spends no second turn, because a second
opinion on identical content is how one branch comes to hold two opposite verdicts.
There is **no escape hatch**: no flag, no option, no environment variable, because an
escape here is reached under exactly the time pressure that produced both errors. And
the key covers the bar as well as the content, so moving any part of that bar —
`personas/planner.yaml`, the verdict schema, or the question asked above them —
invalidates every record granted under the previous one, exactly as
`scripts/llmlint-fingerprint.sh` invalidates a cached verdict when the judge
configuration moves.

**What the key covers is chosen, not incidental.** It is the authored content — the
title, the body prose, the node's `kind`, the persona, and the dependencies — and nothing
a settlement write-back owns. `status` in particular is not in it: the engine projects each
settlement back onto the plan it was launched from, so a key over the whole record would
go stale the first time a node ran and the gate would refuse every plan that had ever
been launched. Covering exactly the authored content buys the other half of that too — a
write-back that overwrote authored prose invalidates the record rather than leaving a
pass standing over content nobody read.

**A lifecycle node states that content somewhere else, and reaching it is the whole of
why "authored content" is the rule rather than a list of four fields.** A node that runs
several agent steps on one branch states its prose and its persona **once per `steps`
entry** rather than in `task` and `persona`, so for that node the steps are where its
criteria live — and a key that stopped at the node's own fields left a standing record
over criteria nobody had read, which is the one thing this gate exists to prevent. Each
step is keyed on the three fields its author writes — its id, its persona, and its prose
— so editing any step's criteria or persona invalidates that node's record, and reordering
or dropping a step does too. Each step is narrowed to those three for the same reason
`status` is excluded above: a field something other than the author adds to a step must
not invalidate a review of content nobody moved. The reviewer is shown every step in the
order it runs, as prose rather than as escaped JSON, because a stepped node's acceptance
criteria *are* that prose.

**The `kind` is in the key because it decides which question was asked**, and the
reviewer is shown it for the same reason. A `kind: human` node names an action an
external person performs, so it states that action and no acceptance criteria at all —
and a reviewer shown only its prose, under a bar about acceptance criteria, refuses it
for the one property its shape forbids it from having. `check-plan` then refuses the
plan for want of the record that refusal could not write, so **every** plan carrying
this repository's own documented human node shape was unlaunchable through its own
pre-launch check, whatever its author wrote. The prompt now names that shape and says
what to hold it to — whether the action is genuinely external, and whether one person
could read it and know what to do — and the key covers `kind`, so re-labelling a node
invalidates the review granted over it under the other question.

**One thing a plan carries is outside the key, and what that costs is worth knowing
rather than discovering.** A task **retargeted at another repository** after its review
keeps its record: which repository the work lands in is not something the review ruled on.

**A record is written by this repository's own code and never by a dispatched agent**,
and that is structural rather than a rule anybody is asked to follow. The store this
gate writes into is the gitignored `.plans/` of the *manager's* checkout; a dispatched
worker runs in a worktree of its own, nothing it writes below that directory is tracked,
and so no branch it produces carries a record back. There is no recipe, flag, or
documented step by which a dispatch writes one either. **A record is read from any
source and written into a local Markdown one**, which is the asymmetry to know before
reaching for `just review-plan`: a record travels in the open metadata map a task
already carries, so `check-plan` reads one off a board task exactly as it does off a
file, while writing one edits that task's own Markdown document — which only a local
Markdown source has. So a plan held on the `plans` board is checked, refused for
carrying no record, and cannot be cleared here; both commands say which store it is
rather than naming a `review-plan` that would spend a judged turn and then discover it
has nowhere to write. The two
writers are `just review-plan`, on a pass, and **`just plan`'s own closeout**: a
planning run that settles successfully has produced a plan `personas/planner.yaml`'s
judge already read, so the tasks that run authored are recorded rather than reviewed a
second time. That trusts the planner's judge to have covered node-level criteria, and
it is a decision rather than an inference — a planner-authored node that later fails a
review is evidence to stop and look, not a reason to add a per-node planner pass. The
closeout is scoped to what changed during the run, so the brief the *manager* wrote to
launch it is not blessed by the planning run happening beside it.

**Every** launch this repository makes exports `ORCHESTRATOR_ASK_MANAGER`, the path
of `scripts/ask-manager.sh`, which is how a dispatched agent puts one blocking
question to its manager over the run's own channel instead of guessing at a
decision fork: `just orchestrate` attached, detached, and adopted, and `just plan`.
The wrapper is half of that seam and the run it asks on is the other half — it reads
`ONEPIPELINE_RUN_ID` and refuses rather than guessing at one — and **every node
dispatch of a run carries it as of onepipeline 0.17.5**, composed where the dispatch
is made. That is a statement about the release in force and **not** about where the
behaviour arrived: `executor::dispatch_env` composes the pair, and it has done so
since onepipeline **0.8.1** (https://github.com/nickderobertis/onepipeline/pull/76).
Below *that* release nothing composed it: it reached a worker only by leaking
out of an *attached* driver that had started an observer graph in its own process, so
a dispatch of a detached or adopted run met its first fork with the wrapper there and
no run for it to ask on, and every brief had to be written to be answerable without
asking. `just plan` is the exception that stayed sound throughout, because it writes
the plan and exports the id itself.
`tests/e2e/test_launch_ask_seam_e2e.py` measures both halves per launch shape,
and its run-id journey fails on the detached and adopted shapes below the bump.
`scripts/ask-manager-env.sh` is the wrapper's one source and `scripts/onepipeline.sh`
is where a launch takes it, so a read-only view — which dispatches nobody — takes it
not at all. That wrapper is the only supported way to ask: `onepipeline channel
serve` answers its own timeouts at exit 0 with a ruling that reads like a decision,
and a verdict on the queue is claimed by whichever reader reaches it next, so a
question asked any other way can be answered by a fabricated verdict or by another
reader's ruling. A live graph edit is no longer among the things that can answer it:
the adopted release routes a reply by the halves it carries, so a commands-only
envelope stays on the command path and never reaches a reader waiting for a verdict.
A question is answered with `just channel-next` and `just
channel-reply`, which the launch prints.

**Those same launch verbs put this checkout's own credentials in that environment**,
read from a gitignored `.env` at the repository root — the first of them is
`GH_PROJECTS_TOKEN`, which a worker's `onetaskgraph` invocation needs. The dialect is
`KEY=VALUE` lines with `#` comments, an optional `export ` prefix, and matching
surrounding quotes stripped, which is onetaskgraph's own settled rule rather than a
second spelling of `.env`: an operator who has written one of these files has written
the other, and a value parsed with its quotes still attached fails authentication
somewhere far away with nothing pointing back at the file.
`scripts/credentials-env.sh` is its one source, sourced by `scripts/onepipeline.sh` on
`start` and `adopt` and by `scripts/plan.sh`, so — exactly as with the ask seam above —
a read-only view loads nothing and refuses nothing, because it dispatches nobody. **A
name the process environment already defines is never overridden**, so a value exported
for one command beats a file written once and forgotten. An absent file, an empty one,
and a name it does not define are all ordinary; a line that is neither a comment nor
`KEY=VALUE` is refused naming the file and the line number, rather than skipped in
silence. No refusal, diagnostic, or log carries a value, and nothing had to be taught
about this file for that to hold past the loader:
`orchestrator/redaction.py` and `scripts/preserved-log.sh` hide credential-shaped
values found in the **process environment**, which is precisely where this puts them.
`tests/e2e/test_launch_ask_seam_e2e.py` reads a name back out of a real dispatch's own
environment, per launch shape, and `tests/test_credential_dialect_drift.py` reconciles
the dialect against onetaskgraph's own parser in the uncached tier — a copied shape that
nothing reconciles is how the first version of this loader came to export
`TOKEN="ghp_..."` with the quotes still attached.

**Two credentials files exist on this host and only one of them is this
repository's.** `$HOME/.config/onetaskgraph/secrets.env` is onetaskgraph's own
development setup — machine-wide, outside every worktree, and not this repository's to
manage — and onetaskgraph answers a credential name from the process environment first
and that file second. So a name this checkout's `.env` defines arrives in the
environment layer and therefore **wins** for anything a dispatch here runs. That
precedence is the intent rather than an accident: a dispatch of this repository runs on
the credentials this repository supplies, rather than inheriting whatever a developer
happens to have configured for onetaskgraph itself — a consuming repository that had to
depend on another repository's development setup in order to run would be the defect.
The cost, stated plainly because somebody will meet it: the two hold the same names and
can drift apart, and **nothing detects, reconciles, or warns about that drift**. A value
changed in the machine-wide file and nowhere else has no effect on anything this
repository launches, and reads as lost rather than as overridden. Knowing which file
wins is the whole of the answer, and `scripts/credentials-env.sh` reads this checkout's
file and nothing else — the machine-wide one is not a fallback, not a source to read
from, and not something this repository moves or repairs.

**That same file nominates a board, and the nomination is the whole of what keeps a
credentialed write lane off the plans board.** `GH_PROJECTS_TOKEN` is not the only name
this checkout's `.env` carries: `GH_PROJECTS_OWNER` and `GH_PROJECTS_NUMBER` name one
GitHub Projects board by owner and number, and onetaskgraph's own live lane — the
integration tests of its `github-projects` plugin, which write for real — selects its
board from exactly that pair. This account owns two, number 1 *"onetaskgraph live"*,
which holds that lane's own fixtures, and number 2 *"AI Orchestrator"*, the plans board
`onetaskgraph.yaml`'s `plans` source reads. Under the release that lane used to
**discover** a board when the pair was absent — it queried the viewer's most recently
updated ProjectV2 and wrote to whatever came back — so the moment the plans board became
the more recently updated of the two, a credentialed write lane retargeted itself onto
it with nothing said, and left a draft item there on 2026-08-27. **The adopted
onetaskgraph 0.2.12 no longer discovers anything**, and it wants a third name: read from
that release's own published source — `crates/onetaskgraph-github-projects/tests/live.rs`
at tag `v0.2.12`, which is where that lane lives — the board comes from
`GH_PROJECTS_OWNER` and `GH_PROJECTS_NUMBER`, the repository its issues are created in
comes from `GH_PROJECTS_REPOSITORY`, and the lane **skips** when any of the three is
absent, exactly as it already did without `GH_PROJECTS_TOKEN`. That is cited to the file
and the tag rather than stamped with a date, because **nothing here re-takes it**: under
the rule below a dated claim about an external tool names the check that re-takes it or
goes, and a source a reader can open is the honest form of a claim that has none. So the
retargeting above is history rather than live exposure, and the cost of a missing
nomination has inverted:
a lane nobody nominated a repository for does not run at all. This host therefore
nominates `GH_PROJECTS_OWNER=nickderobertis`, `GH_PROJECTS_NUMBER=1` and
`GH_PROJECTS_REPOSITORY` for that lane's own repository, in the gitignored `.env` above,
because that is the file `scripts/credentials-env.sh` reads and the launcher's copy of
it is what a launch exports from — so nothing tracked here can carry the nomination, and
a worker in a worktree cannot make one for a future launch. Nothing detects its absence
either: on the release that discovered a board, a lane with no nomination looked exactly
like a lane with a correct one until somebody read which board it wrote to; on this one
it looks exactly like a lane whose credential is absent.
<!-- dated-claim: incident the date stamps when a superseded release's board discovery
wrote to the wrong project; what the adopted release does instead is cited above to its
own source at its own tag rather than to a reading taken here -->

**That third name is the lane's repository and not this source's**, and the two are
configured in different places for a reason worth keeping straight. `GH_PROJECTS_*`
steers another repository's test lane, from the process environment; what steers *this*
host's plans board is `onetaskgraph.yaml`'s own `plans` source, and since the redesign it
carries `repository: nickderobertis/ai-orchestrator` beside its owner and number. That
source is the one this repository plans against, for the reasons in [Where a plan of this
repository lives](#where-a-plan-of-this-repository-lives). That
field is where the board's project and task issues are created, because **a board is a
container of projects and not a project**: a project is an issue and its tasks are that
issue's sub-issues, `createIssue` requires a `repositoryId`, and a board has none of its
own — so a write without the field is refused naming it, and the pin and the field have
to move together. Spell it GitHub's way, `owner/name`: `github.com/nickderobertis/ai-orchestrator`
is refused as not naming a repository, and `github.com/…` is only how the source renders
that identity back.

`just runs` lists recorded runs with the session that launched each one and the
surfaces each has queued unread; `just runs --mine` narrows that to this session's.
`just stop <run-id>` ends a run and its whole dispatch tree, subject to the
[ownership rule](#your-loop-as-manager) above. Every one of those verbs goes
through `scripts/onepipeline.sh`, which is the one place a manager's identity is
established: `onepipeline` decides ownership from `ONEPIPELINE_LAUNCHER` /
`ONEPIPELINE_LAUNCHER_SESSION`, the reader's as well as the launcher's, so a view
that did not identify itself matches no run and `--mine` lists nothing.

What a run's agents *are* is [`graphs/`](docs/orchestration.md#the-agent-graphs-a-run-launches),
and it is this repository's content rather than the engines': `onepipeline` ships
the paths `graphs/dag-scope.yaml`, `graphs/node-scope.yaml`, and
`graphs/pr-author.yaml`, not the files, because they name this operator's own
onejudge base config, oneharness configs, and personas. A checkout without them
refuses every plan it has. All three paths
resolve against the directory a run is launched from, which is why `just
orchestrate` is run from the repository root.

Those views also stop guessing at what is *running*, and what they print is worth
knowing exactly, because the sentence here used to promise more than the commands
give. A node the ledger records as started reports how long it has been running,
what it is doing *now* from its streamed events, how many events it has recorded and
how long ago, and — where the dispatch is streaming — when it was last alive:
`stale-engine-docs-2: running for 25m21s — now Bash … (213 event(s), 9s ago; alive
3s ago)`. A node nothing is driving is flagged `UNDRIVEN` (deliberately not `parked`,
which is the node state a manager's own `cancel` produces). The **side and identity**
attribution is on a *failure* line rather than a running one —
`dispatch-appendix: failed — the agent side: identity 'claude-code:alternate'
refused (quota)`. **`just host`** is the whole-host view, one row per live dispatch:
run, node, harness, and turn age, above the run roots it could not read and why.
Miscounting live dispatches from `ps`, and missing a judge turn wedged for nearly
two hours, are what these replace.

Two things this paragraph used to claim are **not** in force and should not be
planned around: there is no `ORCHESTRATOR_AGENT_STATUS_DIR` stamp on a dispatched
process — no engine on the adopted stack exports that variable, and only
`scripts/smoke.sh` sets it here, so `scripts/oneharness-agent.sh`'s status-marker
path is unexercised on an ordinary dispatch — and neither view prints host load
averages. How `onepipeline` proves a live dispatch is its own is unestablished here;
read it out of the crate before relying on it.
They also report the tier *above* those dispatches: one driver line per unfinished
launch naming whether the orchestrator's recorded pid is still there, which part of
its loop the run's own state places it in, and how long since anything of it was last observed doing something.
A driver this host has proved is gone reads `DRIVER DEAD … nothing is driving this
run` — distinct from `PARKED`, which is a launch that still holds its pid, and
distinct again from the separate **observer** verdict printed beside it
(`OBSERVER DEAD` / `NO OBSERVER`), which says whether anything is *watching* rather
than whether anything is driving. The same tier is representable as run-scope
timeline spans — `rollup` spans labelled `agent_role: orchestrator` — but a real run
measured here carried none, and the bounded local capture that used to back-fill
them exists nowhere on the adopted stack; see [Seeing the supervisory
tier](docs/telemetry.md#seeing-the-supervisory-tier). Both views also say when they
cannot fully answer: on the adopted onepipeline 0.17.5 a run whose journal does not hold
every record whole prints `journal: … — this run's record of itself is incomplete`, which
is the one line that makes the rest unprovable, so read it before acting on a node
those views show as never settled. It used to be said only on the driver's stderr,
which a detached run writes to a log nobody opens.
**`just recoverable`** is the other half of that: every preserved-but-unpublished
branch, where it lives, why its workstream stopped,
whether it carries an incomplete-step marker, and the exact command that lands it —
`just repo-recover` for incomplete provenance, `just publish-branch` for a complete
branch under its identity's policy, `just integrate` for the local merge train,
with the fetch included when the publication checkout does not have the
branch. Reach for it instead of diffing clones by hand. **What it will no longer
offer is a branch that already landed — unconditionally for a `local-direct`
identity, and only while this host's record of the landing survives for a remote
one.** Through onevcs 0.8.x it decided that by
comparing trees, so a stale copy of a merged branch could be listed as work to
resume and the command beside it would have reopened it; 0.9.0 infers landing from
history instead, and ties it to the copy whose work it accounts for. **What that
inference has left to read is what differs by workflow**, for the reason [the landing
tiers](#what-this-repo-is) give: a `local-direct` landing is stamped onto the base by
`onevcs`'s own squash commit, so the listing drops it from the repository's history
alone and would go on doing so on a host that had never seen the branch; a remote
landing is stamped by the host, which stamps nothing, so every record of it lives in
`$ONEVCS_HOME` and a remote branch whose record is gone is listed again, under `— may
have landed`, with a `publish-branch` command beside it. So a **`local-direct`** branch
missing from the listing is a branch this verb has evidence about, not one it failed to
notice; for a remote branch that holds only while the record does, and a remote row
saying `may have landed` is the verb reporting that it has no record rather than that
the branch is unpublished. `just work-status <branch>` is where either evidence is
read — its `decided by:` line, which names the tier, before its `landed:` line.
**Which branches "every" covers is decided by where you run it**, and it is not always every identity: run
inside a registered checkout it answers for that checkout's identity **alone**, and
run anywhere else it answers across every registered identity. Run from this
repository root — a registered checkout — it lists ai-orchestrator branches and no
onepipeline ones. So an empty or short result is never "nothing to recover"
anywhere; read the first line, which names the scope it just answered at, and ask
again from outside any registered checkout for the cross-identity view.

**A run's journal is where a dispatch's own evidence lives, and it is the first
place to look when a settled node's evidence appears missing.**
`runs/<run-id>/events.jsonl` records every dispatched turn as `turn-activity`
events — each tool call **and the output it returned** — labelled with the run, the
node, the persona and the member that produced them, and it is written beside the
run rather than inside the dispatch, so it outlives both the turn and the worktree
a sweep later reclaims. Nothing a manager reads by default shows it: `just
channel-next` and `just monitor` read through the `planner` profile, which omits
worker turns deliberately. So a node can settle `failed` for want of evidence that
was on disk the whole time, and one did — `adopt-and-retire-gate` settled on its
judge's verdict that the dispatch had produced no installed-binary measurement,
while 490 `turn-activity` events sat in its journal and four of them carried that
measurement's own output verbatim. Three reads reach them: `just monitor <run-id>
--filter monitor` for the detailed stream, `--all` to bypass profiles entirely, and
**`just transcript <run-id> [node]`**, which renders one run's dispatched turns on
their own.

**What `just transcript` renders on the release this host has is the calls *and*
their outputs.** Re-measured on the adopted onepipeline 0.17.5 against the same
recorded run this used to be measured on, which is what closed a defect a manager
had to be warned about here: each turn prints one `tool_call` line per call, carrying
the tool's name and the argument string its producer recorded, and one `tool_result`
line per result carrying that result's own output. Every rendered value is
control-stripped onto one line, and an output is bounded at 4096 characters — the
same bound the run's own envelope already applies, so nothing the journal kept can be
cut here. **What is not printed is said rather than dropped**, which is the half worth
knowing: an output this view cuts ends `… [4096 of N characters]`, one the producer
had already cut ends `… [already cut short by the producer]`, one whose truncation
flag the build cannot read says so, and a result that returned nothing renders as the
empty column it is. The turn's own assistant text is still not rendered from the
journal — that arrives with a retained settlement report. Read `events.jsonl` as the
authoritative record either way; what changed is that the verb no longer hides the
answer while showing the question.
`tests/e2e/test_transcript_recipe_e2e.py` drives the recipe against a recorded run
and holds both halves of that reading, so the day the render changes is the day
this paragraph fails rather than the day somebody notices.

Every one of these views is read-only and safe beside live work.
Human completion is never inferred and enters the graph only as an explicit live
`attest` command, or the equivalent `onepipeline attest RUN REFERENCE`. Keep
operational
syntax and result contracts in
`docs/orchestration.md` and lifecycle policy in `docs/repo-lifecycle.md` rather
than duplicating command help here.
The root quality recipes delegate project selection and caching to Nx:
`check`/`test` use the full uniform target set through `run-many`, while
`lint`/`typecheck`/`format` use affected selection. The language-native tools
inside each project target remain authoritative, and `format-check` remains a
format-only verification. Session provisioning and the initial locked Bun
install precede Nx because they make Nx available; bootstrap then delegates
project setup through uniform Nx `bootstrap` targets.
The computation cache those targets use is not `.nx/cache`, despite the
`cacheDirectory` fallback in `nx.json`: every supported invocation goes through
`scripts/nx.sh`, which exports `NX_CACHE_DIRECTORY` as
`${XDG_CACHE_HOME:-$HOME/.cache}/ai-orchestrator/nx/<key>`, where `<key>` is the
first 16 hexadecimal characters of the SHA-256 of `remote.origin.url`. That
environment variable wins over `nx.json`. Measured 2026-08-26, the canonical
checkout, this run's isolated clone and worktree, and an extant publication clone
all reported `https://github.com/nickderobertis/ai-orchestrator.git`, resolved to
`756e79f93796751d`, and shared the same 81 MB directory; `.nx/cache` did not exist.
So deleting or changing `nx.json`'s setting would change no gate the wrapper runs,
and a publication clone already replays entries a session worktree stored.
`tests/e2e/test_nx_cache_scope_e2e.py` re-takes it against real Nx and the real
`scripts/nx.sh` in real linked worktrees, including the originless checkout that must not
be grouped with a repository identity.

An originless Git checkout still falls back to its top-level path for the key:
copies belonging to e2e journeys are not repository identities and must not be
silently grouped together. The wrapper used to `mkdir` that key before asking Nx
to do anything, leaving 167 empty directories among the 171 measured in the host
cache root. It now only exports the destination; Nx creates it if a cacheable task
actually stores an entry, while a metadata-only invocation leaves nothing behind.
Existing empty directories are inert historical litter; this change deliberately
does not delete shared host state.

The publication-shaped measurement for this change was
`AI_ORCHESTRATOR_NX_SHOW_OUTPUT=1 just gate origin main`. Its deterministic Nx run
took 6m52s, not the issue's roughly 37 minutes, and reported one hit among seven
cacheable tasks: `format-check` replayed. `test` re-ran for 6m50s because the
changed wrapper is part of its `codeWorkspace` input; `test-docs` re-ran for
2m43s and `lint` and `typecheck` re-ran because their `wholeWorkspace` inputs saw
the same change; `test-recipes` re-ran for 19s because `recipeWorkspace` includes
`scripts/**/*`; `test-checkouts` always re-runs because it is uncached; and
`coverage` always re-runs because it is uncached and consumes the test result.
The run stopped at the real lint finding (an unsorted new import), after completing
the other targets, rather than reaching llmlint; the import was then fixed at the
reported site. That is a cold changed-input measurement, not evidence of a
workspace-local cache miss: the shared cache was in use, and every miss either had
an input changed by this branch or belonged to a deliberately uncached target.
`scripts/workspace-install.sh` is that locked Bun install's one source. A freshly
created worktree carries no `node_modules`, so every `scripts/nx.sh` runs it first
and heals itself — and so does a worktree whose `node_modules` no longer matches
`bun.lock`, because the guard is `bun install --frozen-lockfile` itself rather
than a check for the Nx binary. Presence answered for *an* install rather than
*the locked* one, so a moved pin was invisible to every checkout that already had
a `node_modules`: this one served `onepipeline-ui` 0.3.3 under a 0.5.0 pin, and
six defects were reported against a bundle that had already shipped them fixed.
Asking Bun costs ~18ms against ~4ms, on a wrapper whose cheapest Nx invocation is
~1.6s. `just bootstrap` runs it with `--force`, which is now the one thing the
ordinary run does not do: discard the installed tree first, since what the
lockfile does not describe is what Bun reconciling against it will keep. Nothing
here asks an operator to run Bun by hand, and the e2e
journeys that drive real Nx provision through the same script rather than skipping
when a worktree is fresh — a bare `pytest` in one means what the gate means.
`scripts/python-install.sh` is the same self-heal for the other half of the
toolchain, and `scripts/nx.sh` runs it beside the Bun one for the same reason: a
fresh worktree and a publication clone carry no `.venv` either, and a missing one
does not fail — every reader of `<root>/.venv/bin` **falls through to whatever other
checkout is on PATH**, which is how a gate came to verify a branch against the
engine versions the branch had already moved past. It is `uv sync --locked`, so the
committed lockfile decides and one that would have to move is a refusal naming `uv
lock` rather than the silent rewrite `uv run` performs on its way into a target —
the difference between the two on a branch whose subject *is* a pin. `UV_NO_SYNC`
turns it off, which is how the journeys that copy this checkout keep pointing uv at
this one's environment.
Use `docs/telemetry.md` to inspect a run's wall-clock breakdown and usage with
`just telemetry`; it is run-scoped, with no per-node rows and no turn timeline —
the timeline is the read API's, at `/api/v2/runs/{run}/timeline`.
A node that died to the provider is diagnosed from `just status` alone: it and
`just runs` print `oneagentgraph health`'s own JSON report — every identity oneharness
knows, not only the five these configs name, each with its selector, auth mode, and
every availability window's `used_percent`, `resets_at`, and `is_binding`, and a
failed probe listed as `state: unknown` with the reason rather than dropped — above
a per-node failure line naming the refusing **side** and **identity**. Read the side first: the agent and judge
chains prefer different identities, so a fix aimed at the wrong one changes
nothing, and a whole night was once lost to a judge-chain quota that read as a
bare `harness failed (quota)`. `just results` and the read API carry the same
attribution per node with the harness's own bounded output. See [Diagnosing a
provider failure](docs/telemetry.md#diagnosing-a-provider-failure).
`just telemetry-server` serves the published read-only DAG API over a runs root and
`just dag-ui` serves the published browser bundle against it; both are read-only and
mutate no run. Neither is built here any more — the API is `onepipeline-api` and the
view is the `onepipeline-ui` bundle — so `just dag-ui` puts the two behind one origin
and `just dag-ui-screens` photographs that bundle at every viewport in the matrix,
printing the gitignored per-invocation gallery it wrote. Operational detail lives in
[`docs/dag-ui.md`](docs/dag-ui.md).
`just sweep` reclaims the dead working directories this host accumulates, by
composing the **two** published verbs that own them rather than reimplementing
either: `oneagentgraph sweep` for the scratch a dispatch leaves behind, and `onevcs
sweep` for the publication and recovery workspaces a lifecycle leaves behind. Both
judge a candidate on proven non-reference — no live process names it in its argv,
environment, `cwd`/`root`/`exe`, open descriptors, or memory mappings — past a short
age that only covers the gap between creating a directory and first naming it.
`--dry-run` inspects without removing and `--min-age-hours` moves the conservative
threshold, and each reaches **both** verbs, because an age floor that meant one thing
to one family and another to the next would be worse than no floor. **The recipe
passes 4 hours when you name none**, against the 24 both verbs default to: at 24 the
composed sweep reclaimed 0 B on this host where 4 reclaimed 23.9 GB, because a host
running several dispatches churns workspaces and scratch hourly and almost nothing
provably dead is ever a day old. Lowering it weakens no proof — neither verb removes
anything on age alone. The reasoning and the measurements ruling out the obvious
alternative explanations are in `scripts/sweep.sh` beside the number, so moving it is
an argument with those rather than with taste. Session setup runs it automatically.
The recipe was named `sweep-scratch` while the first verb was the whole of it; a
publication workspace is not scratch, so the name now names the composition and
`sweep-scratch` is gone rather than kept as a misleading synonym.
Neither verb's report is rewritten — each names its own families, its own retentions,
and its own reason for each. It is **quiet on success**: a sweep that examined every
family and left nothing to act on is one line naming those families, and the two
reports and the trailer appear only when a verb failed or a family went unexamined,
which is what makes them worth reading when they do. `--dry-run` always prints them,
because it removes nothing and those reports are the answer it was asked for. What
the composition adds is the **trailer**, which is the part no single verb can write:
the families examined *across* the two, and the families **neither** examined, each
with a reason, so that every family appears in exactly one list. That is the whole point of it. A sweep reporting `0 B reclaimed`
while a family it never looked at fills the disk answers the operator's question with
a number that reads like an all-clear, and this host has been in exactly that state.
Two consequences to read rather than infer: a verb that **fails** moves its families
into the not-examined list and the other verb still sweeps, with the recipe exiting
non-zero so the gap shows in the status as well as the report; and the pre-adoption
`~/.ai-orchestrator/worktrees` root is **reported with its size, never reclaimed** —
every directory under it is a *registered* git worktree whose lender still lists it
and whose branch can still hold unpublished work, so land or discard that branch
(`just recoverable` names the verb) and then `git worktree remove` it in the lender.
**The host scratch root is the second reported-never-reclaimed family, and it is the
one that filled this disk.** `$TMPDIR`, or `/tmp`: `oneagentgraph` writes the family
it owns there and reaches nothing else, `onevcs` writes nothing there at all, and
every other directory under it belongs to neither verb — a private `nx` install per
`bunx nx`, a copy of Nx's native binary per workspace root, a pytest run directory
per session, onejudge's own scratch. It reached 139 GB of a 169 GB device, took `/`
to 2 MB free, and stopped every dispatch on this host while every sweep that day
reported success. The trailer now names it with its size, its entry count, and its
largest three name groups with trailing ids folded together — because the producer
was 3,646 directories of one `nx` cache, which reads as an unrelated long tail in any
per-directory listing. Nothing here removes any of it: doing so means implementing
the proof both verbs already have, which is their work rather than this wrapper's.
`docs/orchestration.md` records what the trailer leaves out of that measurement and
why.

The processes that are *meant* to outlive their launcher — the driver `just
orchestrate` starts, and the dispatches and publications it forks — are the
engines' own to keep alive and to reap; never work around a kill here with
`nohup`/`setsid` by hand.
Dead lifecycle runs form a separate bounded recovery history: retain the newest
**3** run roots with unpublished work. A retry or `repo-recover` adopts the exact
worktree only after claiming its free occupancy lease and rejecting a live
recorded owner; dirty adopted work becomes an incomplete-step commit and must
pass the ordinary merge path before publication.

**What prunes that history is `onevcs session open`, and reclaiming is its *first*
act.** Through onevcs 0.14.0 the only proof it accepted was an exclusive take on the
root's occupancy lease — which no `onevcs` verb holds past its own command, so a root
three hours into a dispatch was as takeable as one created a second ago. On 2026-08-22
three dispatches of one run were destroyed within 90 seconds of launch by the next
sibling's `session open`.
<!-- dated-claim: incident the date stamps when three dispatches were destroyed on this
host; what onevcs does about it is stated by release and re-taken by the check the next
paragraph names -->

**onevcs 0.14.1 fixed that — this host adopts it at 0.15.8 and runs 0.16.2 today — so the
one-dispatch-per-identity constraint this paragraph used to impose is lifted.**
`reclaim` reads every session record before it walks the directory, skips the run roots
those name, and only then falls through to the lease — at 0.14.1 for a record whose
**owner was live**, and since **0.15.6** for any record that is still **open**, whether
or not the CLI that opened it has exited
([PR #99](https://github.com/nickderobertis/onevcs/pull/99), bisected here on
2026-08-29: kept from 0.15.6 onward, removed at 0.15.4 and 0.15.5). What prunes a root
is therefore **closing** its session rather than its opener dying, and a session that
opens and is never closed keeps its root until `just sweep` proves nothing names it —
[the change](https://github.com/nickderobertis/onevcs/pull/82) is the one
[`docs/run-root-reclamation.md`](docs/run-root-reclamation.md) specified, landed as
specified. Re-measured here on 2026-08-25 against both binaries with everything else
held: a live session's run root survives a sibling open on 0.14.1 and 0.15.4 and is
removed on 0.14.0, and
`tests/e2e/test_run_root_lease_e2e.py` now asserts the surviving half and was proven
red against 0.14.0 before it was believed, and it asserts the widened rule above on the
pinned 0.16.2 as well: a killed owner leaves an open session's root standing, and a
closed session's root is gone at the very next sibling open. So **schedule concurrent lifecycle
dispatches on one identity freely**, including a plan whose `concurrency` is above 1 —
which used to reach this with no second manager involved, since a run's own driver owns
every session of it.

Two things a manager should still carry from it. `scripts/hold-run-lease.sh` is
**kept and still wired** — it is a second line now rather than the only one, covering
the case the record rule cannot, a session directory `reclaim` cannot read at all —
and it still takes no lease for a dispatch that fell through to codex, which fires no
`SessionStart` hook. And once a session has closed, its run root is retained only when
its clone *already* holds an
unpublished commit, so an abandoned session that closed and never committed is still
removed outright rather than retained; that is reclamation working, and it is why a
stranded branch is worth committing early. A session that never closes keeps its root
whatever the clone holds, which is the widening above and is also why a crashed driver
leaves a directory nothing prunes until somebody closes the session or `just sweep`
reaches it. And **read a spawn
failure naming a missing `claude` as a possible deleted run root**: `ENOENT` from a
spawn also means the child's working directory is gone, the message names only the
binary, and its suggestion sends the reader to PATH — which is where hours went. The
binary exists; check the run root.

`just smoke` spends one real agent-harness turn in a throwaway directory and
verifies exact prompt delivery plus a successful, fully accounted oneharness
history record. Native per-phase timing is provider-optional, so its absence is a
telemetry-quality signal rather than a launch failure. The *launch* is relaunched a
bounded number of times — `oneagentgraph smoke` owns that policy now — and only the
launch: a host under
concurrent e2e load has started the selected harness and had it die, which reads as
a launch-path outage and once cost a publication that had already passed its gate.
A genuinely broken launch path fails every attempt and still fails, and a recorded
turn that violates the contract fails on the first. A passing run says how many
launches it took. The record it judges is the **selected** candidate's: a
`fallback` chain records every candidate it attempts, so an identity that refused
the turn with a classified `quota` or `auth` failure — or was skipped outright — is
the chain working, and the smoke names it in the pass rather than failing on it.
Anything else is still a launch failure: the selected record breaking the contract,
a candidate failing for a reason the chain does not move past, a candidate whose
record does not back the reason it names — one that identifies no harness, or that
carries a turn somebody was billed for — or every candidate refusing. See [The
record a fallback chain is judged
by](docs/onejudge-integration.md#the-record-a-fallback-chain-is-judged-by).
It is deliberately outside `just gate`. The pre-push hook runs it
only when the pushed diff touches `scripts/`,
`config/oneharness.version`, `config/onejudge.base.yaml`, `oneharness.toml`,
`oneharness.judge.toml`, `oneharness.orchestrator.toml`, or
`oneharness.check-in.toml`; ordinary pushes consume no harness quota.

A dispatched change is done when the checks that exercise it are green; the complete
bar is the pre-push hook's, on the push that publishes the branch. An agent that runs
the judged tier clears its own findings rather than leaving closeout to integration, and
iterates with `just lint-llm-diff <base>` alone rather than paying for the whole gate
per finding.
`llmlint.yml` is a legitimate deliverable when a task names it; otherwise a worker
fixes the code and reports a rule that looks wrong or misapplied instead of editing it.
**What a worker may suppress is not stated here, and that is deliberate.** This document
used to state it twice, in two consecutive sentences that contradicted each other, while
`config/dispatch-appendix.md` read categorically against both — three sources and three
readings of one question. A dispatch followed one of them, cleared four findings exactly
as that reading allowed, and had its finished work failed by a judge reading another. The policy therefore has exactly one source, and it is
[`config/dispatch-appendix.md`](config/dispatch-appendix.md): that is the text every
dispatched task carries, so it is the only copy a worker and its judge read together.
Read the policy there, and amend it there.

The judge behind that tier is non-deterministic, so the run itself is cached: `just
lint-llm-diff` resolves the base ref to a commit and runs the cached Nx
`workspace:lint-llm-diff` target (the root `project.json` — the check spans the
whole tree, so it belongs to no single project), whose command is
`scripts/llmlint-judge.sh` — llmlint with `-v`, and nothing else. Re-running `just
gate` on an unchanged tree against an unchanged base replays that run's own
terminal output instead of rolling the judge again, which is what stops one branch
from being blocked by opposite verdicts on an identical diff. `-v` is why replay
loses nothing that matters: the report carries every rule and the `llmlint history
<id>` pointer, and Nx replays it verbatim. A green elides one thing — the
serialized judge calls `-v` also prints, ~214KB of a 226KB run — because Nx
replays a hit as one burst and a burst past one pipe buffer arrives truncated;
`llmlint history <id>` has them in full, and a failure is never cached and so
prints everything. The key covers the whole workspace, the
resolved base commit, and `scripts/llmlint-fingerprint.sh` — the installed llmlint
version plus the effective merged config, so a rule change in a plugin fetched from
outside this repository still invalidates. That fingerprint resolves both of those
through `scripts/llmlint-runtime-env.sh` — the one environment the target itself
judges with — rather than the caller's, so the key always describes the judge
configuration the run would actually use. `LLMLINT_ONEHARNESS_BIN` is why: `llmlint
config` renders it as `oneharness.bin`, and a dispatch inherits the orchestrator's
checkout path, the session's own, or nothing at all, so one judged diff hashed to a
different key per dispatch and the judge re-rolled on every run. Reading the caller
fails a quieter way too — because Nx scores a runtime input that exits non-zero as
*no contribution* rather than as an error, a fingerprint the caller's environment
can break does not fail the tier, it drops the judge configuration out of the key
and replays a verdict that configuration has moved on from. Because Nx caches
successful tasks only, **only a green is cached**: findings (llmlint exit 1) and a
toolchain that never reached a verdict (exit >= 2) both fail the tier and re-judge
on the next run. That is deliberate — the record/replay protocol that used to
smuggle failures through Nx is gone, and with it the shared scratch directory a
concurrent invocation could clear out from under an in-flight judge. A branch
working through a red pays a fresh roll each time, and `llmlint history` (retained at
`history.max_runs` in `llmlint.yml`) is where every roll lands. A wrong *green*
still sticks: `just lint-llm-diff <base> --skip-nx-cache` re-judges but neither
reads nor writes the cache, so the next ordinary run replays the same entry until
the tree, the base commit, or the judge configuration moves. That per-invocation
flag is the only supported re-judge lever; an ambient global
`NX_SKIP_NX_CACHE` / `NX_DISABLE_NX_CACHE` is reported and ignored by this tier,
because it re-rolls the judge from every unrelated command and breaks the checks
whose contract is cache replay. When a
miss is unexplained, run `scripts/llmlint-fingerprint.sh` — a changed fingerprint
on an unchanged tree is a changed judge, not a changed diff. The cached green for
one content, base commit, and judge configuration is authoritative and the
worker's own gate pays for it: `ONEVCS_COMPARISON_REMOTE` / `ONEVCS_COMPARISON_BASE`
is that identity's one source, and the lifecycle exports it to every dispatch and
every publishing push
of a workstream so the `pre-push` hook replays what the worker cleared instead of
re-rolling against findings it never saw — a push that resolved its own base could
merge work whose own gate had failed. See
[One judged diff, one verdict](docs/repo-lifecycle.md#one-judged-diff-one-verdict).
That base belongs to the workstream, not to one dispatch: a later lifecycle step,
a retry continuing a preserved branch, and a stopped-run resumption are all judged
on the branch's whole diff from its publication base. The measured session shapes,
their evidence, and the onevcs change required before that can be narrowed soundly
are recorded in that section.

Every cached Nx target replays a recorded answer, so one rule governs the test tier
too: a memo may stand in for a verdict on this tree only when its key covers
everything the check reads. The Python targets run from the workspace root over the
whole tree — pytest reads documentation, recipes, hooks, and app config — so they
are keyed on it through `nx.json`'s `wholeWorkspace` input. Narrowing one back to a
subset makes a green suite a claim about a tree that was never run; force a real
re-run of a single tier with `--skip-nx-cache` on that one invocation instead. The
narrowings that earn their keep answer at the scope their tests read:
`orchestrator:test-docs` runs the handful that assert on this repository's prose
and keeps the whole-workspace key; `orchestrator:test-recipes` runs the journeys
that drive `just` recipes and shell scripts under `recipeWorkspace`, exactly what
they drive; `orchestrator:test` runs the rest under `codeWorkspace` — the
workspace minus `docs/**` and `**/*.md`.
`workspace:check-nx-cache` is narrowed the same way, onto the fixture and
scripts it builds its two worktrees from. A documentation edit stops charging for
the whole suite. Where no key would be right the tier is **uncached** instead:
`orchestrator:test-checkouts` reconciles this repository's routing against the
repositories it routes — the required checks each merge path really declares, read
from GitHub, against the inventory in `config/merge-path-checks.json` — and those
live outside the workspace, so a memo would describe whatever they required when it
was recorded. The same tier
holds the **lost-turn wire drift gate**, for the same reason and against a different
outsider: `tests/test_lost_turn_wire_contract.py` reconciles the harness wire shape
`scripts/channel-serve.py` restates against the installed `codex` — what it emits on a
real lost turn, and what `codex app-server generate-json-schema` says it emits — and
`tests/e2e/test_lost_turn_wire_contract_e2e.py` drives that same lost turn through the
filter to a real channel. A memo keyed on this workspace would replay a green across
the very producer upgrade both exist to catch. No split may go
stale silently: an undeclared test that opens
this checkout's own documentation fails in `tests/conftest.py` and is told to carry
`@pytest.mark.reads_docs`, a `@pytest.mark.reads_recipes` test that opens
anything outside its narrower key fails the same way, and so does an unmarked test
that opens a registered checkout of another repository. See
[When a cached verdict may stand
in](docs/repo-lifecycle.md#when-a-cached-verdict-may-stand-in-for-a-verdict-on-this-tree).

A remote lifecycle change request's body is drafted by an agent graph the launch
names, the way its observer is: `just orchestrate` adds `--pr-author-graph
graphs/pr-author.yaml` beside `--dag-graph graphs/dag-scope.yaml`, and both ship
defaulted to nothing because the crate ships the flags and not the documents. One
post-verification turn reads the branch's diff and answers with the
template-shaped body, validated against `config/pr-author-body.schema.json` — the
`{body}` contract `onepipeline` reads out of `results[].structured.body`. A node
that states its own `body` publishes with that. Drafting never blocks publication
and never retries: a draft that cannot run warns on the node and the change
request opens with **no body**, which is also what a launch naming no drafting
graph does. Those two are not the same thing to read, and on the adopted onepipeline 0.17.5
they no longer look it: a drafting dispatch that was configured, attempted, and
produced nothing records `body-not-drafted` against the node with which of
`dispatch-failed` / `schema-refused` / `no-body` it was, and `just results` carries
that ending — while a launch that named no graph, and a node that carried its own
`body`, emit nothing, because neither spends a dispatch and neither is a fault.
**Three commands here draft, and they are the three that open a change request**: that
launch, `just publish-branch`, and `just repo-recover`. The two landing verbs go
through `scripts/land-branch.sh`, which reads the branch and `--repo` out of the
arguments, drafts through the same graph out of band, and appends `--body-file` to
what it forwards; an argument list it cannot read that way lands exactly as it did
before. **A `--repo` that is not a directory is put back to `onevcs resolve`**, so the
alias `just repos` lists — the form an operator actually types — is drafted for rather
than refused; while it was not, every alias-form landing opened its change request with
an empty description behind a message that read like a refusal. Two escapes, in the
order they win: a caller's own `--body` or `--body-file`
is forwarded untouched and spends no turn, and `--no-draft` skips drafting and is
consumed here rather than forwarded, because `onevcs` has no such option. It is the
escape for a bulk landing. **The turn is spent before the push** — the body is an
argument to `onevcs` and the verb is what pushes, so a branch the merge path then
refuses has paid for a body nothing used; that is accepted rather than overlooked, and
moving drafting behind the push would be a different repository's design. `just
integrate` drafts nothing and needs nothing, because the local merge train opens no
change request. And **`onevcs recoverable`'s own printed `Resume:` line drafts
nothing**: it renders `onevcs publish-branch …`, which reaches the verb below the
wrapper and opens an empty description, so `just recoverable` re-renders each of those
commands in its `just` form and passes every other line — and the whole of `--json`,
whose `recover_command` other consumers read — through untouched. See
[Diff-derived PR
descriptions](docs/repo-lifecycle.md#diff-derived-pr-descriptions).

**The body is re-derived at every publication and the subject is derived once, which
is backwards, because the subject is the one that becomes permanent.** `onevcs`
passes `--title` on `gh pr create` and has no path that edits an open change request,
so a republication pushes the branch and updates nothing else: the subject a plan
node's `title` named before any of the work existed outlives every dispatch that
changes what the branch does. onepipeline's change request #127 opened under the
subject *"fix(deps): resolve the onevcs release that opens a session past a retained
pipe"*, which was the whole of the branch at that moment; three further dispatches
then worked it and one of them **reverted the bump**, leaving an empty net
`Cargo.lock` diff and a net diff of two files, neither a manifest. That identity
resolves `change-auto`, which is `gh pr merge --squash --auto` with no `--subject`,
so GitHub composes the squash headline from the title, it lands on a base whose
history is never rewritten, and `release-plz` reads it to choose the next version and
write the changelog line. It was caught with about ten minutes to spare, and only
because the subject was read while checking something else: nothing in the pipeline
would have raised it, and the required `pr-title` check **passed** throughout,
because it validates Conventional Commit *form* and cannot see the diff. So **read a
change request's title against its net diff versus the base before anything merges
it, never against its commits** — reading the commits is what hides this, since every
commit message on that branch was accurate, including the one that made the bump and
the one that undid it. Only the net diff says the pair cancels.

## Dogfooding rule

Use the orchestrator harness for **all tasks of sufficient complexity**, in any
repo or project. A **qualified onetaskgraph project launched by `just orchestrate`**
is the only way to dispatch: one subtask is a project holding one task — one direct
agent (`examples:scheduler-research`) or one lifecycle node
(`examples:health-endpoint`) — and a larger task is the same project with more
tasks. Nothing about plan schema, personas, or node semantics changes
with the node count, so a one-node run still gets a journal, an ownership row,
planner surfaces, and a place in the DAG UI. Lifecycle nodes clone the target,
work in an isolated worktree, publish, and let its merge path verify. Dispatch smaller
project work with a single-node plan rather than doing it directly; only the
slight-tweak exception above applies. This repo is one
local-mode case of the same rule.

**Self-dispatch rule (this repo).** Never author working-tree changes in the shared
canonical checkout: concurrent orchestrators use it and direct edits race them.
Every change — including plans, personas, docs, and `AGENTS.md` — must be dispatched
into an isolated worktree cut from the registered `ai-orchestrator-isolated`
safety clone, with the canonical checkout retained as the node's `repo` publication
repository and only fast-forwarded after integration. Set `execution_checkout` on
each lifecycle node of the plan rather than passing a top-level flag.
This does not restrict the narrow direct git operations above on finished
dispatched work. Confirm `git config core.bare` is `false` before trusting a
self-dispatch result.

## Stack and composition

How this polyglot monorepo was built up from the create-repo reference pieces:

- **Product shape:** a configuration layer over four published CLIs, kept as an Nx
  workspace for its computation cache and its uniform target set. It remains
  closest to `shapes/skills-repo.md` (determinism-vs-judgment split,
  validate-in-gate, narrow allowlist), applied to onejudge configs + personas
  rather than skills. The engine, the lifecycle, and the browser view are no longer
  built here: `onepipeline`, `oneagentgraph`, `onevcs`, and `onepipeline-ui` own
  them, and the `just` recipes are thin wrappers over their verbs.
- **Language(s):** Bash for the recipes' wrappers, the harness routing, and
  provisioning; Python (uv, ruff, mypy, pytest) for the Nx `orchestrator` project —
  now the label contract, the redaction rule, and the suite that proves this
  layer; YAML, JSON, and TOML for configs.
- **Composed:** `base.md` (always) + `shapes/skills-repo.md` + `monorepo.md`. Nx
  provides the project graph, affected execution, and computation caching; the
  underlying language tools remain the source of each check.
- **Excluded, and why:** **CI** — deliberately, per the repo's charter: this is a
  local, private proof-of-concept ("local config/scripts/docs at this point"). The
  full gate still runs locally as `just gate` and at pre-push; add
  `.github/workflows/` mirroring it when this graduates past PoC. `releasing.md` —
  nothing versioned is published. asdf / direnv — the committed Bun and uv
  lockfiles already make the workspace reproducible.
- **Composed additionally:** the `llmlint` LLM-judge tier (`ci.md`'s companion) —
  `llmlint.yml` + the `lint-llm*` recipes, enforced at **pre-push**
  (`.githooks/pre-push`) since there is no CI.

## Invariants (non-negotiable)

- The gate is strict: format check, lint, type check, and tests all fail on
  issues — no warnings-only mode.
- **Coverage is enforced at 100% line coverage** on the `orchestrator/` package
  (`just test`); the gate fails below it. `[tool.coverage.report]` in
  `pyproject.toml` is the floor's one source — `fail_under` sets it and
  `precision` decides it, because pytest-cov compares the total *after* rounding
  at that precision. `tests/test_coverage_gate.py` holds that combination to one
  that can actually fail the build. The floor is 100 because what is left of the
  package is small and is all trust boundary: the label contract handed to a
  subprocess, the redaction rule `scripts/preserved-log.sh` mirrors, and the paths
  both read. `orchestrator:test` measures and judges nothing; the uncached
  `orchestrator:coverage` reads what it wrote and compares that total to the
  declared floor. Nothing else may name a floor, and a measuring tier that did not
  write its data fails that read rather than lowering the total silently.
- **The suite runs across four xdist workers** (`-n 4 --dist loadgroup`), chosen
  from measurement rather than from `auto`: it is latency-bound, its floor is its
  longest single test, and the curve is flat past four while this host also runs
  live dispatches. A test whose subject is a process-wide or machine-wide
  resource declares that as a scheduling constraint — never as a loosened
  assertion, and never as a per-run solo re-proof by hand. The two markers that
  used to carry those constraints went with the dispatch journeys that needed
  them; a test of that shape reintroduces the marker, its tier, and its reason
  together rather than weakening an assertion to survive a worker.
- **Tests are realistic, not mocked.** What this repository still owns is its
  command surface, so the suite drives the *real* `just` recipes, the real wrapper
  scripts, the real `oneharness` CLI, and real Nx. The published CLIs a recipe
  delegates to are doubled at that boundary and nothing above it: each engine is
  proven in its own repository, and a real `onepipeline start` here would launch
  agents. Never double a recipe, a wrapper script, or the shell they run in.
- Validate external inputs at trust boundaries: a persona is validated before
  dispatch (`just validate-personas`), and the label contract handed to a
  subprocess is validated in `orchestrator/labels.py` before it can reach one.
- Do not commit secrets or credentials. Harness credentials (e.g.
  `CLAUDE_CODE_OAUTH_TOKEN`) live in the environment, referenced by name; the
  agent allowlist in `.claude/settings.json` stays narrow.

## Tests are context engineering

This repo runs on agents, so the suite is the only QA loop.

- **e2e** (`tests/e2e/`) proves the real journeys against the real boundaries: the
  whole delegation table driven through the real recipes and wrapper scripts, the
  llmlint tier's cached verdict and its two judging paths, the Nx cache keys
  against real Nx in real linked worktrees, the harness wrapper against the real
  `oneharness` CLI and its fallback chain, and session setup installing the
  adopted releases for real from PyPI. Only the paid model and the published CLIs
  a recipe delegates to are doubled.
- **unit** (`tests/`) covers what this layer decides on its own: the label
  contract, the redaction rule, the coverage floor's enforceability, and the drift
  gates over the pins and the prose.
- A recipe is not done until a journey drives it end to end in `tests/e2e/`.

**A date in this document names the test that re-takes it, or says it is history.**
`AGENTS.md` measures other people's software constantly and stamps the date on what it
found, and that stamp is the whole problem: it is what makes a claim read as current long
after the tool has moved, so the reader who most needs the truth gets a confident answer
to a question nobody has asked since. Two such claims were falsified in one session here,
each having been believed for exactly as long as it stood. So **every ISO date here is a
claim about somebody else's software until its own paragraph says otherwise**, and a
paragraph carrying one does one of two things. It names a test under `tests/` that
re-takes it — the check that fails when the release behind it moves, so the claim comes
due instead of quietly going stale; `tests/test_linked_libraries.py` is the pattern that
generalizes, reading the pins off the installed artifact on every gate run rather than
believing them. Or it classifies the date as stamping something that *happened* rather
than something that is still true, with `<!-- dated-claim: incident <reason> -->` in that
paragraph — a forensic record does not go stale, and three paragraphs here are exactly
that. The marker is a suppression, governed by
[`config/dispatch-appendix.md`](config/dispatch-appendix.md).

`tests/test_dated_claims.py` enforces it, and the reason it triggers on the bare **date**
rather than on how the sentence is written is that two narrower detectors both shipped
holes. One asked for a measuring verb and missed *"held one plan at a time until
<date>"*; widening it to bounded states still passed *"On <date>, onevcs returned X"*,
which is a plain assertion of tool behaviour in neither shape. English has no closed
vocabulary for asserting something, so the vocabulary was abandoned and the judgment
moved to the author, where it is written down. What that gate still cannot do is judge
the test a paragraph names, and that is the one gap left rather than an unstated one. A
claim with neither a check nor a classification is not hedged and kept: the measurement
goes, and what may stand in its place is what a release did, cited to something a reader
can open — a file at a tag, a change request, a release number — rather than to a date on
which somebody here happened to run it.

## Commits and merging

Squash-merge via PR is the intended model; PRs follow
`.github/pull_request_template.md` (terse **What** / **Why**). With no CI, the
**pre-push hook** (`.githooks/pre-push`, activated by `just bootstrap`) is the
enforcement point: it runs `just gate`, so nothing reaches the remote unproven.
Every dispatched agent must clear its own findings before committing. Branch
protection and a CI mirror of this gate are deferred with CI. Keep the
`.claude/settings.json` allowlist current: add a new routine command there instead
of re-approving it each session. Local-first is not local-only: keep the registered
base branch in sync with its origin, and push every change that reaches it immediately rather
than leaving verified work only in the local checkout. A dispatched `local` merge
already pushes to origin, and `just sync` fast-forwards a publication checkout to
what is on it. The pre-push
gate guards every push. Never force-push or rewrite history on the registered base.

`core.hooksPath` activates the whole directory, so that same `just bootstrap` step
also activates two more. **`.githooks/post-checkout`** marks whatever git just
checked out trusted for every claude-code identity a dispatch can run as — both
alternate subscriptions and the primary. It is the hook rather than an `onevcs`
interface because git fires `post-checkout` exactly where a directory appears, and
`onevcs` gives a disposable clone the lender's `core.hooksPath`; claude-code keys
`hasTrustDialogAccepted` on the exact project path and honours no parent entry, so
without it every dispatch began untrusted, discarded its `permissions.allow`, and
blocked on an approval that cannot arrive non-interactively — **which reads from
outside as an identity with quota left doing nothing**, and is worth suspecting when
one does. It never fails and never speaks, because a non-zero `post-checkout` fails
the command that ran it and would break every `git worktree add` this host makes;
session setup makes the same call where a diagnostic reaches somebody who can act on
it. **`.githooks/commit-msg`** holds the *subject*. This
repository's product is its tracked source — config, personas, scripts, docs — so
every commit changes the deliverable, and the hook takes that to its conclusion: a
subject must be a Conventional Commit within onevcs's 120-character publication
limit, carrying a type this repository releases from (`feat`, `fix`, `perf`, or any
type marked breaking with `!`). A `docs:` or `chore(deps):` change to tracked source
merges green and then never cuts a release, which is what cost two changes in one
plan and was caught both times only by a person reading the title. The hook reads the
subject and nothing else — no index, no diff, no branch — because the adopted
**onevcs 0.16.2** puts the composed subject a publication is about to land under to
that repository's own `commit-msg` hook, where none of that exists, and one policy
must mean the same thing to both callers. Re-measured on 2026-08-26 against the
adopted CLI, the hook still refuses the publication but the diagnostic now comes
from the squash commit's `git commit -m` failure rather than the earlier up-front
wording. The disposable clone a publication works in is given the lender's
`core.hooksPath` (or its tracked `.githooks/`) when it is cut, so git has always run
this repository's hook on the squash commit a publication writes — from the far side
of a gate run and a merge, reported as `invalid input: git commit -m <subject>
failed`. `tests/e2e/test_publish_branch_e2e.py` holds the refusal and proves the
base remains unchanged. Two things it is therefore *not*: `just integrate`
composes the train's subject through `provenance::publication_subject` rather than
the publication path, so it never asks; and a lifecycle dispatch publishes through
the onevcs `onepipeline` links, which is 0.16.2 too, so it asks the same question
before anything is written. That last one was worth stating separately only while
the two numbers differed: through the cycle when the linked copy was 0.4.2, a
dispatched publication met this hook as git's own refusal of the commit and nothing
earlier, and it will read that way again the moment they part. Git's own generated subjects, autosquash
markers, and the `(incomplete step)` marker and its attestation are exempt: no
publication carries them.

Squash-merge is what a recovered incomplete step publishes too, so **every** path
that advances the base — lifecycle publication, `repo-recover`, and the `integrate`
train — leaves **one** commit on it: the `(incomplete step)` marker and its
`chore: attest verified recovery of preserved work` are branch state, and merging or
fast-forwarding the branch's provenance commits onto the base contradicts this
model. The attestation is not dropped — the publication commit's message ends with one
`Orchestrator-Recovered-Incomplete: <marker sha>` trailer per marker it recovered,
so the base still records that a step was left incomplete and a green gate cleared
it. A published subject names the change only; a marker's text never appears in
one. That `Orchestrator-` prefix is this host's, not the published default —
`config/onevcs.rules.yml`'s `trailer_prefix` is its one source, and a marker under a
prefix the rules file does not name is reported unrecognized and refused
publication rather than read or ignored. See [What the base branch carries for a
recovered incomplete
step](docs/repo-lifecycle.md#what-the-base-branch-carries-for-a-recovered-incomplete-step).
Provenance commits and marker-fragment subjects that already reached `main` stay
where they are: that history is never rewritten.

## After the main task

Act on two standing goals beyond the ask: (1) engineer the context for next time
(a real e2e for any journey a bug slipped through, a script for a step you did by
hand, a terse note here for what the code doesn't show); (2) keep the codebase and
environment clean and reproducible. Fold either in when it's the lowest-error path
to the ask; otherwise propose it as a follow-up. Skip busywork.
