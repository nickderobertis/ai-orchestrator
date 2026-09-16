"""The operational appendix hands a dispatch its checks, and no longer hands it a gate.

`config/dispatch-appendix.md` is the text every dispatched task carries, and it was
gitignored scratch propagated by copy-paste until this suite tracked it. That is not
incidental to what went wrong in it. It named a three-part chain as the complete gate to
be run *once*, and then, four paragraphs later, demonstrated waiting on a gate with a
sentinel that backgrounded only two of those three parts; a worker that obeyed both ran
the parts separately and was failed for it, in its judge's own words: *"The required
complete gate (...) was never run once end-to-end; its components were run separately."*
It failed a second node the opposite way, by spelling the gate recipe as a bare `just
gate` — work that had run crozier's real bar, `just check`, crozier having no `gate`
recipe, was failed for naming it. Both are why the chain was afterwards named once and
its recipe derived rather than handed over.

**This host has since stopped asking a dispatch to run that bar at all**, which is what
these checks are now written to. A gate run spent twenty to forty minutes of a dispatch
learning what the merge path reports anyway, and six of fourteen nodes in one workstream
settled `task-failed` with complete, gate-green work. So what is asserted here is the
instruction that replaced it — run only the checks that exercise what you changed, take
the narrowest scope each supports, and rerun the one that reported — together with the
one thing the file may still say about a repository-wide bar: that it runs downstream,
on the merge path, after this dispatch has settled.

The machinery the old instruction needed is asserted **gone** rather than left to a
reviewer's memory, exactly as the retired concurrency rule below is: the `complete_gate`
definition, the recipe slot a reader had to derive for it, and the chained
one-invocation demand. A rule this file states constrains every dispatch on this host, so
one that has stopped being true is a cost paid hundreds of times over — and a reason left
behind is an instruction the next reader reconstructs.

What the ordering incident left behind is held positively instead — and the ordering
demand it first produced is now held **gone**. Six of those settled nodes were failed on
where the completion report sat rather than on a missed criterion, and the file answered
by demanding that the report be the dispatch's last output; six more were then failed on
*that*, with complete committed work, a green deterministic tier, and no acceptance
criterion found unmet, because a dispatch's conversation does not end when the worker
reports. So the file now states the property that ordering was serving — every claim
about the finished work is true of the tree as it finally stands, satisfied by a correct
delta as much as by restating the whole report — and the withdrawn ordering is asserted
absent, because a rule finished work cannot clear teaches everyone to route around the
thing that enforces quality.

The passages that were never about the gate are unchanged, and so are their reasons. The
rule on waiting was written as advice about waiting on a *gate*, and readers who had read
it walked into the same trap against other patterns: a worker wedged on
`scripts/fetch-corpus.sh`, another on `llmlint-judge.sh`, and the manager an hour after
issuing the rule on `publish-branch`. Seven instances are recorded in it: four workers
before one night, and two workers and the manager during it. Which is why what is asserted
below is the mechanism (`pgrep -f` matches whole command lines and excludes only its own
process) together with the self-match-proof check the old text answered nowhere.

The signalling rule is held on a different axis from the one it used to argue from. Every
paragraph of it argued from `-f`'s self-match, and `-x` does not self-match — so a reader
who followed that argument to `pkill -x` had followed it to a conclusion it supports. Two
workers reached that place in one day while another manager's dispatch was live, one by
`pkill -TERM -x just` and one by reading the table and piping pids into `kill`. What is
asserted now is the axis that actually decides it: a process is signalled only when it was
identified by its own PID, a PID obtained from a pattern is still a pattern kill, and the
self-match text stays as the explanation of a wait that never ends.

The instruction against running two checks concurrently is asserted gone for the same
reason: it rested on this repository's e2e configs binding fixed ports, live e2e code
allocates through `_free_port()` instead, and two managers' judged tiers ran concurrently
on 2026-08-24 and both completed.

Two rules moved *into* this file rather than out of it, because dispatch policy has one
source and it is this file: what a dispatch that changed nothing tracked owes, and when a
piece's checks are run. The removal side is `tests/test_shared_dispatch_bar.py`'s. A
third is new to both — a process must finish inside the turn that started it.

`tests/test_criteria_guard.py` proves the guard that reads this file; here the subject
is the file itself, which is why these belong to the tier keyed on this repository's
prose. `tests/e2e/test_dispatched_operational_notes_e2e.py` is the other half and reads
the same rules out of the prompt a really dispatched worker was handed, which is where
they either arrive or do not.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.criteria_guard import APPENDIX, AUTHORIZATIONS
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The definition this file used to carry, asserted absent. Kept as the shape it had,
#: because that is what a reintroduction would look like.
DEFINITION = re.compile(r"^ *complete_gate\(\) *\{(?P<chain>[^}]*)\}", re.MULTILINE)

#: The token every passage called instead of spelling the chain out, and the chained
#: invocation the chain was: two `just` recipes joined into one command for a worker to
#: run. Either one returning is the whole instruction returning with it.
GATE_FUNCTION = "complete_gate"
CHAINED_INVOCATION = re.compile(r"just\s+[^\s&|;]+\s*&&\s*just\b")

#: The demand that chain carried: that it ran end to end in one command. It is the
#: sentence a judge compared a report against, and it has no subject left.
ONE_INVOCATION = re.compile(r"one\s+invocation", re.IGNORECASE)

#: The derivation block a reader substituted into. The gate slot needed a recipe name
#: derived per repository, because the recipe that is a repository's full bar is not
#: always called `gate` — crozier's is `check`. With nothing to run, nothing derives one.
ASSIGNMENT = re.compile(r"^ +(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<rest>.*)$", re.MULTILINE)
DERIVATION = re.compile(r"confirm it:", re.I)

#: The rule that replaced all of it, and the scope rule beside it. Held on their
#: load-bearing words rather than on a sentence, so a reword is free and a dropped half
#: is not; `\s+` between words because this file is hard-wrapped.
TARGETED_CHECKS = re.compile(r"only\s+the\s+checks\s+that\s+exercise\s+what\s+you\s+changed", re.I)
NARROWEST_SCOPE = re.compile(r"narrowest\s+scope\s+each\s+one\s+supports", re.IGNORECASE)
RERUN_THAT_CHECK = re.compile(r"run\s+\*\*that\s+check\*\*\s+again", re.IGNORECASE)

#: A repository-wide bar, in the spellings a worker-facing instruction would use. The
#: file may still name one — it has to, to say who runs it — so this locates the mention
#: and :data:`DOWNSTREAM` decides whether it is named as somebody else's.
WIDE_BAR = re.compile(
    r"\b(?:complete|full|whole|entire)\s+(?:gate|bar|verification|check\s+suite)\b", re.I
)

#: What makes a mention of that bar a statement about who runs it rather than an
#: instruction to run it here. The merge path is the whole answer: a `pre-push` hook
#: where the repository publishes locally, the host's required checks where it does not.
DOWNSTREAM = ("downstream", "merge path", "pre-push", "required checks")

#: What a publication check that runs downstream is to a worker and its judge, in the
#: parts a judge acts on: no worker can run one, no dispatch is held to one having passed,
#: and a `checks-failed` retry is judged on its task's own local checks — the repair of
#: what the refusal named — never on the downstream check passing again. A retry that
#: fixed and locally verified the test its refusal named was failed without this, which
#: left finished work unpublished and its dependents skipped until a manager intervened.
PUBLICATION_CHECKS_ARE_NOT_WORKER_RUNNABLE = re.compile(
    r"downstream\s+publication\s+checks\s+are\s+not\s+runnable\s+by\s+a\s+worker", re.I
)
NEVER_HELD_TO_ONE_HAVING_PASSED = re.compile(
    r"never\s+held\s+to\s+one\s+having\s+passed", re.IGNORECASE
)
RETRY_JUDGED_ON_LOCAL_CHECKS = re.compile(
    r"`checks-failed`\s+retry[^.]*?is\s+judged\s+on\s+its\s+task's\s+stated\s+local"
    r"\s+acceptance\s+checks",
    re.IGNORECASE,
)
REPAIR_OF_WHAT_THE_REFUSAL_NAMED = re.compile(
    r"repair\s+of\s+what\s+the\s+refusal\s+named", re.IGNORECASE
)
NEVER_BY_THE_DOWNSTREAM_CHECK_PASSING = re.compile(
    r"never\s+by\s+the\s+downstream\s+check\s+passing\s+again", re.IGNORECASE
)
#: Every part above, for the file's test and for the journey reading the judge's prompt.
DOWNSTREAM_CHECKS_ARE_NOT_JUDGED = (
    (
        PUBLICATION_CHECKS_ARE_NOT_WORKER_RUNNABLE,
        "that downstream publication checks are not runnable by a worker",
    ),
    (NEVER_HELD_TO_ONE_HAVING_PASSED, "that no dispatch is held to one having passed"),
    (
        RETRY_JUDGED_ON_LOCAL_CHECKS,
        "that a `checks-failed` retry is judged on its task's stated local acceptance checks",
    ),
    (REPAIR_OF_WHAT_THE_REFUSAL_NAMED, "that what those checks prove is the repair"),
    (
        NEVER_BY_THE_DOWNSTREAM_CHECK_PASSING,
        "that the retry is never judged by the downstream check passing again",
    ),
)
#: Where `checks-failed` is defined, so the retry this file names is one the engine settles.
OUTCOME_TABLE = "docs/repo-lifecycle.md"
CHECKS_FAILED_OUTCOME = re.compile(r"^\| `failed` \| `checks-failed` \|", re.MULTILINE)

#: The property the withdrawn ordering was serving, in the three parts a worker acts on:
#: every claim about the finished work is true of the final tree, a delta stated in the
#: turn that changed something satisfies that, and a claim about something that was never
#: run or made is false and fails on its own account.
CLAIMS_TRUE_OF_THE_FINAL_TREE = re.compile(
    r"claim\s+you\s+make\s+about\s+the\s+finished\s+work\s+is\s+true\s+of\s+the\s+tree"
    r"\s+as\s+it\s+finally\s+stands",
    re.IGNORECASE,
)
A_DELTA_SATISFIES_IT = re.compile(r"correct\s+delta\s+satisfies\s+this", re.IGNORECASE)
#: Silence after a later change is the third of those parts, and the one a worker is most
#: likely to read the delta allowance as permitting.
SILENCE_DOES_NOT_SATISFY_IT = re.compile(r"silence\s+does\s+not\s+satisfy\s+it", re.IGNORECASE)
A_FALSE_CLAIM_STILL_FAILS = re.compile(
    r"was\s+not\s+run\s+or\s+was\s+not\s+made\s*\n?\s*is\s+false", re.IGNORECASE
)
#: What keeps a once-true claim from becoming a false one: a change that could have
#: invalidated it is re-run against, or the claims not re-checked are named.
RERUN_WHAT_A_CHANGE_COULD_HAVE_BROKEN = re.compile(
    r"re-run\s+what\s+the\s+change\s+could\s+have\s+broken", re.IGNORECASE
)
UNRECHECKED_CLAIMS_ARE_NAMED = re.compile(
    r"say\s+which\s+claims\s+you\s+have\s+not\s+re-checked", re.IGNORECASE
)

#: Evidence a worker produced on purpose about a state other than the finished one. The
#: resulting-tree property reads like a ban on it, and one node was failed for citing the
#: failure its own criteria required it to observe. Two spellings, because they are two
#: separately actionable allowances: a run quoted from before the change, and a failure
#: made to happen. Each is held to the verdict that follows it, so prose that names the
#: case without allowing it no longer passes.
A_RUN_FROM_BEFORE_THE_CHANGE_IS_EVIDENCE = re.compile(
    r"citation\s+of\s+a\s+run\s+taken\s+before\s+a\s+change[^.]*?is\s+correct\s+evidence",
    re.IGNORECASE,
)
AN_INDUCED_FAILURE_IS_EVIDENCE = re.compile(
    r"failure\s+induced\s+on\s+purpose\s+as\s+evidence[^.]*?is\s+correct\s+evidence",
    re.IGNORECASE,
)
THE_CITATION_SAYS_WHICH_IT_IS = re.compile(
    r"provided\s+the\s+citation\s+says\s+which\s+it\s+is", re.IGNORECASE
)

#: The limit of *could have invalidated it*, which judges read as absolute: a commit that
#: cannot affect a check leaves that check's evidence standing. Held in the parts a worker
#: and a judge each act on — the allowance, the report naming the commit and why it is
#: inert, the bound being what the check reads, and that bound cutting both ways: a
#: comment is inert for a check that does not read it and not for one that does.
INERT_COMMIT_LEAVES_EVIDENCE_STANDING = re.compile(
    r"commit\s+that\s+cannot\s+affect\s+a\s+check\s+leaves\s+that\s+check's\s+evidence"
    r"\s+standing",
    re.IGNORECASE,
)
THE_REPORT_NAMES_THE_INERT_COMMIT = re.compile(
    r"provided\s+the\s+report\s+names\s+that\s+commit\s+and\s+says\s+why\s+it\s+is\s+inert",
    re.IGNORECASE,
)
BOUNDED_BY_WHAT_THE_CHECK_READS = re.compile(
    r"what\s+decides\s+it\s+is\s+what\s+the\s+check\s+reads", re.IGNORECASE
)
UNREAD_COMMENTS_ARE_INERT = re.compile(
    r"comment-only\s+or\s+documentation-only\s+commit\s+to\s+content\s+a\s+check\s+does"
    r"\s+not\s+read\s+is\s+inert",
    re.IGNORECASE,
)
READ_COMMENTS_ARE_NOT_INERT = re.compile(
    r"not\s+inert\s+for\s+a\s+check\s+that\s+reads\s+comments", re.IGNORECASE
)

#: The three incidents the claims section records, in the order it records them: a node
#: correctly failed for a claim about a run that never happened, a node failed for citing
#: the induced failure its own criteria required, and two dispatches refused over a
#: comment-only final commit.
RECORDED_INCIDENTS = (
    re.compile(r"one\s+node\s+of\s+this\s+host's\s+history\s+was\s+correctly\s+failed\s+for\s+it"),
    re.compile(r"One\s+node\s+of\s+this\s+host\s+was\s+failed\s+for\s+exactly\s+that"),
    re.compile(r"refused\s+over\s+nothing\s+but\s+a\s+comment-only\s+final\s+commit"),
)

#: The withdrawn demand, in the two forms a worker would act on. Held as an absence
#: because the file said both of these and six nodes with complete, green work were
#: failed against them; prose that no longer argues for the rule but still states it is
#: the rule still being enforced.
REPORT_IS_LAST = re.compile(
    r"completion\s+report\s+is\s+the\s+last\s+thing\s+this\s+dispatch\s+produces", re.I
)
REPORTED_AFRESH = re.compile(r"fixed\s+first\s+and\s+then\s+reported\s+afresh", re.IGNORECASE)

#: The publication rule, and the two carve-outs this host lets a task grant beside it.
#: Each half is held on its load-bearing words: the session branch reaching its remote
#: through `onevcs publish` alone, landing staying the lifecycle's, each carve-out
#: opening on the task's own grant, the draft never marked ready by the worker, the
#: demonstration change request based on the session branch and closed before the
#: worker finishes, and everything else still forbidden. `\s+` between words because
#: this file is hard-wrapped.
PUBLICATION_RULE = re.compile(r"\*\*Publication goes through the harness\.\*\*")
ONLY_THROUGH_ONEVCS = re.compile(r'only\s+through\s+`onevcs publish "\$ONEVCS_SESSION"`')
LANDING_IS_NOT_THE_WORKERS = re.compile(r"Landing it is explicitly \*\*not\*\* yours")
CARVE_OUT = re.compile(r"\*\*Only when the task's `## Additional info`\s+(?P<grant>[^*]+)\*\*")
OPENS_THE_DRAFT = re.compile(r'`onevcs publish "\$ONEVCS_SESSION" --draft')
DESCRIBES_THE_DRAFT = re.compile(r'`onevcs change describe "\$ONEVCS_SESSION" --body-file PATH`')
READS_THE_DRAFT_BACK = re.compile(r'`onevcs change show "\$ONEVCS_SESSION"`')
NEVER_ONE_SECOND_CHANGE_REQUEST = re.compile(r"never\s+opens\s+a\s+second")
NEVER_MARKED_READY_BY_THE_WORKER = re.compile(r"Never\s+mark\s+the\s+draft\s+ready")
BASED_ON_THE_SESSION_BRANCH = re.compile(r"--base <session branch>")
NEVER_ON_THE_REPOSITORYS_BASE = re.compile(r"never\s+the\s+repository's\s+base")
CLOSED_WITH_ITS_BRANCH = re.compile(r"`gh pr close <n> --delete-branch`")
CLOSED_BEFORE_FINISHING = re.compile(r"closed\s+before\s+you\s+finish")
EVERYTHING_ELSE_STAYS_FORBIDDEN = re.compile(
    r"Everything\s+else\s+a\s+push\s+or\s+a\s+change-request\s+verb\s+could\s+do\s+is\s+still\s+not\s+yours"
)

#: The heading of the section that asks for each demand to be stated as a criterion. The
#: bullets under it are what a plan's builder copies, so a demand is asked for there or
#: nowhere — the rest of the file recounts, argues and instructs, in the same words.
STATE_THE_BAR = "### State the bar in `## Acceptance criteria`, not only here"

#: A command put into the background, which is what the sentinel example does, and the
#: sentinel that example polls. The pair is what makes the wait an answer about the
#: invocation that wrote it rather than about whichever dispatch wrote `/tmp` first.
BACKGROUNDED = re.compile(r"\(\s*(?P<command>[^()]*?)\s*\)\s*&")
WAITED_ON = re.compile(r"until \[ -f (?P<sentinel>\S+) \]")
#: What makes a path this invocation's own rather than every dispatch's.
PER_INVOCATION_PATH = re.compile(r"\$\$|\$\{?[A-Za-z_]")

#: Where the polling rule is stated at all: the passage that first reaches for a
#: pattern match against a process. Matched on `pgrep -f` rather than on a heading,
#: because the heading is the part that was wrong — it named gates, and the defect is
#: in the mechanism.
POLLING = re.compile(r"pgrep\s+-f")

#: The mechanism, as the two facts that make every such wait self-matching: the
#: pattern is matched against whole command lines, and the only process excluded from
#: the match is `pgrep`'s own — never the shell that invoked it. Held with `\s+`
#: between words because this file is hard-wrapped.
MATCHES_COMMAND_LINES = re.compile(r"full\s+command\s+lines?", re.IGNORECASE)
EXCLUDES_ONLY_ITSELF = re.compile(r"excludes[^.]*?\bitself\b", re.IGNORECASE | re.DOTALL)

#: The check the old text answered nowhere: how to ask whether something is running,
#: in a form that cannot match the shell asking. `pgrep -x` matches the executable
#: name, and the `ps` fallback drops the one self-match its own pipeline has.
BY_EXECUTABLE = re.compile(r"pgrep\s+-x\b")
PS_FALLBACK = re.compile(r"ps\s+-eo[^\n]*\|\s*grep\s+-v\s+grep")

#: The pattern kill, refused in every spelling. Held without naming a flag: `-f` alone was
#: what the old text refused, and `pkill -x` — which does not self-match, and which takes
#: every process of that name on the host — is the form a worker actually reached for.
NEVER_PATTERN_KILL = re.compile(r"pkill[^.]*?\bnever\b|\bnever\b[^.]*?pkill", re.I)

#: The rule the whole passage now opens with, on the axis that decides it: whose process
#: this is, rather than how the pattern matched. Two phrases, because each is a separate
#: way to be wrong — signalling something you only matched, and believing that resolving
#: the match to a pid first makes it yours to signal.
SIGNAL_ONLY_BY_PID = re.compile(r"[Nn]ever signal a process you did not identify by PID")
PID_FROM_A_PATTERN = re.compile(r"PID you got from a pattern is\s+still a pattern kill")

#: Which tool each spelling is safe in. The distinction has to be drawn on the tool rather
#: than on the flag, because `-x` is the safer spelling for the tool that reads and the
#: more dangerous one for the tool that signals.
READS_VERSUS_SIGNALS = re.compile(
    r"`pgrep` and `ps` \*\*read\*\*.*?`pkill` \*\*signals\*\*", re.DOTALL
)
UNSCOPED_X_KILL = re.compile(r"pkill\s+-x\s+just`\s+takes\s+every\s+`just`\s+on\s+the\s+machine")

#: The positive form, which is the only thing that makes the question go away: a process
#: you launched yourself has told you its pid.
CAPTURED_AT_LAUNCH = re.compile(r"cmd & MYPID=\$!")

#: The rule that is gone, in every form a reader would act on: the instruction itself and
#: the fixed-port conflict it rested on. `4321` stands for the port list; the whole list is
#: not enumerated here, because one surviving number is enough to reintroduce the reason.
CONCURRENT_GATE_INSTRUCTION = re.compile(r"two gates at once|two concurrent gates", re.I)
FIXED_PORT_REASON = re.compile(r"bind fixed ports|43\d\d/|is already used", re.I)

#: The sentinel wait's naming rule, which the rewrite had to leave standing: one
#: sentinel and one log per invocation, on a host whose dispatches share `/tmp`.
PER_INVOCATION = re.compile(r"one\s+log\s+per\s+invocation", re.IGNORECASE)

#: The pre-launch check is about whether the invocation's sentinel path is already
#: owned, never whether another check or judged tier is running. The former prevents
#: one invocation from reading another's result; the latter would reinstate the
#: retired concurrency rule through implication rather than by name. The permission is
#: matched on `checks` as well as `gates` because the thing a worker now runs
#: concurrently is a check: the noun moved with the instruction, the rule did not.
SENTINEL_PATH_OWNERSHIP = re.compile(
    r"sentinel\s+path\s+is\s+already\s+in\s+use\s+by\s+another\s+invocation",
    re.IGNORECASE,
)
CONCURRENT_RUNS_ALLOWED = re.compile(
    r"does\s+not\s+(?:forbid|prohibit)[^.]*?concurrent[^.]*?(?:gates?|checks?|judged\s+tiers?)",
    re.IGNORECASE,
)
AMBIGUOUS_IN_FLIGHT = re.compile(
    r"nothing\s+is\s+already\s+in\s+flight\s+before\s+starting\s+one",
    re.IGNORECASE,
)

#: The per-dispatch scratch directory, and the alternative it replaces. Both halves,
#: because a variable named without saying what it is for reads as trivia a worker
#: skips: the whole instruction is *this instead of a `/tmp` path you invented*.
SCRATCH_DIRECTORY = re.compile(r"ONEPIPELINE_NODE_SCRATCH_DIR")
INSTEAD_OF_INVENTING_ONE = re.compile(
    r"rather\s+than\s+under\s+a\s+`/tmp`\s+path\s+you\s+invented", re.IGNORECASE
)
#: Its contract, in the four properties a worker relies on to use it without checking:
#: it is a path it can use from anywhere, it is there when the dispatch starts, it
#: belongs to this dispatch alone, and nothing takes it away underneath. A variable named
#: with no contract is one a worker tests for and falls back from, which is the `/tmp`
#: path again with an extra branch.
#:
#: Exactly these four, and nothing beyond them: what the engine promises is what this
#: file may state, and a fifth property added here would be a promise a worker relies on
#: and nothing keeps.
SCRATCH_CONTRACT = (
    re.compile(r"an\s+absolute\s+path", re.IGNORECASE),
    re.compile(r"exists\s+and\s+is\s+writable\s+when\s+the\s+dispatch\s+starts", re.I),
    re.compile(r"unique\s+to\s+this\s+dispatch", re.IGNORECASE),
    re.compile(r"not\s+removed\s+while\s+it\s+runs", re.IGNORECASE),
)

#: What a dispatch that changed nothing tracked owes, which is none of the checks above.
#: Held on what it permits, not only on the condition it names.
NO_TRACKED_CHANGE = re.compile(r"changed\s+nothing\s+tracked\s+owes", re.IGNORECASE)
COMPLETE_WITHOUT_THEM = re.compile(r"you\s+are\s+complete\s+without\s+them", re.IGNORECASE)
NOT_BECAUSE_IT_IS_SLOW = re.compile(
    r"never\s+because\s+running\s+them\s+is\s+slow\s+or\s+inconvenient", re.IGNORECASE
)
NOTHING_TO_COMMIT_IS_COMPLETE = re.compile(r"correct\s+and\s+complete\s+outcome", re.IGNORECASE)

#: A process outliving the turn that launched it. Three parts, each separately
#: actionable: the rule, why the launch gives no sign of the loss, and what to do instead.
FINISHES_INSIDE_ITS_TURN = re.compile(
    r"must\s+finish\s+inside\s+the\s+turn\s+that\s+started\s+it", re.IGNORECASE
)
THE_LAUNCH_LOOKS_FINE = re.compile(
    r"because\s+it\s+succeeded\s+and\s+the\s+turn\s+then\s+ended\s+normally", re.IGNORECASE
)
WAIT_INSIDE_THE_TURN = re.compile(
    r"wait\s+out\s+its\s+sentinel\s+before\s+your\s+turn\s+ends", re.IGNORECASE
)

#: Committing as the work goes, held on the consequence rather than on the instruction:
#: "commit often" is advice a worker weighs, and "what is uncommitted is what nothing
#: recovers" is a fact about this host that decides it.
COMMIT_EACH_PIECE = re.compile(
    r"[Cc]ommit\s+a\s+coherent\s+working\s+piece\s+the\s+moment\s+it\s+works"
)
UNCOMMITTED_IS_LOST = re.compile(r"dirty\s+worktree\s+does\s+not\s+survive", re.IGNORECASE)
#: When the checks a piece needs are run. The removed preamble demanded a *tier* before
#: every commit; what survives is the timing, attached to the checks this file selects.
CHECKED_BEFORE_COMMITTED = re.compile(
    r"run\s+before\s+it\s+is\s+committed\s+rather\s+than\s+after", re.IGNORECASE
)

#: Asking rather than stopping, and the seam it is asked over. The seam is named
#: because a worker told to "ask its manager" with no mechanism has been told to write
#: it in a report nobody reads until the dispatch is over.
ASK_RATHER_THAN_STOP = re.compile(r"\*\*Ask\s+rather\s+than\s+stop\.\*\*")
ASK_SEAM = re.compile(r"\$ORCHESTRATOR_ASK_MANAGER")
#: The ending it refuses, which is the one a careful worker reaches: it declines to
#: decide, says so, and stops — leaving nothing committed and nobody asked.
STOPPING_UNASKED = re.compile(r"work\s+undone\s+and\s+the\s+question\s+unasked", re.IGNORECASE)

#: The secondary rate limiter, in the two things that separate it from the primary one:
#: what tells you which it is, and that waiting is not the answer.
SECONDARY_LIMITER = re.compile(r"secondary\s+limiter", re.IGNORECASE)
DISAGREEING_BUDGET = re.compile(r"gh api rate_limit")
NOT_WAITED_OUT = re.compile(r"every\s+further\s+attempt\s+extends\s+it", re.IGNORECASE)


@pytest.fixture(scope="module")
def appendix() -> str:
    return (REPO_ROOT / APPENDIX).read_text(encoding="utf-8")


def sentence_around(prose: str, at: int) -> str:
    """The sentence ``at`` falls inside, flattened onto one line.

    Cut on sentence boundaries rather than on paragraphs, because a paragraph is large
    enough to hold both a mention of a repository-wide bar and, somewhere else entirely,
    the word that would excuse it.
    """
    opened = max(prose.rfind(". ", 0, at), prose.rfind("\n\n", 0, at)) + 1
    closed = prose.find(". ", at)
    return " ".join(prose[opened : len(prose) if closed < 0 else closed].split())


def test_the_appendix_defines_no_repository_wide_gate_for_a_worker_to_run(
    appendix: str,
) -> None:
    """The instruction this host removed, asserted gone in the machinery it needed.

    A `complete_gate` definition, the token every other passage called instead of
    spelling the chain out, and a chained `just … && just …` invocation are the three
    shapes it had here. Any one of them coming back brings the whole instruction with
    it, whatever the prose around it then says: a worker reads the runnable thing.

    Asserted rather than remembered because a rule this file states is paid for by every
    dispatch this host makes — a gate run cost twenty to forty minutes of a dispatch to
    learn what the merge path reports anyway, and six of fourteen nodes in one workstream
    settled `task-failed` with complete, gate-green work.
    """
    defined = DEFINITION.search(appendix)
    assert defined is None, (
        f"{APPENDIX} defines a complete gate again ({defined.group(0)!r}). This host "
        "stopped asking a dispatch to run its repository's whole bar; what a dispatch "
        "owes is the checks that exercise its own change"
    )
    assert GATE_FUNCTION not in appendix, (
        f"{APPENDIX} names `{GATE_FUNCTION}` again, which is the handle every passage "
        "here used to call the whole chain by; nothing left in this file has a chain to "
        "call"
    )
    chained = CHAINED_INVOCATION.search(appendix)
    assert chained is None, (
        f"{APPENDIX} hands a worker {chained.group(0)!r} — recipes chained into one "
        "command is what 'the complete gate' was, under whatever name"
    )
    demanded = ONE_INVOCATION.search(appendix)
    assert demanded is None, (
        f"{APPENDIX} still demands that something run as {demanded.group(0)!r}; that "
        "demand had exactly one subject, and a judge compared a worker's report against "
        "it"
    )


def test_the_appendix_derives_no_gate_recipe_for_a_worker_to_substitute(
    appendix: str,
) -> None:
    """The other half of the machinery: the recipe slot a reader filled in per repository.

    It existed because the recipe that is a repository's full bar is not always called
    `gate` — crozier's is `check` — and a bare name here was both a command that may not
    exist and a literal a judge compared a report against, which failed finished work.
    With nothing repository-wide left to run, a derivation for it is a question with no
    answer: a reader who derives one has been told there is something to run.
    """
    derived = [
        found["name"] for found in ASSIGNMENT.finditer(appendix) if DERIVATION.search(found["rest"])
    ]
    assert not derived, (
        f"{APPENDIX} shows a worker how to derive {derived} before it starts. Those "
        "slots existed to fill in a chained gate invocation; deriving one again is the "
        "instruction returning by way of its setup block"
    )


def test_the_appendix_names_a_repository_wide_bar_only_as_something_run_downstream(
    appendix: str,
) -> None:
    """The one thing this file may still say about that bar: who runs it, and when.

    It has to say it — a worker told to run less needs to know the rest is not simply
    skipped, or it reinstates the wider run on its own judgment. So each mention is read
    in its own sentence and required to name the merge path that owns it: a `pre-push`
    hook where the repository publishes locally, the host's required checks where it
    publishes remotely.
    """
    mentions = list(WIDE_BAR.finditer(appendix))
    assert mentions, (
        f"{APPENDIX} no longer says a repository-wide bar exists at all. A worker told "
        "only to run less concludes the rest is nobody's, and runs it anyway"
    )
    for mention in mentions:
        sentence = sentence_around(appendix, mention.start())
        assert any(marker in sentence.lower() for marker in DOWNSTREAM), (
            f"{APPENDIX} names {mention.group(0)!r} in a sentence that does not say it "
            f"runs downstream on the merge path ({sentence!r}), so it reads as this "
            "dispatch's to run"
        )


def test_a_retry_is_judged_on_its_local_checks_never_on_a_downstream_publication_check(
    appendix: str,
) -> None:
    """A check that runs after the dispatch settles is no criterion of that dispatch.

    The judge is the party this is for. A `checks-failed` retry carries the merge path's
    refusal, and a judge reading "the required check failed" beside a generic completion
    bar read re-running that check as unmet — so a retry that had fixed and locally
    verified the failing test was failed, leaving finished work unpublished and its
    dependents skipped until a manager published it by hand. The statement is held in one
    paragraph, because a judge weighs its halves together: that downstream checks are not
    a worker's to run, and what a retry is judged on instead.

    `checks-failed` is anchored to the outcome table `tests/test_engine_contracts.py`
    reconciles to the engine, so the retry named here stays one the engine really settles.
    """
    paragraphs = [
        block
        for block in appendix.split("\n\n")
        if PUBLICATION_CHECKS_ARE_NOT_WORKER_RUNNABLE.search(" ".join(block.split()))
    ]
    assert len(paragraphs) == 1, (
        f"{APPENDIX} states that downstream publication checks are not runnable by a worker "
        f"in {len(paragraphs)} paragraphs; it is stated once, beside what a retry is judged on"
    )
    paragraph = " ".join(paragraphs[0].split())
    for stated, missing in DOWNSTREAM_CHECKS_ARE_NOT_JUDGED:
        assert stated.search(paragraph), (
            f"{APPENDIX} no longer says {missing}. Without it a judge reads a refusal's "
            "failed required check as a criterion the retry left unmet, and fails a "
            f"repaired retry for a check it cannot run:\n{paragraph}"
        )
    table = (REPO_ROOT / OUTCOME_TABLE).read_text(encoding="utf-8")
    assert CHECKS_FAILED_OUTCOME.search(table), (
        f"{OUTCOME_TABLE}'s outcome table no longer defines `checks-failed`, so the retry "
        f"{APPENDIX} tells a judge how to assess is one the engine does not settle"
    )


def test_the_targeted_check_rule_is_the_first_thing_a_reader_meets(appendix: str) -> None:
    """Burying the cheap rule has a measured cost, so its position is part of what it says.

    The buried version of it — iterate on the judged tier, do not loop on the whole
    chain — cost one node about 84 minutes after it had already been written down. What
    leads now is the rule that replaced it, and it leads for the same reason.
    """
    first = re.search(r"\*\*(?P<rule>[^*]+)\*\*", appendix)
    assert first is not None, f"{APPENDIX} leads with no emphasized rule at all"

    rule = " ".join(first["rule"].split())
    assert TARGETED_CHECKS.search(rule), (
        f"{APPENDIX} now leads with {rule!r}; the rule about which checks a dispatch owes "
        "is what this file exists to teach, and burying its predecessor cost a node 84 "
        "minutes"
    )


def test_the_appendix_scopes_each_check_and_reruns_only_the_one_that_reported(
    appendix: str,
) -> None:
    """The two halves that keep 'run less' from collapsing back into 'run everything'.

    Without the scope rule, a worker satisfies the leading rule by running the widest
    tier that touches its change and calling it exercised. Without the rerun rule it
    confirms a fix by running that tier's neighbours again, which is the ~28-minute round
    the old instruction was looped on — the cost is identical whatever the loop is
    called.
    """
    assert NARROWEST_SCOPE.search(appendix), (
        f"{APPENDIX} no longer tells a worker to take the narrowest scope each check "
        "supports, so 'the checks that exercise what you changed' is satisfied by the "
        "widest tier that touches it"
    )
    assert RERUN_THAT_CHECK.search(appendix), (
        f"{APPENDIX} no longer says that a check which reported something is the check to "
        "run again; confirming a fix by rerunning its neighbours is the round this "
        "instruction replaced"
    )


def test_what_a_dispatch_claims_is_stated_as_the_property_it_must_have(appendix: str) -> None:
    """The residue of the removed gate, stated as a property rather than as an order.

    The order came first and failed six nodes of one run in a night — complete committed
    work, a green deterministic tier, no acceptance criterion found unmet, and two of them
    carrying an escalated warning about the pattern in their own task. It is unsatisfiable
    rather than unread: a dispatch's conversation does not end when the worker reports,
    the supervisor keeps asking, and answering well means running things, so every good
    answer invalidated the report and only restating it whole complied. What the file
    states instead is the property that ordering was serving — every claim about the
    finished work true of the tree as it finally stands, a delta in the turn that changed
    something satisfying it as fully as a restated report, and a claim about a check that
    never ran still false. Evidence produced on purpose about an earlier or induced state
    is held here too: the property reads like a ban on it, and a node was failed for
    citing the failure its own criteria required it to observe.
    """
    for stated, missing in (
        (
            CLAIMS_TRUE_OF_THE_FINAL_TREE,
            "that every claim a dispatch makes about the finished work is true of the "
            "tree as it finally stands",
        ),
        (A_DELTA_SATISFIES_IT, "that a correct delta satisfies that property"),
        (SILENCE_DOES_NOT_SATISFY_IT, "that silence after a later change satisfies nothing"),
        (
            A_FALSE_CLAIM_STILL_FAILS,
            "that a claim about a check or a commit that was never run or made is false",
        ),
        (
            RERUN_WHAT_A_CHANGE_COULD_HAVE_BROKEN,
            "that a change which could have invalidated a claim is re-run against",
        ),
        (
            UNRECHECKED_CLAIMS_ARE_NAMED,
            "that the claims left un-re-checked are named instead",
        ),
        (
            A_RUN_FROM_BEFORE_THE_CHANGE_IS_EVIDENCE,
            "that citing a run taken before a change is correct evidence",
        ),
        (
            AN_INDUCED_FAILURE_IS_EVIDENCE,
            "that citing a failure induced on purpose is correct evidence",
        ),
        (
            THE_CITATION_SAYS_WHICH_IT_IS,
            "that such a citation carries which of the two it is",
        ),
    ):
        assert stated.search(appendix), (
            f"{APPENDIX} no longer says {missing}; without it the demand it replaced is "
            "the only reading left of what a report owes, and that demand failed six "
            "nodes whose work was complete and green"
        )


def test_a_commit_that_cannot_affect_a_check_leaves_its_evidence_standing(
    appendix: str,
) -> None:
    """The conditional rule's limit, stated where judges had read the rule as absolute.

    *Do not carry a claim forward across a change that could have invalidated it* is a
    condition, and two dispatches of about 45 minutes each were refused over a
    comment-only final commit anyway: read as absolute it is unsatisfiable, because every
    commit comes after the last check run. So the file says what the condition excludes,
    bounded by what the check reads rather than by the kind of change — which is what keeps
    it from becoming a licence, since the same comment is not inert for a judged lint that
    reads it — and the report owes the commit's name and why it is inert.

    Held after the conditional rule and as the third of the section's recorded incidents,
    because the clause limits that rule rather than replacing it, and the two earlier
    incidents are the reasons the rest of the section stands.
    """
    section = appendix[: appendix.index(STATE_THE_BAR)]
    for stated, missing in (
        (
            INERT_COMMIT_LEAVES_EVIDENCE_STANDING,
            "that a commit which cannot affect a check leaves that check's evidence standing",
        ),
        (
            THE_REPORT_NAMES_THE_INERT_COMMIT,
            "that the report names that commit and says why it is inert",
        ),
        (BOUNDED_BY_WHAT_THE_CHECK_READS, "that what the check reads decides whether it is"),
        (
            UNREAD_COMMENTS_ARE_INERT,
            "that a comment- or documentation-only commit to content a check does not read "
            "is inert for that check",
        ),
        (
            READ_COMMENTS_ARE_NOT_INERT,
            "that the same comment is not inert for a check that reads comments",
        ),
    ):
        assert stated.search(section), (
            f"{APPENDIX} no longer says {missing}. Without it, judges read 'a change that "
            "could have invalidated it' as every commit, and two dispatches with correct "
            "trees were refused over a comment-only final commit"
        )

    conditional = RERUN_WHAT_A_CHANGE_COULD_HAVE_BROKEN.search(section)
    allowance = INERT_COMMIT_LEAVES_EVIDENCE_STANDING.search(section)
    assert conditional is not None and allowance is not None
    assert conditional.start() < allowance.start(), (
        f"{APPENDIX} states the inert-commit allowance before the conditional rule it limits"
    )

    recorded = [incident.search(section) for incident in RECORDED_INCIDENTS]
    unrecorded = [
        incident.pattern
        for incident, found in zip(RECORDED_INCIDENTS, recorded, strict=True)
        if found is None
    ]
    assert not unrecorded, (
        f"{APPENDIX}'s claims section no longer records all three of its incidents: {unrecorded}"
    )
    starts = [found.start() for found in recorded if found is not None]
    assert starts == sorted(starts), (
        f"{APPENDIX} records the comment-only-commit incident before the two it follows"
    )


def test_the_appendix_no_longer_orders_where_the_completion_report_sits(
    appendix: str,
) -> None:
    """The withdrawn demand, asserted absent rather than argued against.

    Prose that stops arguing for a rule but still states it is the rule still being
    enforced: a worker reads the instruction, and a judge handed the same text imports
    it. Both spellings are held, because each is separately actionable — the report
    being last, and anything found afterwards being fixed and then reported afresh.
    """
    for withdrawn, what in (
        (REPORT_IS_LAST, "that the completion report is the last thing the dispatch produces"),
        (REPORTED_AFRESH, "that what is found afterwards is fixed and then reported afresh"),
    ):
        found = withdrawn.search(appendix)
        assert found is None, (
            f"{APPENDIX} demands {what} again ({found.group(0)!r}). That ordering cannot "
            "be satisfied by a dispatch whose supervisor keeps asking after the report, "
            "and it failed six nodes with complete, green work and no criterion unmet"
        )


def test_the_worked_wait_backgrounds_one_command_behind_its_own_sentinel(
    appendix: str,
) -> None:
    """The wait, held to the naming rule the passage beside it states.

    Its predecessor backgrounded two of the complete gate's three parts, which failed a
    node for running the components separately, and wrote `/tmp/gate.exit` — a shared
    path, in a file whose own next paragraph requires one sentinel per invocation, on a
    host where three workers collided on shared names in one day. So what is asserted is
    the pair: the example backgrounds one whole command, and the sentinel it polls is the
    one that command writes and is named after the invocation that ran it.
    """
    backgrounded = BACKGROUNDED.findall(appendix)
    assert backgrounded, f"{APPENDIX} no longer shows how to wait on a long command at all"

    waited = WAITED_ON.search(appendix)
    assert waited is not None, (
        f"{APPENDIX} backgrounds a command and never polls for its sentinel, which is the "
        "half that makes the wait end"
    )
    sentinel = waited["sentinel"]
    assert PER_INVOCATION_PATH.search(sentinel), (
        f"{APPENDIX} waits on {sentinel!r}, a path every dispatch on this host would "
        "write; name it after the invocation, as the paragraph below this example requires"
    )
    for command in backgrounded:
        assert sentinel in command, (
            f"{APPENDIX} backgrounds {command!r} and waits on {sentinel!r}, which that "
            "command does not write — so the wait ends on somebody else's result"
        )
        chained = CHAINED_INVOCATION.search(command)
        assert chained is None, (
            f"{APPENDIX} backgrounds {chained.group(0)!r}: a chain rather than the one "
            "command a worker meant to run, which is the shape that failed a node for "
            "running its parts separately"
        )


def test_the_appendix_asks_for_the_bar_to_be_stated_as_criteria(appendix: str) -> None:
    """The demands a judge imports when the criteria are silent, named here as criteria.

    Two branches were failed by demands nobody wrote down: end-to-end proof, which the
    built-in `engineer` bar makes of every implementation dispatch, and a final
    completion report, which is in neither the task nor the shared clause. Asking for
    both here is what gives `just review-plan`'s judged turn something to read the
    criteria against — this file is one of the two places a demand is made, and whether
    the criteria answer it is judged by meaning rather than by the phrase matching that
    used to ask it deterministically and refused wordings the same review had asked for.

    The second demand is asked for as the *property* rather than as the artifact, and this
    reads the section that asks rather than the whole file for that reason: both wordings
    also occur elsewhere here — `"completion report"` where the incident is recounted, the
    property where a worker is told it — so a whole-file assertion would go on passing
    after the bullet asking for either had been replaced by anything at all.
    """
    asked = appendix[appendix.index(STATE_THE_BAR) :]

    for demand in ("proven end to end", "true of the tree as it finally stands"):
        assert demand in asked, (
            f"{APPENDIX} no longer asks for {demand!r} as an acceptance criterion, so a "
            "node that omits it is judged on the bar's own reading of it instead"
        )


def test_the_polling_rule_is_about_pgrep_rather_than_about_gates(appendix: str) -> None:
    """The rule stated as the mechanism, which is the only form that transfers.

    It was written as advice about waiting on a *gate*, and readers who had read it
    walked into the same trap against other patterns: a worker wedged on
    `scripts/fetch-corpus.sh`, another on `llmlint-judge.sh`, and the manager an hour
    after issuing the rule on `publish-branch`. None of those is a gate, and every one
    of them is the same defect — `pgrep -f` matches against whole command lines and
    excludes only its own process, so the pattern naming what you are waiting for is by
    construction inside the command line of the shell doing the waiting.

    Asserted where the file first reaches for `pgrep -f`, so a future edit that puts
    the gate-only wording back has to put it somewhere this cannot see, rather than
    merely reword the sentence. That passage is now *below* the rule on signalling,
    which is what
    :func:`test_the_rule_on_signalling_is_stated_before_any_reasoning_about_matching`
    holds: the self-match explains a wait that never ends, and never why somebody
    else's process may not be killed.
    """
    introduced = POLLING.search(appendix)
    assert introduced is not None, f"{APPENDIX} no longer says anything about `pgrep -f`"

    paragraphs = [block for block in appendix.split("\n\n") if POLLING.search(block)]
    stated = "\n\n".join(paragraphs[:1])

    assert MATCHES_COMMAND_LINES.search(stated), (
        f"{APPENDIX} introduces `pgrep -f` without saying it matches full command "
        "lines, which is the whole reason a wait on one matches its own poll; stated "
        "as a fact about gates instead, it has failed to transfer three times"
    )
    assert EXCLUDES_ONLY_ITSELF.search(stated), (
        f"{APPENDIX} no longer says `pgrep` excludes only itself. Excluding itself is "
        "what makes the rule look safe — the shell doing the waiting is the parent, and "
        "nothing excludes that"
    )


def test_the_appendix_gives_a_self_match_proof_way_to_ask_what_is_running(
    appendix: str,
) -> None:
    """The half the old text answered nowhere, and a pattern kill refused by name.

    Telling a reader not to wait on `pgrep -f` leaves them with a real need — is this
    thing running at all? — and the old paragraph answered it with nothing, so readers
    reached for the broken form anyway. `pgrep -x` matches the executable name, which a
    polling shell called `bash` cannot collide with; `ps -eo pid,args | grep -v grep` is
    the fallback where the binary name identifies nothing. Both are reads: `pkill -f` is
    the same self-match with a signal attached, and one worker reached for it against
    three patterns while another manager's run was live on this host.
    """
    assert BY_EXECUTABLE.search(appendix), (
        f"{APPENDIX} gives no self-match-proof way to test whether a process is "
        "running. `pgrep -x` matches the executable name rather than the command line, "
        "so the shell asking cannot match itself; without it a reader talked out of "
        "`pgrep -f` has nothing to reach for and reaches for it anyway"
    )
    assert PS_FALLBACK.search(appendix), (
        f"{APPENDIX} names no fallback for a binary whose name is not distinctive "
        "(`just`, `node`, `python` run everything), where `pgrep -x` cannot answer; the "
        "`ps -eo pid,args | grep -v grep` form is what drops that pipeline's own match"
    )
    assert NEVER_PATTERN_KILL.search(appendix), (
        f"{APPENDIX} does not refuse `pkill`, which knows nothing about whose process it "
        "matched — on a host several managers share, that is a worker killing somebody "
        "else's run"
    )
    assert PER_INVOCATION.search(appendix), (
        f"{APPENDIX} no longer names one sentinel and one log per invocation, which the "
        "rewrite of the polling rule had to leave standing"
    )


def test_the_pre_launch_check_is_about_sentinel_ownership_not_gate_concurrency(
    appendix: str,
) -> None:
    """Keep a shared path from becoming an implied host-wide gate lock.

    Two workers used distinct sentinel and log names, yet monitors read the old
    ``nothing is already in flight`` sentence as forbidding their gates because it
    never named the thing being checked. The check is path ownership; concurrent
    gates and judged tiers remain valid when their invocations own distinct paths.
    """
    assert SENTINEL_PATH_OWNERSHIP.search(appendix), (
        f"{APPENDIX} does not say that the pre-launch check is for an invocation's "
        "sentinel path. Without that object, a reader can treat the check as a ban on "
        "starting a gate while any other gate is running"
    )
    assert CONCURRENT_RUNS_ALLOWED.search(appendix), (
        f"{APPENDIX} does not distinguish sentinel-path ownership from concurrent "
        "gates and judged tiers, so the retired serialization rule remains available "
        "as an implication"
    )
    ambiguous = AMBIGUOUS_IN_FLIGHT.search(appendix)
    assert ambiguous is None, (
        f"{APPENDIX} still carries the ambiguous pre-launch instruction "
        f"({ambiguous.group(0)!r}) that two monitors read as a concurrent-gate ban"
    )


def test_the_rule_on_signalling_is_stated_before_any_reasoning_about_matching(
    appendix: str,
) -> None:
    """Whose process it is, stated ahead of how the pattern matched.

    Order is the assertion because order is what failed. Every paragraph of the old
    passage argued from `-f`'s self-match; `-x` does not self-match, and the same passage
    then recommended `pgrep -x` as the careful way to ask what is running. A reader who
    took the argument to `pkill -x` had taken it where it leads — and `pkill -x just`
    takes every `just` on the machine, where `pkill -f 'just gate'` at least needs a
    narrowing pattern. So the axis that decides it has to be met first, and the
    self-match has to be met as the explanation of a wait rather than as the reason not
    to signal.
    """
    signalling = SIGNAL_ONLY_BY_PID.search(appendix)
    assert signalling is not None, (
        f"{APPENDIX} no longer states the rule on the axis that decides it. A kill by "
        "name knows nothing about whose work it matched, and two workers reached that "
        "place in one day while another manager's dispatch was live"
    )
    matching = POLLING.search(appendix)
    assert matching is not None, f"{APPENDIX} no longer says anything about `pgrep -f`"
    assert signalling.start() < matching.start(), (
        f"{APPENDIX} reasons about how a pattern matches before it says a process is "
        "never signalled unless it was identified by its own PID. Read in that order the "
        "self-match reads as the reason, and `pkill -x` — which does not self-match — "
        "reads as the careful form it licenses"
    )
    assert PID_FROM_A_PATTERN.search(appendix), (
        f"{APPENDIX} does not say that a PID obtained from a pattern is still a pattern "
        "kill. One of the two workers read the process table first and piped the pids "
        "into `kill`, which obeys every other sentence here and does the same damage"
    )
    assert READS_VERSUS_SIGNALS.search(appendix) and UNSCOPED_X_KILL.search(appendix), (
        f"{APPENDIX} no longer separates the tool that reads from the tool that signals, "
        "or no longer says what an unscoped `pkill -x` takes. `-x` is the safer spelling "
        "for `pgrep` and the more dangerous one for `pkill`, and a reader given one rule "
        "for both flags applies the wrong half"
    )
    assert CAPTURED_AT_LAUNCH.search(appendix), (
        f"{APPENDIX} shows no way to hold a PID that was never matched at all. A rule "
        "that only forbids leaves the reader needing a pid and reaching for a pattern to "
        "get one; `cmd & MYPID=$!` is what makes the question not arise"
    )


def test_the_appendix_no_longer_forbids_running_two_gates_at_once(appendix: str) -> None:
    """A rule this file states is paid for by every dispatch, so a stale one is deleted.

    It rested on this repository's e2e configs binding fixed ports, and they do not:
    live e2e code allocates through `_free_port()` in
    `tests/dag_ui/test_dag_ui_serving_e2e.py`, the remaining `43xx` literals are recorded
    fixtures under `tests/fixtures/`, and the port literals left in live test code are
    rendered-command and argument-validation assertions that bind nothing. Measured
    besides: on 2026-08-24 two managers' judged tiers ran concurrently — one node's
    overlapping another run's whole bootstrap-gate-judge chain — and both completed.

    The reason is asserted gone beside the instruction, because a reason left behind is
    an instruction a reader reconstructs.
    """
    instruction = CONCURRENT_GATE_INSTRUCTION.search(appendix)
    assert instruction is None, (
        f"{APPENDIX} tells a worker not to run two gates concurrently "
        f"({instruction.group(0)!r}). Nothing on this host makes that true any more, and "
        "a rule stated here constrains every dispatch this host makes"
    )
    reason = FIXED_PORT_REASON.search(appendix)
    assert reason is None, (
        f"{APPENDIX} still offers the fixed-port conflict as a reason "
        f"({reason.group(0)!r}); live e2e code allocates its ports through `_free_port()` "
        "and a reader handed the reason reinstates the rule"
    )


def test_the_appendix_names_the_per_dispatch_scratch_directory(appendix: str) -> None:
    """Where a dispatch's own files go, with the contract that lets it be used unchecked.

    The alternative is not "nowhere": it is a `/tmp` path the worker made up, on a host
    where several dispatches share `/tmp` and one of them has already read another's
    result as its own. So the variable is named together with what it replaces, and with
    the four properties that make a fallback branch unnecessary — a worker that has to
    test for the directory writes the `/tmp` path anyway, in the else arm.

    This file states the contract and does not provide it; the engine does. So it states
    those four properties and stops: a worker relying on a fifth that nobody promised is
    the next defect, and prose is where such a promise gets made by accident.
    """
    assert SCRATCH_DIRECTORY.search(appendix), (
        f"{APPENDIX} no longer names ONEPIPELINE_NODE_SCRATCH_DIR, so a dispatch needing "
        "somewhere to write invents a `/tmp` path and shares it with every other dispatch "
        "on this host"
    )
    assert INSTEAD_OF_INVENTING_ONE.search(appendix), (
        f"{APPENDIX} names the scratch directory without saying what it is instead of. A "
        "variable with no alternative beside it reads as trivia, and the worker writes to "
        "`/tmp` as before"
    )
    for stated in SCRATCH_CONTRACT:
        assert stated.search(appendix), (
            f"{APPENDIX} states the scratch directory without the property "
            f"{stated.pattern!r}. A directory with no contract is one a worker probes and "
            "falls back from, which is the invented path again with an extra branch"
        )


def test_the_appendix_tells_a_worker_to_commit_each_piece_as_it_works(appendix: str) -> None:
    """Committing as the work goes, stated as what happens if it does not.

    Held on the consequence as well as the instruction: "commit often" is advice a
    worker weighs against tidiness, while "a dirty worktree does not survive this
    dispatch" is a fact about this host that settles it. The shared preamble in
    `config/onejudge.base.yaml` says the same thing, and this file is where a worker
    reads it beside the operational detail — one of the two alone has already been read
    as a preference.
    """
    assert COMMIT_EACH_PIECE.search(appendix), (
        f"{APPENDIX} no longer tells a worker to commit each coherent piece as it works, "
        "so a dispatch that ends early takes finished work with it"
    )
    assert UNCOMMITTED_IS_LOST.search(appendix), (
        f"{APPENDIX} asks for commits without saying what an uncommitted tree costs. The "
        "instruction is weighed against tidiness unless the consequence is beside it"
    )
    assert CHECKED_BEFORE_COMMITTED.search(appendix), (
        f"{APPENDIX} no longer says when the checks a piece needs are run. "
        "`config/onejudge.base.yaml` demanded the project's deterministic tier before "
        "every commit, which named a tier where it meant a time and contradicted the "
        "scoping rule this file opens with; the timing is what survives that removal, "
        "and without it 'the moment it works' is a commit nobody has evidence for"
    )


def test_the_appendix_tells_a_worker_to_ask_rather_than_stop(appendix: str) -> None:
    """The ending a careful worker reaches, and the seam that replaces it.

    A worker that meets a decision which is not its own and declines to make it is doing
    the right thing up to the point where it stops: one reasoned about a frozen contract
    correctly, named two options for its owner, and then ended three turns with nothing
    committed and nobody asked — about fifteen minutes of correct work reported as `ahead
    of main: 0 commit(s)`. So the seam is named rather than left as "ask your manager",
    which a worker satisfies by writing it in a report read after the dispatch is over.
    """
    assert ASK_RATHER_THAN_STOP.search(appendix), (
        f"{APPENDIX} no longer tells a worker to ask rather than stop, so a decision fork "
        "it cannot pass ends the dispatch instead of reaching the manager"
    )
    assert ASK_SEAM.search(appendix), (
        f"{APPENDIX} tells a worker to ask without naming $ORCHESTRATOR_ASK_MANAGER, the "
        "seam every dispatch carries. Told only to ask, a worker writes the question in "
        "its report, which is read after it has already settled"
    )
    assert STOPPING_UNASKED.search(appendix), (
        f"{APPENDIX} does not name the ending it is refusing. The failure is not a worker "
        "that asked badly; it is one that stopped with the work undone and the question "
        "unasked, and a rule that does not say so reads as advice about phrasing"
    )


#: The drafting rule, in what it has to say: the command, where its shape is stated, that a
#: draft is unverified and is not the completion report, that a list at the end of a final
#: message is not a place follow-ups go, and the blocking half beside it.
DRAFT_COMMAND = re.compile(r"\$ORCHESTRATOR_FOLLOW_UP_DRAFT")
DRAFTING_RULE = (
    ("names where the shape is stated", re.compile(r"`--help`")),
    ("says what earns a draft", re.compile(r"outside\s+your\s+subtask", re.IGNORECASE)),
    ("counts the harness and agent context", re.compile(r"improvement\s+to\s+this\s+harness")),
    ("says a draft is unverified", re.compile(r"\bunverified\b", re.IGNORECASE)),
    ("says a draft is not the report", re.compile(r"not\s+your\s+completion\s+report")),
    ("refuses the end-of-message list", re.compile(r"end\s+of\s+your\s+last\s+message")),
    ("sends anything blocking over the seam", ASK_SEAM),
    ("never drafts in its place", re.compile(r"never\s+drafted\s+in\s+its\s+place")),
)


@pytest.mark.parametrize(
    ("what", "stated_by"), DRAFTING_RULE, ids=[row[0] for row in DRAFTING_RULE]
)
def test_the_appendix_states_the_drafting_rule_once_beside_the_blocking_route(
    appendix: str, what: str, stated_by: re.Pattern[str]
) -> None:
    """One paragraph says when to draft a follow-up, and says the channel half beside it.

    Follow-ups used to be noted at the end of a worker's final message and summarised by a
    judge into a report nobody opened. What replaces that is a command — so the rule names
    it, and names the other route in the same breath, because a draft read after the run is
    the wrong place for anything the manager needed now. Once, because this file is the one
    statement of dispatch policy and a second paragraph is a second answer.

    A read of the file, and deliberately no more: whether a model drafts or asks is the
    paid model's, which nothing here drives. That this paragraph reaches a real worker
    intact is `tests/e2e/test_dispatched_operational_notes_e2e.py`'s, and that the command
    it names works from inside a real dispatch is
    `tests/ask_seam/test_follow_up_drafts_launch_e2e.py`'s.
    """
    paragraphs = [block for block in appendix.split("\n\n") if DRAFT_COMMAND.search(block)]
    assert len(paragraphs) == 1, (
        f"{APPENDIX} names $ORCHESTRATOR_FOLLOW_UP_DRAFT in {len(paragraphs)} paragraphs; the "
        "drafting rule is stated once"
    )
    assert stated_by.search(" ".join(paragraphs[0].split())), (
        f"{APPENDIX}'s drafting rule no longer {what}:\n{paragraphs[0]}"
    )


def test_the_appendix_says_a_rate_limit_gh_disagrees_with_is_the_secondary_one(
    appendix: str,
) -> None:
    """The one rate limit that polling makes worse, told apart by the thing that reports it.

    Both halves are required. Without the test — a refusal naming a rate limit while
    `gh api rate_limit` still shows budget — a worker cannot tell which limiter it hit,
    and the primary one genuinely is waited out. Without the consequence, a worker that
    identifies it correctly still retries, because retrying is what a rate limit usually
    asks for.
    """
    assert SECONDARY_LIMITER.search(appendix), (
        f"{APPENDIX} no longer names GitHub's secondary limiter, so a worker reads every "
        "rate-limit refusal as the primary one and waits out a limit that is not there"
    )
    assert DISAGREEING_BUDGET.search(appendix), (
        f"{APPENDIX} names the secondary limiter without the read that tells it apart. "
        "`gh api rate_limit` reporting budget beside a refusal is the whole signal; "
        "without it the distinction is one a worker cannot make"
    )
    assert NOT_WAITED_OUT.search(appendix), (
        f"{APPENDIX} does not say that a further attempt extends the secondary limit. A "
        "worker that identified it correctly still retries, because that is what a rate "
        "limit normally asks for"
    )


def test_the_appendix_is_still_the_one_source_a_task_appendix_is_built_from(
    appendix: str,
) -> None:
    """Whatever else it says, it stays the block a task's `## Additional info` is.

    `orchestrator.criteria_guard.check_appendix` refuses a task that does not carry this
    file's whole content, so every addition above has to leave it a document that opens
    at that heading and is embeddable verbatim. Driven through that function rather than
    asserted about the text, because the function is what a plan is really refused by.
    """
    from orchestrator.criteria_guard import CriteriaError, check_appendix

    assert appendix.lstrip().startswith("## Additional info"), (
        f"{APPENDIX} no longer opens at the heading a task's appendix section is, so a "
        f"task built from it carries the block under no heading:\n{appendix[:120]}"
    )
    check_appendix(f"## What\nSomething.\n\n{appendix.strip()}\n", "carrying-it")
    with pytest.raises(CriteriaError, match="current operational appendix"):
        check_appendix("## What\nSomething.\n", "carrying-none")


def test_the_appendix_says_what_a_dispatch_that_changed_nothing_tracked_owes(
    appendix: str,
) -> None:
    """The allowance for a document-producing dispatch, in the file that selects checks.

    Held on what it *permits* as well as on the condition it names: one that named the
    condition and demanded the checks anyway would leave the file as false for that
    dispatch as saying nothing does. And on what may not decide it, since without that
    clause every dispatch can reach the allowance.
    """
    for stated, missing in (
        (NO_TRACKED_CHANGE, "what a dispatch that changed nothing tracked owes"),
        (COMPLETE_WITHOUT_THEM, "that such a dispatch is complete without those checks"),
        (NOT_BECAUSE_IT_IS_SLOW, "what may not decide that a dispatch is one of those"),
        (
            NOTHING_TO_COMMIT_IS_COMPLETE,
            "that having nothing to commit is then a correct outcome rather than a "
            "problem to solve by writing a file nobody asked for",
        ),
    ):
        assert stated.search(appendix), (
            f"{APPENDIX} no longer says {missing}. A dispatch whose deliverable is a "
            "document reads the selection rule above as owing checks it cannot run, and "
            "one has already been failed against that"
        )


def test_the_appendix_says_a_process_must_finish_inside_the_turn_that_started_it(
    appendix: str,
) -> None:
    """The turn boundary, which this file taught backgrounding across without naming.

    Held in three parts because each is separately actionable: the rule alone reads as
    style advice, the reason the launch looks fine is what stops a worker trusting a
    successful background launch, and one talked out of carrying a process across needs
    telling what to do with a slow check instead.
    """
    for stated, missing in (
        (FINISHES_INSIDE_ITS_TURN, "that a process must finish inside the turn that started it"),
        (
            THE_LAUNCH_LOOKS_FINE,
            "why the loss is invisible: the launch succeeded and the turn then ended normally",
        ),
        (
            WAIT_INSIDE_THE_TURN,
            "what to do with a slow check instead of carrying it across a turn boundary",
        ),
    ):
        assert stated.search(appendix), (
            f"{APPENDIX} no longer says {missing}. This file teaches backgrounding as the "
            "way to wait on a slow command, and a worker that reads only that ends its "
            "turn with the run still going and nothing to show for it"
        )


def test_the_publication_rule_keeps_its_rule_and_states_the_two_carve_outs(
    appendix: str,
) -> None:
    """Publication is the lifecycle's, and two things beside it are the worker's on a grant.

    A worker may open its session's change request as a draft and, stacked on it, a
    throwaway demonstration change request — each only when its task's own
    `## Additional info` says so. This is the one file that states that, so what is held
    is the whole shape: the rule kept, each carve-out opening on the task's grant, the
    commands, the base, the closing, and what stays forbidden. A worker reading to
    comply and a judge reading to refuse have to land in the same place, which is why
    the boundaries are stated rather than left to the commands.
    """
    rule = PUBLICATION_RULE.search(appendix)
    assert rule, f"{APPENDIX} no longer states that publication goes through the harness"
    paragraph = appendix[rule.start() : appendix.find("\n\n**", rule.end() + 1)]
    assert ONLY_THROUGH_ONEVCS.search(paragraph), (
        f"{APPENDIX} no longer says the session branch reaches its remote only through "
        "`onevcs publish`; that is the rule the carve-outs are carved out of"
    )
    assert LANDING_IS_NOT_THE_WORKERS.search(paragraph), (
        f"{APPENDIX} no longer says landing the change is not the worker's; with a draft "
        "the worker may open, that is the half of the rule a worker most needs stated"
    )
    assert EVERYTHING_ELSE_STAYS_FORBIDDEN.search(paragraph), (
        f"{APPENDIX} states the carve-outs without closing them: everything else a push "
        "or a change-request verb could do has to be said to stay forbidden"
    )

    for expectation, missing in (
        (OPENS_THE_DRAFT, "the command that opens the session's change request as a draft"),
        (NEVER_ONE_SECOND_CHANGE_REQUEST, "that a later draft publication opens no second one"),
        (DESCRIBES_THE_DRAFT, "the command that replaces the draft's description"),
        (READS_THE_DRAFT_BACK, "the command that reads the description back"),
        (NEVER_MARKED_READY_BY_THE_WORKER, "that the worker never marks the draft ready"),
        (BASED_ON_THE_SESSION_BRANCH, "that the demonstration is based on the session branch"),
        (NEVER_ON_THE_REPOSITORYS_BASE, "that it is never based on the repository's base"),
        (CLOSED_WITH_ITS_BRANCH, "the command that closes it and deletes its branch"),
        (CLOSED_BEFORE_FINISHING, "that it is closed before the worker finishes"),
    ):
        assert expectation.search(paragraph), f"{APPENDIX}'s carve-out no longer states {missing}"


def test_each_carve_out_opens_on_the_grant_the_criteria_guard_reads(appendix: str) -> None:
    """The words a task grants a carve-out in are the words `just check-plan` reads.

    `orchestrator/criteria_guard.py` admits a criterion about the worker's draft, or about
    a demonstration change request, only for a task whose own `## Additional info` grants
    it — and it reads the grant by the carve-out's own words. So each carve-out here has
    to open on a sentence that grant pattern matches: a planner told to write "in the
    words the appendix names" would otherwise write words the guard does not read, and
    the plan would be refused for a criterion its task had granted.
    """
    rule = PUBLICATION_RULE.search(appendix)
    assert rule, f"{APPENDIX} no longer states that publication goes through the harness"
    paragraph = appendix[rule.start() : appendix.find("\n\n**", rule.end() + 1)]
    grants = [" ".join(found["grant"].split()) for found in CARVE_OUT.finditer(paragraph)]
    assert len(grants) == len(AUTHORIZATIONS), (
        f"{APPENDIX} opens {len(grants)} carve-outs on the task's own `## Additional info` "
        f"and the criteria guard reads {len(AUTHORIZATIONS)} authorizations: {grants}"
    )
    for authorization in AUTHORIZATIONS:
        assert any(authorization.granted_by.search(grant) for grant in grants), (
            f"{APPENDIX}'s carve-outs open on {grants}, none of which is the wording the "
            f"criteria guard reads a grant of {authorization.name} by — a task writing "
            f"{authorization.grant!r} would be granting something this file does not name"
        )
