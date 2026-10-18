---
title: "plan"
project: "planner-brief-example"
status: "todo"
metadata:
  "onepipeline.id": "plan"
  "orchestrator.unit": "Planning"
  "onepipeline.persona": "../personas/planner.yaml"
  "onepipeline.agent_graph": "graphs/planner.yaml"
---

# Give the read API a paginated node listing

Plan project: authoring:read-api-paginated-listing

## What

Design the decomposition for adding a paginated node listing to the read API: the
route and its request and response fields, the browser view that consumes it, and
the order the pieces have to land in. Answer with one node per subtask, each naming
its id, its persona, its dependencies, and its task.

The read API is `onepipeline-api` and the view is the `onepipeline-ui` bundle;
neither is built in this repository, so a subtask against either is a lifecycle node
naming that repository in its record's own `repositories`.

## Why

An operator supervising a long run cannot see past the first screen of nodes, so
they read the run's journal by hand to answer "what is still outstanding" — which is
the question the view exists to answer. Nobody has decided whether the cursor is an
opaque token or a node id, and that choice decides whether the view can deep-link.

## Acceptance criteria

- The plan is written as launchable local Markdown records in the `authoring` source
  — one project record and one task record per subtask, each task carrying a
  `## What` / `## Why` / `## Acceptance criteria` body a worker can be dispatched on —
  and its qualified `source:project` id is returned.
- Every task record names its subtask's persona, its dependencies, and the contract
  between it and the subtasks that consume it.
- The route's request and response fields and their types are stated, including
  what the cursor is and what an exhausted page answers.
- The split follows that contract: the node establishing the route lands first and
  non-breaking, so its consumers are unaffected and the gate stays green.
- Every choice the brief left open that could change what the user gets — the
  cursor's representation first among them, since it decides whether the view can
  deep-link — is asked of the manager and answered before the plan is finished; only
  a choice with no such consequence is decided in the plan, with its reason recorded.
- Every claim the completion report makes about the plan — which records exist,
  what each states, what was asked and answered — is true of the store as it finally
  stands.

## Additional info

### Operational notes for this host

**Run only the checks that exercise what you changed.** The tests that cover the code
you touched, the lint that reads your diff — and nothing wider. The repository's own
automation runs the full bar downstream on the change this work becomes: a `pre-push`
hook where that repository publishes locally, the host's required checks where it
publishes remotely. Neither is waiting for you to have run it first, so running it here
spends twenty to forty minutes of this dispatch learning what the merge path reports
anyway. That is measured rather than estimated: one node in this workstream spent about
84 minutes on three rounds of everything its repository could run, to reach lint findings
a diff-scoped run reports in two.

**Pick those checks from your change, and take the narrowest scope each one supports.**
One test module over the whole suite; the lint over your diff over the lint over the
tree. A repository's own fast deterministic tier is a fine choice when it is what
exercises your change — its `just --list` says which recipe that is — and a poor one when
you reached for it because it was the widest thing available. When a check you ran
reports something, fix that and run **that check** again; nothing here asks you to re-run
its neighbours to confirm, and a wider tier cannot re-check a finding it never made.

**A dispatch that changed nothing tracked owes none of them.** When your deliverable is a
document, a report, or an answer, there is no changed code for those checks to exercise
and you are complete without them — decide that from what the repository shows, never
because running them is slow or inconvenient. `git status` reporting nothing to commit is
then a correct and complete outcome, not a problem to solve by writing a file nobody
asked for.

**A publication check runs downstream, so no worker can run it and none is judged on it.**
The host's required checks and a repository's `pre-push` gate run on the change this
work becomes, after this dispatch settles: downstream publication checks are not
runnable by a worker, and a dispatch is never held to one having passed. That holds on a
retry too. A `checks-failed` retry — a re-dispatch onto the same branch carrying the
merge path's refusal and `onevcs`'s evidence — is judged on its task's stated local
acceptance checks: the repair of what the refusal named, proven by the checks the task
names, and never by the downstream check passing again, which can only happen once the
retry has settled. One retry that fixed and locally verified the failing test its
refusal named was failed for not re-running the required check, which left finished
work unpublished and its dependents skipped until a manager published it by hand.

**Never signal a process you did not identify by PID, and a PID you got from a pattern is
still a pattern kill.** Several managers share this host, and their dispatches, drivers,
publications and test servers run under the same few binary names — `just`, `node`,
`python`, `onepipeline-api` — so a kill by name knows nothing about *whose* work it
matched. The distinction is the tool rather than the pattern: `pgrep` and `ps` **read**,
and either spelling of them is fine, `pgrep -x` included; `pkill` **signals**, and it is
never the answer here, in any spelling. `-x` is the trap, because it reads as the careful
one — `pkill -f 'just gate'` at least needs a narrowing pattern, while `pkill -x just`
takes every `just` on the machine. Reading the table first and piping the pids into `kill`
is no way around it: that is the same pattern kill with more steps.

Two workers reached that place from different directions on 2026-08-24, while another
manager's dispatch had been live over an hour. One ran `pkill -TERM -x just`. The other
ran `ps -eo pid,args | grep 'onepipeline-api serve' | awk '{print $1}' | while read pid;
do kill "$pid"; done` — which obeys the letter of every paragraph below and does the same
damage, because `just dag-ui` and the e2e suites both spawn that server and a pytest run's
were alive at that moment.

**The one process you may signal is one you started yourself, so capture its PID as you
start it:**

    cmd & MYPID=$!
    kill "$MYPID"

Anything you did not start belongs to somebody. Read what it is, report what you found,
and leave it running.

**A wait written against a full command line never ends, because one of those command
lines is yours.** That is a different defect from the one above — it wedges your own
dispatch rather than somebody else's — and it is why `pgrep -f` cannot be waited on.
`pgrep -f` matches its pattern against every process's whole argv and excludes exactly one
process — itself — never the shell that invoked it. So any pattern naming the thing you are
waiting for is, by construction, a substring of the command line of the shell doing the
waiting: `until ! pgrep -f "just gate"` matches its own loop and never exits, and so does
the same loop written against the judged lint tier's script, `scripts/fetch-corpus.sh`,
or `publish-branch`. That is a defect in the polling mechanism rather than a fact about gates,
so it applies to every wait you write.

That has wedged four workers here, and then two more workers and the manager itself in one
night: seven instances of one defect. A crozier corpus audit waited in `until ! pgrep -f
'scripts/fetch-corpus.sh'` with no fetch running at all — the only two matches were its own
polling shell and the manager's diagnostic shell — and waited until it was interrupted. A
`check-plan-bar-conflict` dispatch waited on `! pgrep -f` naming the judged lint tier's
script, whose negation could never become true, and was cancelled and killed; its replacement then reached for
`pkill -f` against three patterns, which is not a wait at all but the same self-match with a
signal attached, and is refused above. And the manager, an hour after instructing a worker
about this, guarded a launch with `pgrep -f "publish-branch" && echo ABORT || launch`, whose
pattern was a literal substring of the launching shell's own command line: it aborted
unconditionally and then reported a publish as RUNNING that did not exist. That last one is
the evidence that matters most — a rule its own author reproduces an hour after issuing it
is a rule whose wording is the problem rather than its reader.

**Wait with a sentinel file.** Background the whole command you are waiting on — one
command, so that what you waited on is what you meant to run — and poll for a file only
that invocation can write:

    ( just test > /tmp/check.$$.log 2>&1; echo $? > /tmp/check.$$.exit ) &
    until [ -f /tmp/check.$$.exit ]; do sleep 20; done

**A process must finish inside the turn that started it.** Backgrounding is how you wait
on a slow command *within* one turn, which is why the launch and the wait sit together in
that example — both halves belong to the same turn. What does not survive is a background
process left running when the turn ends: nothing carries it across, and the launch gives
you no sign of that, because it succeeded and the turn then ended normally. The loss
shows up a turn later as a sentinel that never appeared and a recipe terminated by
signal, with the whole run to pay for again. It bites hardest where it is most tempting,
because a worker reaches for backgrounding exactly when a command is slow enough to
threaten the turn — here the judged lint tier and a whole test tier, which are the two
runs a node most needs to have made. So run a slow check in the foreground, or background
it and wait out its sentinel before your turn ends; never end a turn intending to read a
result on the next one.

**To ask whether a process is running at all, match the executable, not the command line.**
`pgrep -x onepipeline` matches process *names* exactly — the binary, not its arguments — so
the shell doing the asking, whose own name is `bash`, cannot match it however the pattern
is spelled. Where the binary name is not distinctive enough to identify what you mean
(`just`, `node` and `python` run everything), read the process table instead and drop the
one self-match that form has:

    ps -eo pid,args | grep -v grep | grep 'lint-llm-diff'

Both of those are **reads**, which is what makes `-x` safe here and forbidden the moment
the tool signals. The replacement for the worker wedged above reached for `pkill -f "just
gate"`, `pkill -f "just check"` and `pkill -f "nx run"` while another manager's run was
live. Read to learn what is running, report what you found, and signal nothing you did not
start.

**One sentinel and one log per invocation, and look before you launch one.** That pair is a
shape rather than two literal paths. Several dispatches run on this host at once and they
share `/tmp`, so a second worker writing `/tmp/check.log` writes into the first one's — and
`until [ -f /tmp/check.exit ]` returns immediately on somebody else's exit file, reporting
their result as yours. Name both after the invocation (`/tmp/check.$$.log`, or the node
id), and, before starting it, check whether its sentinel path is already in use by another
invocation; if so, choose a different invocation-specific pair. This path-ownership check
does not prohibit concurrent checks or judged tiers. Three workers collided on shared names
today and not one of the three failures read as what it was: two
deterministic-tier runs deadlocked against the same cargo target-directory lock, so both
logs sat unchanged and neither progressed — about fifteen minutes lost and three
interruptions to a live turn; two whole-e2e runs wrote to one log, and stopping the
duplicate left four `SIGTERM` entries in it that read exactly like real test failures; two
judged-tier runs raced and wasted a roll, which was harmless only by luck. Read a log that
has stopped growing as two commands blocking each other before you read it as one command
working.

<!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The source of this contract is the engine that exports the variable, and it lands in the same run as this paragraph: there is no released copy to reconcile against yet, and this task is explicitly told not to go looking for one in an installed engine. `tests/test_dispatch_appendix.py` holds these four properties and no more, so the day the engine side is adopted the drift gate has an exact set to reconcile. -->
**Write the files this dispatch needs under `ONEPIPELINE_NODE_SCRATCH_DIR`, rather than
under a `/tmp` path you invented.** It is an absolute path to a directory that exists and
is writable when the dispatch starts, is unique to this dispatch, and is not removed while
it runs.

**Root-owned files after a container run.** The pre-push visual guard captures in Docker
as root and can leave `.nx/` and `dist/` unwritable, failing the *next* check with `NX
Permission denied (os error 13)`. There is no passwordless sudo; repair from inside a
container:

    docker run --rm -v "$PWD":/w -w /w mcr.microsoft.com/playwright:v1.61.1-noble \
      chown -R "$(id -u):$(id -g)" /w

**If a test fails in a way that makes no sense, check the host before the test.** On
2026-08-19 two publication attempts against the same already-green branch failed on two
unrelated tests — a 5-second timeout, then a browser remote that never came up — and both
were written up as flakes. The host was at 197G/197G, and the next attempt said so
outright with `No space left on device`. A full disk does not announce itself to a test
runner; it announces itself as an unrelated test failing. One command rules it out:

    df -h /home .

**If a rule looks wrong or misapplied, say so with evidence rather than editing it.**

**One suppression policy, and this is the whole of it.** A site-scoped `ignore` directive
is permitted where the rule is genuinely misapplied at that site **and** the directive
carries a substantive reason saying why. What is forbidden is silencing a finding you
have not answered so that a count moves. What you owe an account of is **every
suppression your own diff writes — added, moved or rewritten — and every one already
standing over a line that diff changes** — each with its site and its reason, in your
completion report, so the manager reads what you left rather than discovering it. Both
halves are decided from the diff alone: a directive line inside it, or a changed line
inside the region a directive covers. A file-scoped directive is outside both unless your
diff writes the directive itself — its region is every line of the file, so counting it
would make this file's own header a debt of every change that edits a word of it.
Suppressions elsewhere in the tree are not yours to inventory: this repository carries several hundred standing directives, and
a list of them would be longer than the report is read under while saying nothing about
the change under review.

That scope is stated because the sentence it replaces read two ways at once: its first
clause reached every directive anywhere in the repository, while its second — *so the
manager reads what you left* — was about this dispatch's own work, in the same breath. A
node was failed on the wider reading for not accounting for suppressions it had never
touched, on a tree whose every target had just run from cold and come back green; it cost
a third dispatch of a run's final node. The reading above is the one the purpose clause
and the scale both support, and it is written so that a worker complying with it and a
judge reading it adversarially land in the same place. It says which lines rather than which sites for the same
reason: *site* was itself readable two ways, and the first worker to apply the sentence
could not decide whether the file-scoped directives at the head of `AGENTS.md` were
owed by a change that edits one of its paragraphs. Apply that same test to whatever
replaces it: read it as a worker trying to comply and as a judge looking for a way to
refuse, and if the two can land in different places it is not one source yet.

`AGENTS.md` states no suppression policy of its own and points here, because this file is
what your judge reads beside your task: while that document said it twice, one
dispatch added four site-scoped ignores each carrying a substantive reason and was failed
for *"the task's categorical instruction never to suppress a rule"* — work that was
correct under one of this repository's own readings and refused under the other.

**`--no-verify` is not an acceptable response to a slow or inconvenient hook.** The hooks
are this repository's enforcement point — there is no CI — so bypassing one commits work
that nothing has checked, and nothing you run afterwards catches it: a check runs over
the finished tree, by which point a bypassed commit is simply history. Two workers
today reached for `git commit --no-verify`, both on a commit they had judged mechanical,
and one of the two was a *merge* commit — a conflict resolution, which is the single most
likely commit in a dispatch to be wrong. No check caught either one; the monitor caught
both. If a hook is slow, wait for it. If it refuses, it is telling you something about the
commit in front of you, and the answer is to fix the commit.

**Commit a coherent working piece the moment it works.** A dirty worktree does not
survive this dispatch: what is uncommitted when your last turn ends is what nothing
recovers — not a retry, not a recovery verb, not a manager reading the branch. So each
piece is its own commit as you finish it, rather than one commit held back to the end.
*The moment it works* is the moment you have seen it work: the checks that piece needs
are run before it is committed rather than after, so that each commit is one you have
evidence for.

**Ask rather than stop.** When you cannot proceed without a decision that is not yours —
a frozen contract you would have to amend, a goal that reads two ways, a constraint you
would have to relax — put the question to your manager over `$ORCHESTRATOR_ASK_MANAGER`,
which every dispatch carries, and go on working on whatever does not depend on the answer.
Ending your turns with the work undone and the question unasked is the one response
nothing can recover: a worker that reasoned about a frozen contract correctly, declined to
amend it unilaterally, named two options for its owner, and then stopped for three turns
lost about fifteen minutes of correct work and settled reporting `ahead of main: 0
commit(s)`.

**Draft a follow-up that can wait; never draft one that cannot.** Work you notice that
is outside your subtask — a bug beside the code you touched, a missing test or script, a
stale document, an improvement to this harness or to the context agents are given — is a
follow-up, and doing it is not yours. Record each one the moment you notice it by piping
its body into `"$ORCHESTRATOR_FOLLOW_UP_DRAFT"`, whose `--help` names the flags and the four
headings that body carries; the command stamps where it came from. Do not collect them into
a list at the end of your last message instead, because nothing reads that list. A draft is
unverified — a follow-up agent checks every one after the run — and it is not your
completion report, which still says what you did. Drafting is only for what can wait:
anything blocking, whether a decision fork, a constraint you cannot meet, or something your
manager should act on now, goes over `$ORCHESTRATOR_ASK_MANAGER` immediately, as the
paragraph above says, and is never drafted in its place.

**A GitHub rate-limit refusal that `gh api rate_limit` disagrees with is the secondary
limiter.** The primary limit is the one that endpoint reports and the one a wait answers.
The secondary limiter is reported by nothing, polled by nothing, and not waited out by
polling — every further attempt extends it. So when a `gh` call is refused for a rate
limit while `gh api rate_limit` still shows budget, stop making that call: deliver the
result another way, name in your report what was refused and what you tried, and leave the
retry to a person.

**Publication goes through the harness.** The session branch reaches its remote only
through `onevcs publish "$ONEVCS_SESSION"`, and its change request is lifted and landed
by the lifecycle after you settle. No `git push` of the session branch, no `gh pr create`
for it, no `gh pr ready`, no `gh pr merge`. Finish the branch, commit everything, leave
the tree clean, and report. Landing it is explicitly **not** yours to perform and **not**
part of your acceptance criteria. Two things are yours, and each only when this task's
own `## Additional info` — its own words above these notes — says so:

- **Only when the task's `## Additional info` says the change request may be published
  early**: `onevcs publish "$ONEVCS_SESSION" --draft [--title T] [--body-file PATH]` opens
  the session's change request as a draft, and a later `publish --draft` pushes new
  commits onto that same change request and never opens a second. Commit everything
  first — a dirty tree is committed for you, under a provenance you did not choose.
  `onevcs change describe "$ONEVCS_SESSION" --body-file PATH` replaces its description
  and `onevcs change show "$ONEVCS_SESSION"` reads it back. The description you leave is
  what the drafter finishes from, so start it with what only you know — the evidence and
  where it is. Never mark the draft ready; the lifecycle does, after the description is
  finished. `ONEVCS_SESSION` is in every lifecycle dispatch's environment.
- **Only when the task's `## Additional info` authorizes a throwaway demonstration change
  request**: after the draft is open, cut a branch from the session branch, commit the
  demonstration on it, push it with `git push -u origin <branch>`, open it with
  `gh pr create --draft --base <session branch> --head <branch> --title "DO NOT MERGE: …"`,
  capture the evidence — `gh pr view <n> --comments`, `gh pr checks <n>`, and `gh api`
  **reads** of that change request's own comments, checks and reviews, never a write to
  anything — then close it with `gh pr close <n> --delete-branch`, return to the session
  branch, and delete the local branch — a local branch left behind makes the session's
  close refuse. Its base is always the session branch, never the repository's base; it
  is never marked ready; it is closed before you finish.

Everything else a push or a change-request verb could do is still not yours.

**Every claim you make about the finished work is true of the tree as it finally
stands.** That is the property you are held to, and it says nothing about where your
report sits. This conversation does not end when you report — your supervisor keeps
asking, and answering well means running things — so a report required to be this
dispatch's literal final output is one no correct worker can give. What is required
instead is that nothing you have said about the work is untrue of the tree by the time you
stop.

When work follows your report, say in that same turn what changed and what you re-ran. A
correct delta satisfies this exactly as fully as restating the whole report does. Silence
does not satisfy it at all. Your last substantive turn should leave a reader able to say
what the finished tree contains and what was verified about it, whether that comes from
one report or from a report plus the deltas after it.

**A claim about a check, a test, a lint run, or a commit that was not run or was not made
is false, and fails on its merits.** A report asserting a check passed on a commit where
no such run occurred is that case, and one node of this host's history was correctly
failed for it. So do not carry a claim forward across a change that could have invalidated
it — re-run what the change could have broken, or say which claims you have not re-checked.

**Evidence deliberately about an earlier or induced state is not a false claim.** Proving
that an assertion can fail means making it fail, reading the message, and reverting;
proving that a message used to say nothing means quoting the run from before the change.
A citation of a run taken before a change, or of a failure induced on purpose as evidence,
is correct evidence — provided the citation says which it is. One node of this host was
failed for exactly that: its own criteria required its assertions be observed failing, and
the resulting-tree property above was read as forbidding the citation that requirement
produces.

**A commit that cannot affect a check leaves that check's evidence standing.** *Could have
invalidated it* is a condition, and a commit that fails it does not reach the claim — so a
run taken before a later commit is still evidence for the tree after it, provided the
report names that commit and says why it is inert for that check. What decides it is what
the check reads, never what kind of change the commit is. A comment-only or
documentation-only commit to content a check does not read is inert for that check; the
same comment is not inert for a check that reads comments, which a judged lint does, so
that one is re-run or named as not re-checked. A commit touching anything a check reads is
not inert for it, however small. Two dispatches of one node, about 45 minutes each, were
refused over nothing but a comment-only final commit after their cited check runs, with no
change in whether either tree was correct, and the manager had to send *re-run after every
commit* three separate times to get past it: read as absolute, the conditional rule above
is one no worker can meet, because every commit comes after the last run.

The ordering demand this replaces — that the report come after everything else, and that
anything found later be repaired and the whole report written again — is **withdrawn**. It
failed six of fourteen nodes in one workstream, each with complete committed work, a green
deterministic tier, and no acceptance criterion found unmet, and two of those carried an
escalated warning about it in their own task and failed anyway. A bar finished work
cannot clear teaches everyone to route around the thing that enforces quality; the
property above is what that demand was serving, and it is what survives.

### State the bar in `## Acceptance criteria`, not only here

A judge reads this node's `## Acceptance criteria` and its own review bar. Where the
criteria are silent about something the bar demands, it imports the demand and applies its
own reading of it — and finished, gate-green work has been failed twice that way: once for
running the gate's parts separately, and once for never having *"provided a final verified
completion report"*, a demand in neither the task nor the shared completion clause.

So every demand this node will be held to is stated as a criterion of its own, including
the two the bar makes of every implementation dispatch:

- the behavior this node adds is **proven end to end** by a test or journey that drives the
  real interface, rather than by inspection;
- every claim the dispatch makes about the finished work — what it verified, and the
  evidence for it — is **true of the tree as it finally stands**.

Criteria state properties of the finished tree; the commands that produce them belong in
this section. Whether the criteria answer every demand their own bar — or this appendix —
makes of them is read by `just review-plan <source:project>`, which spends a judged turn
on it and judges it by meaning: criteria that state a demand in their own words answer it,
and no criterion is refused for failing to use a particular phrase. Nothing reads it
deterministically any more, because the phrase matching that did refused wordings the same
review had just asked for.

## The plan checklist this plan is judged against

This dispatch has a second judge beside its reviewer: the plan checklist, `config/plan-checklist.llmlint.yml`,
which llmlint runs over the description of the plan and every task document each time
the reviewer decides. The panel is not done while any rule below fails, so write the
description and every task against these rules on the first draft. They are listed as
llmlint resolved them when this task was written, the rules of its plugins included, and
are read against a plan rather than code, under the framing the checklist judge is given.

### How the `plan` judge reads the plan

#### You are reading a plan, not code

Every target file is a document of one execution plan, written before any of its
work exists. Each is either the plan's description (its goal, its workload and
sizing, its answer about 10x usage, and its budget checklist) or one plan task's
Markdown document. A task document carries YAML frontmatter, whose
`orchestrator.budgets` record lists the budgets that task owns, and a body with
`## What`, `## Why`, `## Acceptance criteria`, an optional `## Budgets` section
and `## Additional info`. Text under `### Operational notes for this host` is a
standard appendix every task carries: read it as context, never as the plan's
own content, and never report a violation inside it.

The plan describes work in a target repository you cannot see. Judge each rule
against what the plan says will exist once its tasks are done. A rule written
about code, tests, scripts, `budgets.yaml` files or project definitions is
judged against the plan's own statements of them: where each budget will be
registered and what its command will read and measure, what each test will
assert and with what doubled, and which project a test will land in and what
that project will depend on. A plan that states nothing a rule could judge
makes that rule not relevant; never report a rule against a file only because
the repository it describes is not visible to you.

### The rules

#### `budgets_track_product_owner_outcomes`

Budgets track product-owner outcomes, with finer figures as their breakdown.
false when the file registers a budget whose figure is a part of another
budget's figure the repository registers — a step, phase or operation of it
— or the same concern as another budget in a second unit, such as requests
beside points for one quota. true otherwise. Whether a product owner would
care about a figure is never judged.

Judged when the plan registers or keeps a budget, in a task's budget record or a description's budget checklist.

#### `budgets_reuse_gate_telemetry`

true when a budget whose behaviour an existing gate's tests or runtime already
exercise takes its figure from telemetry those runs record and save
temporarily (for example as the test target's output), its `command` only
analysing that data. Also true when no existing gate exercises the measured
behaviour, or when recording it there would cost more than a standalone
measurement. false when a budget re-runs a scenario an existing test already
runs, only to count or time it, where adding telemetry to that test or its
runtime code would be cheaper than the extra gate time.

Judged when the plan registers a budget and states what its command runs or reads.

#### `budgets_scoped_to_minimal_tree`

true when everything specific to a `budgets.yaml`'s measurements lives in the
tree that file's directory roots: the commands it names, the tests they run,
and budget-only fixtures. Calling shared code that lives elsewhere is fine —
a common harness, the code under measurement, a fixture other tests also
use, or telemetry recording added to an existing test or runtime code. false
only when something that exists solely to serve one of its budgets
lives outside that tree.

Judged when the plan registers a budget and states where its budgets file, command, or budget-only fixtures will live.

#### `budget_commands_measure_directly`

true when each budget's `command` runs its measurement directly, or through
one generic runner shared by every budget. false when it goes through a
per-budget wrapper that re-lists budget ids or re-checks that a result was
written — work onebudgetspec already does.

Judged when the plan registers a budget and states its command.

#### `tests_hold_no_nonfunctional_thresholds`

false when the file asserts a cost figure against a fixed bound — wall
clock, request, query or API-point count, payload or artifact size, memory
or money at a realistic workload — or tells two behaviours apart by elapsed
time ("returned within 2 s, so it did not wait") rather than asserting the
observable event. true otherwise, including a configured timeout, deadline
or backoff asserted near its value, an exact count of an action, the
absence of an operation, a limit the product itself enforces, and a timeout
that only guards against a hang.

Judged when the plan states what a test it adds or changes will assert about a timing, size or count figure, or how it bounds a wait.

#### `expensive_tests_stay_behind_their_own_edge`

true when the test lives in a project whose graph edges reach only what it
actually tests, so an unrelated change cannot make it run. false when it sits
in a broadly-depended-on project, or its project depends on unrelated code
(an import, a `dependsOn`, or an implicit dependency), so every change in the
repo pays for it.

Judged when the plan adds or changes a test that is slow or reaches an external service, or a project definition or dependency edge that owns one, or makes a budget's figure depend on one.

#### `budget_descriptions_durable_and_terse`

true when each budget's `description` says, in one or two sentences, what is
measured and what it protects. false when a description reads as session or
changelog narrative — base figures from an old commit, a history of the
figure, or what each journey did — or restates the budget's own fields: its
command, unit, direction or threshold.

Switched off in this checklist: never judged.

#### `onebudgetspec_is_the_only_judge`

true when the code a budget's `command` runs reports its figure (to the file
`ONEBUDGETSPEC_RESULT` names, or as its elapsed run) and leaves the comparison
with the threshold to onebudgetspec. false when that code reads a
`budgets.yaml`, or asserts the figure against a threshold itself.

Switched off in this checklist: never judged.

#### `instruction_layer_localized`

true when subtree rules live in a nested `AGENTS.md` inside each project,
leaving the root `AGENTS.md` to repo-wide constraints only. false when
project-specific rules are piled into the root `AGENTS.md`.

Switched off in this checklist: never judged.

#### `new_projects_keep_the_graph_uniform`

true when the project declares the repo's standard target names for the work
it has (`build`, `lint`, `test`, `typecheck`) and the boundary tags its layer
requires, so `nx affected`/`run-many` fan out by name and the module-boundary
rule can judge it. false when it invents a target name the repo already names,
omits a target it has work for, or is untagged where its layer is tag-enforced.

Switched off in this checklist: never judged.

#### `code_lands_in_the_domain_that_owns_it`

true when a new or changed type, interface (a trait, protocol, abstract base,
or port), store or repository interface, or helper lands in the project of
the domain that owns it — the project whose other code changes when that
concern changes — or in a project that is a genuine cross-domain contract: a
shared identity, an envelope or representation rule, a wire or storage
format, or an interface every domain must agree on. A shared package is a
contract only if adding or changing one domain's concept does not require
editing it. A contract holds what every domain must agree on and never
enumerates the domains: a closed sum type, registry, or table in a central
package with an entry per domain's things is a bag with a type signature,
however it is persisted or served. false when it lands in a project grouped
by the kind of code it is rather than by the concern it serves — a central
`types`, `models`, `store`, `data-access`, `utils`, `common` or `shared`
package several domains depend on — so that a change to one domain's concern
re-touches a package every other domain builds against; and false when it
adds one domain's entry — a variant, a registration, a row, a case — to a
closed sum type, registry, or table in a central package, or declares one
domain's structs in the central package because a central enumeration
reaches them. The project's name is a signal, not the test: a `types`
project holding only the identities, the envelope and the formats every
domain agrees on is a contract, and a `contract` project holding one
domain's structs beside another's is a bag.

Switched off in this checklist: never judged.

#### `plan_criteria_satisfiable_inside_the_dispatch`

true when every acceptance criterion can be met from inside the task's own dispatch,
from what its worker controls. false when one rests on something that happens only
after the dispatch settles or outside it: a change request merging, a release or
deploy, the merge path's or a host's required checks passing, a scheduled job, or a
third party acting. A criterion naming a later comparison while stating that its
result is not the task's bar, one about the dispatch's own judged lint, and one about
a draft or demonstration change request the task grants its worker rest on nothing
outside it.

Judged when the task document has an `## Acceptance criteria` section.

#### `plan_criteria_require_realistic_tests`

true when every criterion asking for proof of behaviour requires a test that drives
the real interface the behaviour is reached by, doubling at most a paid, external or
third-party boundary, and asserts the behaviour itself rather than internal state or
a snapshot of output.

Judged when the task's acceptance criteria ask for a test or other proof of behaviour.

#### `plan_workload_states_numbers`

true when every realistic workload the file states gives its scale in numbers: how
many of what, how often, how large. false when one is stated only in words such as
"typical", "a few" or "normal use".

Judged when the file states a realistic workload, in a plan description's workload or a budget's `workload`.

#### `plan_ten_x_names_a_covering_budget`

true when the 10x answer names, by id, a budget the plan registers or keeps whose
figure grows with the effect the answer names.

Judged when the description answers what the product owner notices getting worse first at 10x realistic usage.

#### `plan_budget_command_measures_its_figure`

true when every budget in the task's record names a `command` that, as the plan
describes it, produces that budget's `measure`, in its `unit`, at its `workload`.

Judged when the task's budget record registers or changes a budget.

#### `plan_budget_basis_matches_evidence`

true when each such budget's `evidence` reports a measurement of its figure at its
`workload`.

Judged when the task's budget record holds a budget whose `basis` is `measured`.

#### `plan_budget_inner_measure_has_reason`

true when every budget measures the outermost figure its concern is felt at, or,
where it measures one step, component or inner process of what the user waits on or
pays for, gives a non-empty `inner_measure_reason` saying why.

Judged when the task's budget record registers or changes a budget.

#### `plan_criteria_omit_repo_wide_budgets`

true when the task's acceptance criteria name only budgets the task itself registers
or changes. false when a criterion holds the task to a repository-wide budget it
does not own, such as the gate's own time or another root budget.

Judged when the task document has an `## Acceptance criteria` section.

#### `plan_budget_owner_criteria_name_budget_and_workload`

true when the task's acceptance criteria name, by id, every budget its record
registers or changes, each with the workload it is measured at.

Judged when the task's budget record registers or changes a budget.

#### `plan_budget_names_its_telemetry_source`

true when the task names, for every such budget, the test target or targets whose
tests record what its command reads (the project and target, or the test tier, that
runs them, not only test files) and the project whose budgets file registers it.

Judged when the task's budget record holds a budget whose figure is read from telemetry tests record, by a `source` of `telemetry` or a command that analyses recorded telemetry.

#### `plan_budget_measurement_is_decided`

true when the task states, for every budget it owns, one decided way its figure is
taken (read from telemetry its tests record, or measured fresh by its command)
together with any label the budget carries, such as `host`. false when it leaves the
way to the worker, as an either/or or a "say which".

Judged when the task's budget record registers or changes a budget.

## Additional info

**This dispatch works in a checkout it does not own, so it commits nothing.** It is a
direct node, dispatched into the checkout the flow was launched from — the shared
canonical checkout that several orchestrators use — rather than into a worktree of its
own. So it may write only to gitignored paths, may not commit, may not cut a branch, and
may not leave the checkout on any branch but its base.

That exempts it from one clause of the shared completion bar every dispatch on this host
is judged against — "with every change this dispatch made committed and nothing
half-applied left behind". Here there is nothing to commit, and a clean `git status` is
the correct and complete outcome: what this dispatch writes to a gitignored path is the
deliverable, and committing it would be the failure rather than the proof.
