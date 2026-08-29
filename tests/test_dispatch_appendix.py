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

What the ordering incident left behind is held positively instead. Six of those settled
nodes were failed on where the completion report sat rather than on a missed criterion,
so the file now states the outcome that report must have — the last thing the dispatch
produces, describing the tree as it finally is — and that is asserted here as a property
rather than as a procedure for producing it.

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

`tests/test_criteria_guard.py` proves the guard that reads this file; here the subject
is the file itself, which is why these belong to the tier keyed on this repository's
prose. `tests/e2e/test_dispatched_operational_notes_e2e.py` is the other half and reads
the same rules out of the prompt a really dispatched worker was handed, which is where
they either arrive or do not.
"""

from __future__ import annotations

import re

import pytest

from orchestrator.criteria_guard import APPENDIX
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

#: The outcome the completion report owes, in the three parts that failed six nodes: it
#: is produced last, it describes the tree as it finally is, and anything found after it
#: is fixed and reported again rather than left behind a report that predates it.
REPORT_IS_LAST = re.compile(
    r"completion\s+report\s+is\s+the\s+last\s+thing\s+this\s+dispatch\s+produces", re.I
)
REPORT_DESCRIBES_THE_FINAL_TREE = re.compile(
    r"describes\s+the\s+tree\s+as\s+it\s+finally\s+is", re.IGNORECASE
)
REPORTED_AFRESH = re.compile(r"fixed\s+first\s+and\s+then\s+reported\s+afresh", re.IGNORECASE)

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


def test_the_completion_report_is_stated_as_the_outcome_it_must_have(appendix: str) -> None:
    """The residue of the removed gate, stated as a property rather than as an order.

    Six of fourteen nodes in one workstream settled `task-failed` with complete,
    gate-green work, every verdict naming where the completion report sat rather than a
    criterion it missed — one that the report appeared before the recorded commit and
    gate actions. A live `context` note warning a dispatch about the pattern did not
    prevent a recurrence, which is why this is written as what has to be true of the
    finished dispatch: the report is last, it describes the tree as it finally is, and
    anything found afterwards is fixed and reported again rather than left standing
    behind a report that predates it.
    """
    for stated, missing in (
        (REPORT_IS_LAST, "that the completion report is the last thing the dispatch produces"),
        (REPORT_DESCRIBES_THE_FINAL_TREE, "that it describes the tree as it finally is"),
        (REPORTED_AFRESH, "that what is found afterwards is fixed and then reported afresh"),
    ):
        assert stated.search(appendix), (
            f"{APPENDIX} no longer says {missing}; without it a dispatch that fixes "
            "something after reporting leaves a report describing a tree that no longer "
            "exists, which is what failed six nodes whose work was complete and green"
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
    both here is what makes `just check-plan` refuse a task that omits them — the guard
    enforces a demand where it is made, and this file is one of the two places it reads.
    """
    for demand in ("proven end to end", "completion report"):
        assert demand in appendix, (
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
    `tests/e2e/test_dag_ui_serving_e2e.py`, the remaining `43xx` literals are recorded
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
