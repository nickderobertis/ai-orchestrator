# A dispatch's run root, and what may delete it

On 2026-08-22 three dispatches of one run were destroyed within 90 seconds of launch,
and every one of them was reported as a missing `claude` binary. This is what
happened, what this repository did about it, and the upstream fix that has since
landed and been adopted here.

**Read the history below as history.** `onevcs` 0.14.1 fixed this at the source —
[`fix: prove a run root is abandoned from its session record, not from a lease nothing
holds`](https://github.com/nickderobertis/onevcs/pull/82) — and `config/onevcs.version`
adopted it at 0.15.4 and reads 0.16.2 today. Re-measured on this host on 2026-08-25 with both binaries and
everything else held: a session record naming a live owner keeps its run root across a
sibling `session open` on 0.14.1 and 0.15.4, and loses it on 0.14.0. **Concurrent
lifecycle dispatch on one identity is no longer unsafe for this reason**, and the
"at most one" constraint this document used to impose is lifted.

**onevcs 0.15.6 widened the rule this document specified, and the widening is worth
reading before the specification below.** What that release protects is every run root
a record still `open` names, whatever became of the process that opened it — the
`liveness()` half of the test below is gone. The reason is the case the specification
did not separate: a session opened from the command line is owned by the `onevcs` that
printed its token and then exited, so its record answers stale from that instant while
an operator works in the worktree for hours, and reading stale as *nobody is in here*
is the same deletion this document is about. What ends a run root's protection now is
`session close` writing `Lifecycle::Closed`, after which it falls through to the lease
and the retention bound exactly as before. Measured against the adopted 0.16.2 on
2026-08-29 and asserted by two legs of
`tests/e2e/test_run_root_lease_e2e.py`: a killed owner leaves an open session's root
standing, and a closed session's root is gone at the very next sibling open.

Every section below is kept rather than deleted, because the reasoning is what a reader
needs when they meet the next race of this shape — and because the mitigation is still
wired and still driven. Which parts are historical is said where they are.

## What deleted it, through onevcs 0.14.0

`onevcs session open` reclaims run roots as its first act. Reading the release the
adopted `onepipeline` links — `onevcs` v0.11.0, whose working checkout on this host
is `/home/nick/.ai-orchestrator/repos/nickderobertis__onevcs`; the line numbers below
are the tag's, and the tip of `main` has since moved them:

- `crates/onevcs/src/workspace.rs:620`, inside `pub fn open`, calls `reclaim(&runs)`
  on the identity's whole `runs` directory before this session's own root is created.
- `crates/onevcs/src/workspace.rs:1228`, `fn reclaim`, decides each root's fate at
  `:1243`: `lock::try_exclusive(&occupancy_identity(&run_root))`. The comment above it
  calls an exclusive take *"the proof that nothing is working in here"*. A root that
  cannot be taken exclusively is skipped; one that can is either removed outright when
  its clone holds no unpublished commit, or kept only if it is among the newest
  `RETAINED_DEAD_RUNS` (3) that do.

The lease that take is contending for is a `flock(2)` on
`<state root>/locks/<sha256("run:<run root>")>.lock`, and **nothing holds it while a
dispatch works**. `open` takes it shared at `workspace.rs:628`, immediately after
creating the run root, and drops it at `:680` as it returns — after the clone, the
worktree and the session record are written. `adopt`, `close` and the publication
paths each take it the same way, for the duration of that one command. So a session's
root is unheld from the moment `open` returns until some other verb touches it, which
for a dispatch is the whole time the agent is working in there.

That makes the exposure larger than "a startup window". A root created moments
earlier is reclaimable because it has no lease *yet*; a root three hours into a
dispatch is reclaimable because it has no lease *any more*. Both are the same
condition, and it is the normal state of a live dispatch.

Measured on this host rather than inferred. While the dispatch that wrote this
document was working in it, `onevcs` reported its own session as live —

```
s-dac3c89cfc70	open	live	pid=1710617	onevcs/s-dac3c89cfc70	…/runs/s-dac3c89cfc70/worktree
```

— pid 1710617 being the `onepipeline start` driver, and an exclusive take on that run
root's lock file succeeded on the first attempt. `onevcs` already knew the session was
live; the reclaimer never asked.

The consequence is what the incident looked like. Six ai-orchestrator sessions opened
between 20:25:42Z and 20:26:31Z; the three oldest were reclaimed and the two newest
survived. A session branch that has not committed yet has no unpublished commit, so
those roots did not even reach the retention list — they were removed outright.

## It is not the sweep

An earlier reading blamed `scripts/session-setup.sh` running `just sweep` at dispatch
startup. That reading is wrong, and two independent pieces of evidence rule it out.

`onevcs sweep` says what it answers for, and it is not this: *"This answers for the
publication and recovery workspaces onevcs owns under `~/.onevcs/workspaces`, and for
nothing else on this host."* And `just sweep --dry-run` on this host names each
identity's run root explicitly as a family it holds back —

```
…/workspaces/github.com-nickderobertis-ai-orchestrator-c2fddf4e28b4 — the per-run
lifecycle clone root, which `onevcs session open` keeps as a bounded recovery history
so a dead run's branch stays reachable; this verb does not reach into it
```

— and reported `Reclaimed: none`. The sweep is the verb that was *observed running*
during the incident, because session setup runs it; the verb that deletes run roots is
`session open`, which every dispatch also runs and which nothing logs.

## What this repository did about it, and still does

**This is now a second line rather than the only one.** With the upstream fix adopted,
a live session's run root is spared whether or not this holds a lease; what follows is
kept because a mitigation that has stopped being load-bearing has not stopped working,
and because it still covers the one case the record rule cannot — a run root whose
session record cannot be read at all, which `reclaim` answers with "no live sessions"
and falls through to the lease for.

`scripts/hold-run-lease.sh`, run first by `scripts/session-setup.sh`, takes that same
shared occupancy lease on this dispatch's run root and keeps holding it. Nothing new
is invented: a shared lease is already what `reclaim` reads as *somebody is working in
here*, and every other `onevcs` verb takes the lease shared too, so a held lease
blocks reclamation and nothing else.

It holds while the session is live by `onevcs`'s own definition — the record's state
is `open` and the process that opened it is that same process, still running — and
lets go the moment that stops being true. The holder also dies with its lock, since
the OS releases `flock` on process death.

**It narrows the window; it does not close it.** The run root exists from the instant
`open` creates it, and this script cannot run until the dispatch's agent has started
inside the worktree and fired its `SessionStart` hook — a clone, a worktree cut and an
agent launch later. What was an exposure lasting the dispatch's entire life becomes
one lasting its first few seconds. That is a real reduction and not a fix.

Two further limits, stated rather than discovered later:

- It is wired to the Claude Code `SessionStart` hook, so a dispatch that falls through
  to `codex` runs no such hook and takes no lease.
- It protects the run root only. A session whose record is stale — its owner gone —
  is deliberately left reclaimable.

`tests/e2e/test_run_root_lease_e2e.py` drives all of it against the real `onevcs`:
the root surviving *without* the lease, which is the adopted fix and is what that
journey's first leg now asserts; the root surviving with it; the root surviving a
killed owner while its record is still `open`, which is the 0.15.6 widening; the root
pruned once the session is *closed*; the lease
released with its session — and released again when that session is handed to a
different owner — and a real `session-setup.sh` taking it inside a real session
worktree. That first leg asserted the deletion until 2026-08-25, and it was proven to
discriminate before it was believed: it fails against onevcs 0.14.0 with the reclaimed
root named, and passes against the adopted 0.16.2.

It reads the session record directly rather than asking `onevcs session holders`,
which answers the same question, because a freshly cut worktree has no `.venv` yet:
there is no `onevcs` on its PATH for most of the window this covers. That makes the
record's field names, the `open` state, the `/proc` field `Record::liveness` reads,
and the `run:<path>` lease identity four copies of another crate's contracts, so
`tests/test_engine_contracts.py` reconciles each of them against the `onevcs` release
`onepipeline` links. A renamed field would otherwise not fail this script — it would
decline, saying the record names no run root, and the dispatch would work on unheld
behind a line that reads like an ordinary "nothing to hold here".

## The bounded recovery history still prunes

This must not buy a live dispatch's safety with a disk that fills up, so the
retention was tested rather than reasoned about.
`test_a_closed_sessions_run_root_is_reclaimed_by_the_next_open` opens a
real session, holds its lease, kills the owning process, waits for the lease to be
released, **closes the session**, and then requires the next `onevcs session open` to
delete that root. It does.

**Closing it is the question because onevcs 0.15.6 changed which one decides.**
[`fix(workspace): keep a run root a live session is still working in`](https://github.com/nickderobertis/onevcs/pull/99)
protects a root named by an **open** session even once the CLI that opened it has
exited — the door 0.14.1 left open, through which a sibling open was still deleting an
active dispatch's worktree moments after launch. Bisected here on 2026-08-29 against
the released CLIs with everything else held: that root is kept from 0.15.6 onward and
was removed at 0.15.4 and 0.15.5, and every release through the pinned 0.16.2 behaves
as 0.15.6 does. So owner liveness no longer prunes and session close does, which both the old and
the pinned release answer identically. The cost is stated rather than discovered later:
a session that opens and is never closed keeps its run root indefinitely, and `just
sweep` — which removes only what it can prove no live process names — is what reclaims
those.

## The upstream fix, as landed

**Specified here, then landed upstream as specified.** What this section asked for is
what `onevcs` 0.14.1 implemented: `fn reclaim` reads `run_roots_of_live_sessions()`
once before its walk — every record whose `liveness()` is `Liveness::Live` — skips any
run root in it, and only then falls through to the occupancy lease it tested before.
Its own doc comment now states the rule as four conditions rather than three, and says
the lease is the layer *behind* the record rather than a replacement for it. A session
directory it cannot read answers with no live sessions and falls through, which is the
gap the mitigation above still covers.

The specification is kept below, unchanged, because it is the argument for why that
shape is right and it is what a reader compares the implementation against.

**File:** `crates/onevcs/src/workspace.rs`. **Function:** `fn reclaim` (v0.11.0
`:1228`), at the `lock::try_exclusive` decision on `:1243`.

**The protection it should use:** a session record. `reclaim` should skip any run root
named by a record whose `state` is `Lifecycle::Open` and whose `liveness()` is
`Liveness::Live`, and only then fall through to the lease test it makes today. (0.14.1
landed exactly this; 0.15.6 then dropped the `liveness()` half, for the reason the
opening section gives.) Both
halves already exist and need no new concept:

- `workspace::all()` (`:464`) returns every session record on the host, so the run
  roots to protect are a lookup rather than a scan.
- `Record::liveness()` (`:359`) already answers *is the process that opened this
  session that same process, still running*, guarding against pid reuse with the
  recorded creation identity.
- `fn held_by` in `crates/onevcs/src/vcs.rs:576` already composes exactly these two
  tests for the same purpose — it reports `Holding::OwnerRunning` when the record is
  live and falls back to `lock::is_occupied` when it is not. `reclaim` asking the
  question that function already asks would make the two agree.

The lease is a per-command occupancy signal and outlives no command, which the
`held_by` doc comment states in as many words; using it alone as the proof that a
directory is abandoned is what loses this race. The record is the durable one, and it
is written before `open` drops the lease — so there is no instant at which a run root
is unprotected under the record rule.

A second, cheaper guard is worth having beside it and is not a substitute: `reclaim`
removes a directory it did not create in this process and reports nothing. An event on
the session stream naming each reclaimed root would have made the incident legible in
minutes instead of hours.

## The diagnostic that sent the diagnosis the wrong way

Both destroyed dispatches reported, on all five identities:

```
failed to spawn `claude`: No such file or directory (os error 2). Suggestion: check
the binary exists and is executable (try `oneharness detect`)
```

Both binaries existed and `oneharness detect` found them. `ENOENT` from a spawn also
means the child's **working directory** does not exist — which is what had happened,
the run root having been deleted — and the message names only the binary while its
suggestion sends the reader to PATH and the harness install.

**This repository cannot correct it.** The text belongs to `oneharness-core`:
`crates/oneharness-core/src/io/runner.rs:490` in `fn run_job_supervised`, and again at
`:726` in `fn stream_job`. It is present in the installed `oneharness` 0.12.1 binary on
this host, and a dispatch reaches the same crate as a linked library rather than
through any script here, so nothing on this side of the boundary is in a position to
rewrite it. The fix belongs there: when the job named a `cwd` (set at `runner.rs:457`)
and the spawn failed with `NotFound`, say which of the two was missing — the check is
one `Path::is_dir` on a path the runner already holds.
