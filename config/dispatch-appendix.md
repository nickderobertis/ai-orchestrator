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

**The judged lint tier is nondeterministic**, wherever a repository runs one. It has
already returned opposite verdicts on an identical diff in this workstream. Clear the
findings it names, then stop — do not re-run hunting a clean sheet, and never suppress a
rule to move a number. If a rule looks wrong or misapplied, say so with evidence rather
than editing it.

**`--no-verify` is not an acceptable response to a slow or inconvenient hook.** The hooks
are this repository's enforcement point — there is no CI — so bypassing one commits work
that nothing has checked, and nothing you run afterwards catches it: a check runs over
the finished tree, by which point a bypassed commit is simply history. Two workers
today reached for `git commit --no-verify`, both on a commit they had judged mechanical,
and one of the two was a *merge* commit — a conflict resolution, which is the single most
likely commit in a dispatch to be wrong. No check caught either one; the monitor caught
both. If a hook is slow, wait for it. If it refuses, it is telling you something about the
commit in front of you, and the answer is to fix the commit.

**Publication goes through the harness.** No `git push`, no `gh pr create`, no `gh pr
merge`. Finish the branch, commit everything, leave the tree clean, and report — the
lifecycle publishes it after you settle. Publication is explicitly **not** yours to
perform and **not** part of your acceptance criteria.

**Your completion report is the last thing this dispatch produces, and it describes the
tree as it finally is.** Every commit you made, every check you ran, over the tree that
exists when you stop. A report that predates your last commit describes a tree that no
longer exists, and is read as work left half-applied — which is how six of fourteen nodes
in one workstream settled `task-failed` while their work was complete and green, every
verdict naming the order of the report rather than a criterion it missed. So there is
nothing to do differently while you work and one thing that has to be true at the end:
whatever you find after you have written the report — a check you re-ran, a file you
touched, a finding you cleared — is fixed first and then reported afresh, so that the
report the judge reads is about the tree the judge can see.

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
this section. `just check-plan <source:project>` refuses a task whose criteria omit a demand its
resolved review bar — or this appendix — makes of it.
