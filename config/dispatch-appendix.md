## Additional info

### Operational notes for this host

**Iterate on the judged tier alone. Never loop on the complete gate.** This is the single
most expensive mistake made in this workstream: one node ran the complete gate three times
to learn its lint findings — ~28 minutes a round, each round ending on the judged tier
after the deterministic tier had already passed — and then cleared its last two findings in
four minutes once it split them. Your loop is

    just lint-llm-diff "$BASE"

alone, about two minutes a roll. The complete gate is `complete_gate` below, and you run it
over the **finished** tree to confirm rather than as the loop that finds your findings.
What that "once" does and does not license is the paragraph after next.

**Define the complete gate once, before you start, and never spell it out again.** Both of
its parts vary by repository — the base branch is not always `main`, and `just gate` already
runs the judged tier in some repositories while in others that tier is a separate step — so
both live in one definition and everything below calls it:

    BASE=origin/master   # confirm it: git remote show origin | sed -n 's/.*HEAD branch: /origin\//p'
    complete_gate() { just bootstrap && just gate && just lint-llm-diff "$BASE"; }

It has to run as **one invocation**. A worker that runs the parts separately is failed for
never having run the gate end to end, however green each part was on its own — which is why
nothing below ever names a part of the chain instead of the whole of it.

**"Once" is a rule against looping, not a per-dispatch budget.** The reason to run the
complete gate exactly once is that it is a ~28-minute way to learn something the judged
tier reports in two; it is not an allowance of one run per dispatch to be spent and then
cited. A worker today read it as the budget, edited a file after its gate had gone green,
and would have cited that green in its completion report — correctly, by the letter of the
instruction, which is why the instruction is now stated in full. A gate run that predates
your last edit verified a tree that no longer exists, and a report citing it is inaccurate
however green it was. So: iterate on the judged tier alone, then run the complete gate over
the tree you are actually reporting on. If it reports something, fix that and run it again
— that second run is the rule working, not a breach of it. What you may never do is reach
for it *before* the judged tier, to discover findings, which is the whole cost above.

**Never run two gates at once.** This repository's e2e configs bind fixed ports
(4301, 4310, 4314, 4316, 4321, ...). A second concurrent gate does not queue — it fails
with `http://127.0.0.1:43xx/... is already used`, and both runs lose. Two publications
raced this way on 2026-08-18 and each burned 13 minutes to reach that error.

**Wait on a gate with a sentinel file, never a `pgrep` pattern that matches your own
poll.** `pgrep -f "just gate"` matches the shell running the loop, so the loop never
exits. That has wedged four workers here. Background `complete_gate` itself — the whole
chain, so what you waited on is the same command the paragraphs above call complete:

    ( complete_gate > /tmp/gate.log 2>&1; echo $? > /tmp/gate.exit ) &
    until [ -f /tmp/gate.exit ]; do sleep 20; done

**One sentinel and one log per invocation, and look before you launch one.** That pair is a
shape rather than two literal paths. Several dispatches run on this host at once and they
share `/tmp`, so a second worker writing `/tmp/gate.log` writes into the first one's — and
`until [ -f /tmp/gate.exit ]` returns immediately on somebody else's exit file, reporting
their result as yours. Name both after the invocation (`/tmp/gate.$$.log`, or the node id),
and check that nothing is already in flight before starting one. Three workers collided on
shared names today and not one of the three failures read as what it was: two
deterministic-tier runs deadlocked against the same cargo target-directory lock, so both
logs sat unchanged and neither progressed — about fifteen minutes lost and three
interruptions to a live turn; two whole-e2e runs wrote to one log, and stopping the
duplicate left four `SIGTERM` entries in it that read exactly like real test failures; two
judged-tier runs raced and wasted a roll, which was harmless only by luck. Read a log that
has stopped growing as two commands blocking each other before you read it as one command
working.

**Root-owned files after a container run.** The pre-push visual guard captures in Docker
as root and can leave `.nx/` and `dist/` unwritable, failing the *next* gate with `NX
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

**The judged tier is nondeterministic.** It has already returned opposite verdicts on an
identical diff in this workstream. Clear the findings it names, then stop — do not re-run
hunting a clean sheet, and never suppress a rule to move a number. If a rule looks wrong
or misapplied, say so with evidence rather than editing it.

**`--no-verify` is not an acceptable response to a slow or inconvenient hook.** The hooks
are this repository's enforcement point — there is no CI — so bypassing one commits work
that nothing has checked, and the closeout gate does not catch it afterwards: that runs
over the finished tree, by which point a bypassed commit is simply history. Two workers
today reached for `git commit --no-verify`, both on a commit they had judged mechanical,
and one of the two was a *merge* commit — a conflict resolution, which is the single most
likely commit in a dispatch to be wrong. No gate caught either one; the monitor caught
both. If a hook is slow, wait for it. If it refuses, it is telling you something about the
commit in front of you, and the answer is to fix the commit.

**Publication goes through the harness.** No `git push`, no `gh pr create`, no `gh pr
merge`. Finish the branch, commit everything, leave the tree clean, and report — the
lifecycle publishes it after you settle. Publication is explicitly **not** yours to
perform and **not** part of your acceptance criteria.

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
- the dispatch closes with a **final completion report** naming what was verified and the
  evidence for it.

Criteria state properties of the finished tree; the commands that produce them belong in
this section. `just check-plan <plan.json>` refuses a task whose criteria omit a demand its
resolved review bar — or this appendix — makes of it.
