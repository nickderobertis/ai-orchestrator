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

**Define the complete gate once, before you start, and never spell it out again.** Every
part of it varies by repository — the base branch is not always `main`, the recipe that is
the full bar is not always called `gate`, and that recipe already runs the judged tier in
some repositories while in others that tier is a separate step — so all of it lives in one
definition and everything below calls it. **Derive both values before you run anything;
neither is a fixed string, and the second one is the one workers get wrong:**

    BASE=origin/master   # confirm it: git remote show origin | sed -n 's/.*HEAD branch: /origin\//p'
    GATE=gate            # confirm it: just --list — take the recipe whose own description is
                         # this repository's full bar, whatever it is called
    complete_gate() { just bootstrap && just "$GATE" && just lint-llm-diff "$BASE"; }

A repository with no `gate` recipe is ordinary rather than broken, and substituting is what
you are meant to do: crozier has none, and its `check` is described by its own `just --list`
as *"Full quality gate. Fails on any issue."* — so `GATE=check` there, and a dispatch that
insisted on `just gate` would have run nothing at all.

**Nothing outside the repository answers which recipe that is.** `config/onevcs.rules.yml`
carried a per-identity `gate:` command until **onevcs 0.11.0 removed the concept** — a rule
is `{publication, approvals}` now and a `gate:` key is refused by name — so it is no longer
an answer. `just repos`'s gate column is not one either: it is the registry's own detection
from the origin and the checkout, and it prints `just gate` for repositories that have no
such recipe. The repository's own `just --list` is what decides.

It has to run as **one invocation**, over the tree you are reporting on. That is the whole
of what is being checked — that the chain ran end to end in one command, not that your
report echoes this file's spelling of it. A report that **names the command you
substituted** (`just bootstrap && just check && just lint-llm-diff origin/master`) has
satisfied the rule, and is the better report, because it says what actually ran. What fails
is running the parts separately, however green each was on its own — which is why nothing
below ever names a part of the chain instead of the whole of it.

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

**Wait with a sentinel file.** Background `complete_gate` itself — the whole chain, so what
you waited on is the same command the paragraphs above call complete — and poll for a file
only that command can write:

    ( complete_gate > /tmp/gate.log 2>&1; echo $? > /tmp/gate.exit ) &
    until [ -f /tmp/gate.exit ]; do sleep 20; done

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
share `/tmp`, so a second worker writing `/tmp/gate.log` writes into the first one's — and
`until [ -f /tmp/gate.exit ]` returns immediately on somebody else's exit file, reporting
their result as yours. Name both after the invocation (`/tmp/gate.$$.log`, or the node id),
and, before starting it, check whether its sentinel path is already in use by another
invocation; if so, choose a different invocation-specific pair. This path-ownership check
does not prohibit concurrent gates or judged tiers. Three workers collided on shared names
today and not one of the three failures read as what it was: two
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
