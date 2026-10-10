---
title: 'feat(create-repo): make onebudgetspec budgets a baseline of every repository'
status: todo
project: nonfunctional-requirements-are-budgets
depends_on:
- id: feat-create-repo-state-when-a-requirement-is-a-budget-and-lint-tests-that-assert-one
  kind: blocks
  item: task
metadata:
  onepipeline.execution_checkout: nickderobertis__dero-skills
  onepipeline.id: cr-budgets-baseline
  onepipeline.max_turns: 100
  onepipeline.persona: engineer
  "onetaskgraph.template": {"answers_digest":"sha256:db450578443639d5e09f57c49fb0e606cc2b7bf7057b44567a59ebd8c82ff4b2","body_digest":"sha256:7ed2c3b0799543cdaf1c70eb9453d3a0dbcca00826f66158111c16ecfa37b95b","digest":"sha256:2b9a5855d9a64466bf9c0bd5843e0b8d79f1055e8187d7526c045a2253f22ff3","template":"onepipeline:plan-task"}
  "orchestrator.plan-review": {"by":"review-plan","key":"71a0b4ed2df0cdd2e347f0454f2ca7e80ce16412223820d557dcd8624ccb232e","reviewed_at":"2026-10-08T13:37:12.529551+00:00"}
  "onetaskgraph.copies": {"plans":"plans:I_kwDOS0jAoc8AAAABVmqJSQ"}
  "orchestrator.budgets": []
repositories:
- github.com/nickderobertis/dero-skills
---
## What

In dero-skills' create-repo skill (`skills/bootstrap/create-repo/`), make onebudgetspec budgets a baseline requirement of every repository the skill composes, not an opt-in.

**The composer (`scripts/compose_repo_plan.py`).**
- Remove the `--tool` flag outright. onebudgetspec was its only choice.
- `references/tools/onebudgetspec.md` joins every composed plan, beside the always-applied references.
- The fragment `assets/llmlint/tools/onebudgetspec.llmlint.yml`, pinned `@1` at its raw `main` URL, joins every composed ongoing `llmlint.yml`. That is the generated repository adopting the plugin. Neither `base.llmlint.yml` nor any other fragment names it.
- `--wiring REPO_DIR` always applies the budgets setup step, with no other flag: the exact pin, the `budgets`/`budgets-host` target defaults, and the `budgets` recipe that `check` depends on.
- The plan's recorded invocation and its "References composed" line follow.
- `--list` and `--help` stop naming `--tool`.

**The baseline checker (`scripts/check_repo_baseline.py`).** Hold every repository to the budgets setup, whatever its recorded composition says:
- onebudgetspec pinned in a lockfile;
- every `budgets.yaml` reached by `check`;
- `llmlint.yml` adopting the fragment.
A repository that is not wired gets a finding whose fix names `compose_repo_plan.py --wiring <repo>`. Drop the "declares the tool" gate. The user accepted that every existing create-repo-built repository that is not wired gets that finding the next time the checker runs on it.

**Prose.** Every place that describes budgets as opt-in, or names `--tool onebudgetspec`, says instead that budgets are part of the baseline. That includes:
- `references/tools/onebudgetspec.md` (its opening and its setup-step and verification sections);
- the fragment's header comment;
- `SKILL.md` (the composer usage line, the "Tools (opt-in)" bullet, the composer description);
- `references/composing.md` (step 3 and the axis table);
- the checker's and composer's docstrings and help;
- `tests/AGENTS.md`;
- the test module docstrings that say "opt-in".
File paths stay where they are, because the fragment URL is a contract adopters pin.

**dero-skills itself.** The checker audits this repository in its own gate (`repo-baseline:test`), so wire dero-skills the same way:
- onebudgetspec pinned;
- the `budgets`/`budgets-host` target defaults in `nx.json`;
- a `budgets` recipe that `just check` runs at its tier;
- the fragment adopted in its root `llmlint.yml`, as an in-tree plugin path like the other fragments it dogfoods.
Then fix whatever the fragment's rules find in dero-skills' own tests, the code rather than the rules. Keep the new recipe out of the expensive tiers' fan-out, which dero-skills' `AGENTS.md` "Project graph" rule protects.

**Tests.** Update every test that drives `--tool` or asserts the opt-in:
- `tests/test_compose_repo_plan.py`;
- `tests/e2e/test_compose_repo_plan_e2e.py`;
- `tests/test_check_repo_baseline_onebudgetspec.py`;
- `tests/test_onebudgetspec_fragment.py`;
- `tests/external/test_onebudgetspec_wiring_e2e.py`.
After the update they prove the baseline:
- a plan composed with no flag but a shape and a language includes the reference and the fragment;
- `--wiring` with no other flag wires a repository;
- `--tool` is refused as an unknown flag;
- the checker reports an unwired repository whose `AGENTS.md` names no onebudgetspec reference.


## Why

The user ruled that budgets are a baseline requirement of every repository create-repo builds, not an opt-in. Every non-functional requirement with a number should be auditable, trackable and adjustable in one place, a `budgets.yaml`, from a repository's first commit, so no new repository starts with constants buried in its tests. Because the user does not want existing llmlint consumers to get the budget rules without choosing them, a generated repository adopts the fragment by listing its URL in its own `llmlint.yml`. The always-on base fragment does not grow.


## Acceptance criteria

- `scripts/compose_repo_plan.py` has no `--tool` flag and refuses one as unknown. A plan composed from a shape and a language alone includes `references/tools/onebudgetspec.md`, and its composed ongoing `llmlint.yml` lists the onebudgetspec fragment's `@1` URL. `--wiring REPO_DIR` with no other flag pins onebudgetspec, adds the `budgets`/`budgets-host` target defaults and adds the `budgets` recipe that `check` depends on, idempotently. `base.llmlint.yml` and every other fragment still name no onebudgetspec rule.
- `scripts/check_repo_baseline.py` holds every repository to the budgets setup whatever its recorded composition, and reports an unwired repository whose `AGENTS.md` names no onebudgetspec reference with a fix naming `--wiring`. A test drives the real checker over such a repository and over a wired one.
- No file in the skill describes budgets as opt-in or shows `--tool` as a supported invocation. That covers usage lines and examples, help text, docstrings and prose, including `SKILL.md`, `references/composing.md`, `references/tools/onebudgetspec.md`, the fragment's header, the composer's and checker's help and docstrings, `tests/AGENTS.md` and the test docstrings. The only places `--tool` may appear are tests that pass it to prove the composer refuses it as an unknown flag. The reference's "when is a requirement a budget" section and the rules `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes` are unchanged from how `cr-budget-rule` landed them.
- dero-skills itself is wired: onebudgetspec is pinned in its lockfile, `nx.json` carries the `budgets`/`budgets-host` target defaults, its `check` recipe runs the budgets recipe at its tier, and its root `llmlint.yml` adopts the fragment. The baseline checker passes over dero-skills, and a judged llmlint run of the fragment's rules over dero-skills' test code and every `budgets.yaml` it holds finds no violation, with no suppression added for them.
- The composer and checker tests, the external wiring journey, and the create-repo skill eval pass on the finished tree. The skill eval's produced repository passes the baseline checker, so it is wired for budgets.
- On the finished tree, the deterministic llmlint `validate` passes and the diff-scoped judged lint over the branch is green.
- Every claim the completion report makes about the finished work and its verification, including which checks ran and with what result, is true of the tree as it finally stands.

## Additional info

The user's and manager's rulings this node applies are fixed and are not reopened:
- budgets are baseline, not opt-in;
- the `--tool` flag is removed outright rather than left with no choices;
- `--wiring` always applies the budgets setup;
- file paths stay;
- dero-skills is wired itself;
- unwired existing repositories get a checker finding.

This node builds on `cr-budget-rule`, which added the rules `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes` and the "when is a requirement a budget" section to the same reference and fragment. Keep that section and those rules as they landed. Any budget dero-skills registers sits at the level the product owner tracks, and its command analyses telemetry its tests already record, as that section states. Edit only the opt-in framing around them. The fragment URL `https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1` and its rule names are a contract other repositories pin. A shared interface is never changed unilaterally. If you find a reason to depart from anything this task states, ask the manager through `$ORCHESTRATOR_ASK_MANAGER` and keep building against the agreed surface until it rules.

If the checker's existing check that `llmlint.yml` adopts the fragment cannot recognise dero-skills' in-tree plugin path, make it recognise either spelling rather than giving dero-skills a URL it does not use elsewhere.

Removing a command-line flag from a skill script can read as a breaking change under dero-skills' semantic-release. The plan titles this `feat`, because the skill's consumers are agents reading the current `SKILL.md`. If dero-skills' own `AGENTS.md` or release rules say otherwise, ask rather than retitle.

Read dero-skills' root `AGENTS.md` and `skills/bootstrap/create-repo/tests/AGENTS.md` first, and hold the change to them:
- skill scripts stay runtime-independent;
- tests are real, never mocked;
- the external journey installs the real pinned release from npm;
- the skill eval sits in `skilltest`;
- commits are Conventional Commits.

Checks that exercise this change, each run when it bears on what you just changed:
- `just test` or the affected `test` targets for the composer and checker tests;
- `just baseline` for the checker over dero-skills itself;
- `just external` for the wiring journey, which needs the npm registry;
- `just lint-llm-validate`;
- `just lint-llm-diff` over the branch;
- `just skilltest` for the create-repo skill eval, which drives the whole skill through a real harness and runs the baseline checker on the repository it produces. It is the slowest of these, so run it once the rest is green.


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
the same loop written against `llmlint-judge.sh`, `scripts/fetch-corpus.sh`, or
`publish-branch`. That is a defect in the polling mechanism rather than a fact about gates,
so it applies to every wait you write.

That has wedged four workers here, and then two more workers and the manager itself in one
night: seven instances of one defect. A crozier corpus audit waited in `until ! pgrep -f
'scripts/fetch-corpus.sh'` with no fetch running at all — the only two matches were its own
polling shell and the manager's diagnostic shell — and waited until it was interrupted. A
`check-plan-bar-conflict` dispatch waited on `! pgrep -f "llmlint-judge.sh"`, whose negation
could never become true, and was cancelled and killed; its replacement then reached for
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
through `onevcs publish "$ONEVCS_SESSION"`, and its change request is lifted, landed or
kept for the user's review by the lifecycle after you settle. No `git push` of the session branch, no `gh pr create`
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
  where it is. Never mark the draft ready yourself; after you finish, the lifecycle lifts
  it or keeps it as a draft for the user's review according to the repository's policy.
  `ONEVCS_SESSION` is in every lifecycle dispatch's environment.
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

**A branch you resume may already be on its remote, and a published branch only grows.**
A retry — a `checks-failed` one among them — re-dispatches onto the same branch, which an
earlier publication may already have pushed. So check before you touch its history: `git
fetch` and `git log origin/<branch>`, or the published commit your task's context names.
The repair of a branch already on its remote is new commits on top of the remote's
commit — never an amend, a rebase, a squash, a reset or a force-push of commits already
there. One retry that fixed both refusals its merge path named by amending the two commits
already pushed was refused `non-fast-forward` when it published, and settled
`push-rejected` with the correct tree stranded on the host.

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


<!-- onetaskgraph:template-answers
acceptance_criteria:
- '`scripts/compose_repo_plan.py` has no `--tool` flag and refuses one as unknown. A plan composed from a shape and a language alone includes `references/tools/onebudgetspec.md`, and its composed ongoing `llmlint.yml` lists the onebudgetspec fragment''s `@1` URL. `--wiring REPO_DIR` with no other flag pins onebudgetspec, adds the `budgets`/`budgets-host` target defaults and adds the `budgets` recipe that `check` depends on, idempotently. `base.llmlint.yml` and every other fragment still name no onebudgetspec rule.'
- '`scripts/check_repo_baseline.py` holds every repository to the budgets setup whatever its recorded composition, and reports an unwired repository whose `AGENTS.md` names no onebudgetspec reference with a fix naming `--wiring`. A test drives the real checker over such a repository and over a wired one.'
- No file in the skill describes budgets as opt-in or shows `--tool` as a supported invocation. That covers usage lines and examples, help text, docstrings and prose, including `SKILL.md`, `references/composing.md`, `references/tools/onebudgetspec.md`, the fragment's header, the composer's and checker's help and docstrings, `tests/AGENTS.md` and the test docstrings. The only places `--tool` may appear are tests that pass it to prove the composer refuses it as an unknown flag. The reference's "when is a requirement a budget" section and the rules `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes` are unchanged from how `cr-budget-rule` landed them.
- 'dero-skills itself is wired: onebudgetspec is pinned in its lockfile, `nx.json` carries the `budgets`/`budgets-host` target defaults, its `check` recipe runs the budgets recipe at its tier, and its root `llmlint.yml` adopts the fragment. The baseline checker passes over dero-skills, and a judged llmlint run of the fragment''s rules over dero-skills'' test code and every `budgets.yaml` it holds finds no violation, with no suppression added for them.'
- The composer and checker tests, the external wiring journey, and the create-repo skill eval pass on the finished tree. The skill eval's produced repository passes the baseline checker, so it is wired for budgets.
- On the finished tree, the deterministic llmlint `validate` passes and the diff-scoped judged lint over the branch is green.
- Every claim the completion report makes about the finished work and its verification, including which checks ran and with what result, is true of the tree as it finally stands.
additional_info: |
  The user's and manager's rulings this node applies are fixed and are not reopened:
  - budgets are baseline, not opt-in;
  - the `--tool` flag is removed outright rather than left with no choices;
  - `--wiring` always applies the budgets setup;
  - file paths stay;
  - dero-skills is wired itself;
  - unwired existing repositories get a checker finding.

  This node builds on `cr-budget-rule`, which added the rules `tests_hold_no_nonfunctional_thresholds` and `budgets_track_product_owner_outcomes` and the "when is a requirement a budget" section to the same reference and fragment. Keep that section and those rules as they landed. Any budget dero-skills registers sits at the level the product owner tracks, and its command analyses telemetry its tests already record, as that section states. Edit only the opt-in framing around them. The fragment URL `https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1` and its rule names are a contract other repositories pin. A shared interface is never changed unilaterally. If you find a reason to depart from anything this task states, ask the manager through `$ORCHESTRATOR_ASK_MANAGER` and keep building against the agreed surface until it rules.

  If the checker's existing check that `llmlint.yml` adopts the fragment cannot recognise dero-skills' in-tree plugin path, make it recognise either spelling rather than giving dero-skills a URL it does not use elsewhere.

  Removing a command-line flag from a skill script can read as a breaking change under dero-skills' semantic-release. The plan titles this `feat`, because the skill's consumers are agents reading the current `SKILL.md`. If dero-skills' own `AGENTS.md` or release rules say otherwise, ask rather than retitle.

  Read dero-skills' root `AGENTS.md` and `skills/bootstrap/create-repo/tests/AGENTS.md` first, and hold the change to them:
  - skill scripts stay runtime-independent;
  - tests are real, never mocked;
  - the external journey installs the real pinned release from npm;
  - the skill eval sits in `skilltest`;
  - commits are Conventional Commits.

  Checks that exercise this change, each run when it bears on what you just changed:
  - `just test` or the affected `test` targets for the composer and checker tests;
  - `just baseline` for the checker over dero-skills itself;
  - `just external` for the wiring journey, which needs the npm registry;
  - `just lint-llm-validate`;
  - `just lint-llm-diff` over the branch;
  - `just skilltest` for the create-repo skill eval, which drives the whole skill through a real harness and runs the baseline checker on the repository it produces. It is the slowest of these, so run it once the rest is green.
budgets: []
spikes: []
what: |
  In dero-skills' create-repo skill (`skills/bootstrap/create-repo/`), make onebudgetspec budgets a baseline requirement of every repository the skill composes, not an opt-in.

  **The composer (`scripts/compose_repo_plan.py`).**
  - Remove the `--tool` flag outright. onebudgetspec was its only choice.
  - `references/tools/onebudgetspec.md` joins every composed plan, beside the always-applied references.
  - The fragment `assets/llmlint/tools/onebudgetspec.llmlint.yml`, pinned `@1` at its raw `main` URL, joins every composed ongoing `llmlint.yml`. That is the generated repository adopting the plugin. Neither `base.llmlint.yml` nor any other fragment names it.
  - `--wiring REPO_DIR` always applies the budgets setup step, with no other flag: the exact pin, the `budgets`/`budgets-host` target defaults, and the `budgets` recipe that `check` depends on.
  - The plan's recorded invocation and its "References composed" line follow.
  - `--list` and `--help` stop naming `--tool`.

  **The baseline checker (`scripts/check_repo_baseline.py`).** Hold every repository to the budgets setup, whatever its recorded composition says:
  - onebudgetspec pinned in a lockfile;
  - every `budgets.yaml` reached by `check`;
  - `llmlint.yml` adopting the fragment.
  A repository that is not wired gets a finding whose fix names `compose_repo_plan.py --wiring <repo>`. Drop the "declares the tool" gate. The user accepted that every existing create-repo-built repository that is not wired gets that finding the next time the checker runs on it.

  **Prose.** Every place that describes budgets as opt-in, or names `--tool onebudgetspec`, says instead that budgets are part of the baseline. That includes:
  - `references/tools/onebudgetspec.md` (its opening and its setup-step and verification sections);
  - the fragment's header comment;
  - `SKILL.md` (the composer usage line, the "Tools (opt-in)" bullet, the composer description);
  - `references/composing.md` (step 3 and the axis table);
  - the checker's and composer's docstrings and help;
  - `tests/AGENTS.md`;
  - the test module docstrings that say "opt-in".
  File paths stay where they are, because the fragment URL is a contract adopters pin.

  **dero-skills itself.** The checker audits this repository in its own gate (`repo-baseline:test`), so wire dero-skills the same way:
  - onebudgetspec pinned;
  - the `budgets`/`budgets-host` target defaults in `nx.json`;
  - a `budgets` recipe that `just check` runs at its tier;
  - the fragment adopted in its root `llmlint.yml`, as an in-tree plugin path like the other fragments it dogfoods.
  Then fix whatever the fragment's rules find in dero-skills' own tests, the code rather than the rules. Keep the new recipe out of the expensive tiers' fan-out, which dero-skills' `AGENTS.md` "Project graph" rule protects.

  **Tests.** Update every test that drives `--tool` or asserts the opt-in:
  - `tests/test_compose_repo_plan.py`;
  - `tests/e2e/test_compose_repo_plan_e2e.py`;
  - `tests/test_check_repo_baseline_onebudgetspec.py`;
  - `tests/test_onebudgetspec_fragment.py`;
  - `tests/external/test_onebudgetspec_wiring_e2e.py`.
  After the update they prove the baseline:
  - a plan composed with no flag but a shape and a language includes the reference and the fragment;
  - `--wiring` with no other flag wires a repository;
  - `--tool` is refused as an unknown flag;
  - the checker reports an unwired repository whose `AGENTS.md` names no onebudgetspec reference.
why: |
  The user ruled that budgets are a baseline requirement of every repository create-repo builds, not an opt-in. Every non-functional requirement with a number should be auditable, trackable and adjustable in one place, a `budgets.yaml`, from a repository's first commit, so no new repository starts with constants buried in its tests. Because the user does not want existing llmlint consumers to get the budget rules without choosing them, a generated repository adopts the fragment by listing its URL in its own `llmlint.yml`. The always-on base fragment does not grow.
-->
