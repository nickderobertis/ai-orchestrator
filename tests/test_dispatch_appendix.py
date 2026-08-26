"""The operational appendix may not contradict itself about the complete gate.

`config/dispatch-appendix.md` is the text every dispatched task carries, and it was
gitignored scratch propagated by copy-paste until this suite tracked it. That is not
incidental to what went wrong in it: it named a three-part chain as the complete gate
to be run *once*, and then, four paragraphs later, demonstrated waiting on a gate with
a sentinel that backgrounded only two of those three parts. A worker that obeyed both
ran the parts separately and was failed for it, in its judge's own words: *"The
required complete gate (...) was never run once end-to-end; its components were run
separately."*

Nothing could have caught that, because nothing read the file. What is asserted here
is the property the two passages have to share rather than either one's wording: the
complete gate is **named once**, and every worked example that runs or waits on a gate
runs that name and never a part of the chain behind it. A future edit is free to change
what the chain is; it is not free to leave two passages disagreeing about it again.

The same file failed a second node the opposite way: it spelled the gate recipe as a
bare `just gate`, and the judge read that as the literal to check a report against, so
work that had run crozier's real bar — `just check`, crozier having no `gate` recipe —
was failed for naming it. The gate slot is therefore held as a substitution the reader
derives, and the file is required to say that what is checked is the one invocation
rather than the name in it.

A third passage failed the same way with a different subject. The rule on waiting for a
gate named `pgrep -f "just gate"` as its trap, so it read as advice about gates while the
defect is in the polling mechanism — and readers who had read it wedged against
`scripts/fetch-corpus.sh`, `llmlint-judge.sh` and `publish-branch` instead. Seven instances
are now recorded in it: four workers before one night, and two workers and the manager
during it, the manager an hour after instructing a worker about the rule. Which is why what
is asserted below is the mechanism (`pgrep -f` matches whole command lines and
excludes only its own process) together with the self-match-proof check the old text
answered nowhere.

The cheap-iteration rule is held the same way — by position rather than by phrasing —
because the cost of burying it is measured: one node lost about 84 minutes looping on
the whole gate to learn its lint findings, after the rule had already been written down.

The signalling rule is held on a different axis from the one it used to argue from. Every
paragraph of it argued from `-f`'s self-match, and `-x` does not self-match — so a reader
who followed that argument to `pkill -x` had followed it to a conclusion it supports. Two
workers reached that place in one day while another manager's dispatch was live, one by
`pkill -TERM -x just` and one by reading the table and piping pids into `kill`. What is
asserted now is the axis that actually decides it: a process is signalled only when it was
identified by its own PID, a PID obtained from a pattern is still a pattern kill, and the
self-match text stays as the explanation of a wait that never ends.

The instruction against running two gates concurrently is asserted **gone**, and its
absence is held rather than left to a reviewer's memory: it rested on this repository's
e2e configs binding fixed ports, live e2e code allocates through `_free_port()` instead,
and two managers' judged tiers ran concurrently on 2026-08-24 and both completed. A rule
this file states constrains every dispatch on this host, so one that has stopped being
true is a cost paid hundreds of times over.

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

#: The one definition every other passage has to call instead of spelling out.
DEFINITION = re.compile(r"^ *complete_gate\(\) *\{(?P<chain>[^}]*)\}", re.MULTILINE)

#: A command put into the background, which is what a sentinel example does. The
#: chain is what has to be in here, and this is where the contradiction lived.
BACKGROUNDED = re.compile(r"\(\s*(?P<command>[^()]*?)\s*\)\s*&")

#: Any single step of the chain, named directly. Legitimate in the definition and in
#: the cheap loop, and nowhere near a sentinel: the whole failure was a sentinel that
#: named two of these instead of the gate they compose into.
CHAIN_STEP = re.compile(r"\bjust +(?:bootstrap|gate|lint-llm-diff)\b")

#: The gate slot of the chain, as a substitution rather than as a literal recipe name.
#: `just "$GATE"` matches; `just gate` deliberately does not.
SUBSTITUTED = re.compile(r"just\s+\"?\$\{?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}?\"?")

#: A literal gate recipe name standing in the chain where a substitution belongs. This
#: is the defect itself: a name that does not exist in every repository, handed over as
#: though it were fixed.
BARE_GATE = re.compile(r"just\s+(?:gate|check)\b")

#: An assignment in the setup block a reader substitutes into, with whatever trails it.
#: `BASE` has always carried a `# confirm it:` derivation; the gate slot now must too.
ASSIGNMENT = re.compile(r"^ +(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<rest>.*)$", re.MULTILINE)

#: What a derivation looks like: the reader is shown the command that answers it.
DERIVATION = re.compile(r"confirm it:", re.I)

#: The report the judge is handed says what actually ran, which in a repository whose
#: full bar is not called `gate` is not the template's own spelling.
SUBSTITUTION_SATISFIES = re.compile(r"names?\s+the\s+command\s+you\s+substituted", re.I)

#: The two halves of the "once" rule, matched on their load-bearing words rather than
#: on a sentence — the same way everything else here is held, so that a reword is free
#: and a dropped half is not. The first says the complete gate confirms a finished tree
#: instead of being the loop that finds the findings; the second says "once" bounds
#: looping rather than budgeting a run that a later edit may then be charged to.
#: Written with `\s+` between words because this file is hard-wrapped, so any phrase
#: long enough to be worth pinning is one reflowing a paragraph can break across a line.
AGAINST_LOOPING = re.compile(r"over\s+the\s+\*\*finished\*\*\s+tree", re.IGNORECASE)
NOT_A_BUDGET = re.compile(r"not\s+a\s+per-dispatch\s+budget", re.IGNORECASE)
STALE_GREEN = re.compile(r"predates\s+your\s+last\s+edit", re.IGNORECASE)

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
#: owned, never whether another gate or judged tier is running. The former prevents
#: one invocation from reading another's result; the latter would reinstate the
#: retired concurrency rule through implication rather than by name.
SENTINEL_PATH_OWNERSHIP = re.compile(
    r"sentinel\s+path\s+is\s+already\s+in\s+use\s+by\s+another\s+invocation",
    re.IGNORECASE,
)
CONCURRENT_GATES_ALLOWED = re.compile(
    r"does\s+not\s+(?:forbid|prohibit)[^.]*?concurrent[^.]*?(?:gates?|judged\s+tiers?)",
    re.IGNORECASE,
)
AMBIGUOUS_IN_FLIGHT = re.compile(
    r"nothing\s+is\s+already\s+in\s+flight\s+before\s+starting\s+one",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def appendix() -> str:
    return (REPO_ROOT / APPENDIX).read_text(encoding="utf-8")


def test_the_complete_gate_is_defined_exactly_once(appendix: str) -> None:
    """One definition is what makes agreement between passages structural.

    Two spellings of the chain is the shape the contradiction had: each was locally
    reasonable, and nothing connected them. A reader following this file cannot end
    up running something other than what it calls complete if there is only one thing
    to run.
    """
    definitions = DEFINITION.findall(appendix)

    assert len(definitions) == 1, (
        f"{APPENDIX} defines the complete gate {len(definitions)} times; two spellings "
        "of the chain is exactly how its two passages came to disagree about what "
        "running it means"
    )


def test_the_definition_composes_every_step_of_the_chain(appendix: str) -> None:
    """A definition that dropped a step would be the same defect, moved.

    The middle step is asserted as a *slot* rather than as a name, because the recipe
    that is a repository's full bar is not always called `gate` — which is the whole
    subject of :func:`test_the_gate_recipe_is_derived_rather_than_handed_over_as_a_fixed_name`.
    What has to survive is that the chain still has three steps and that the gate is one
    of them; what it is called is the reader's to derive.
    """
    chain = DEFINITION.search(appendix)
    assert chain is not None, f"{APPENDIX} no longer defines `complete_gate`"

    for step in ("just bootstrap", "just lint-llm-diff"):
        assert step in chain["chain"], (
            f"{APPENDIX}'s complete gate no longer composes {step!r}, so a worker that "
            f"runs it has not run what this file calls complete:\n{chain['chain']}"
        )
    assert SUBSTITUTED.search(chain["chain"]), (
        f"{APPENDIX}'s complete gate no longer runs the repository's own gate recipe at "
        f"all, so a worker that runs it has not run what this file calls complete:\n"
        f"{chain['chain']}"
    )


def test_every_worked_example_waits_on_the_whole_chain(appendix: str) -> None:
    """The passage that failed a node, held to the passage that defines the gate.

    The sentinel example backgrounded `just bootstrap && just gate` — two of the
    three parts the prose four paragraphs above called complete. This asserts the
    class rather than the wording: whatever is put into the background has to be the
    gate's own name, and naming any step of the chain there is the defect returning
    under new spelling.
    """
    backgrounded = BACKGROUNDED.findall(appendix)

    assert backgrounded, f"{APPENDIX} no longer shows how to wait on a gate at all"
    for command in backgrounded:
        assert "complete_gate" in command, (
            f"{APPENDIX} backgrounds {command!r}, which is not the command it calls "
            "complete; a worker that waits on this has waited on something else"
        )
        named = CHAIN_STEP.search(command)
        assert named is None, (
            f"{APPENDIX} backgrounds {named.group(0)!r} rather than the whole gate "
            f"({command!r}) — which is the contradiction that failed a node for "
            "running the gate's components separately"
        )


def test_the_cheap_iteration_rule_is_the_first_thing_a_reader_meets(appendix: str) -> None:
    """Burying it has a measured cost, so its position is part of what it says."""
    first = re.search(r"\*\*(?P<rule>[^*]+)\*\*", appendix)
    assert first is not None, f"{APPENDIX} leads with no emphasized rule at all"

    rule = " ".join(first["rule"].split())
    assert "Iterate on the judged tier alone" in rule, (
        f"{APPENDIX} now leads with {rule!r}; the cheap-iteration rule is what this "
        "file exists to teach and it cost a node 84 minutes the last time it was not "
        "the first thing read"
    )


def test_the_rule_still_bounds_looping_on_the_complete_gate_without_capping_it(
    appendix: str,
) -> None:
    """The other half of the rule: the cheap loop is the judged tier, not the gate.

    "Once" is asserted here as a bound on *looping*, not as a cap on runs — the appendix
    is required to say both, and a rerun after a reported failure is the rule working.

    Both halves of "once" are required together, because each alone has been read
    wrongly here. Dropping the first lets a worker loop on the ~28-minute chain to
    discover findings the judged tier reports in two. Dropping the second lets a worker
    read "once" as a per-dispatch allowance — one did, edited a file after its gate had
    gone green, and would have reported that green as its verification, which by the
    letter of the older wording it was entitled to do.
    """
    assert "just lint-llm-diff" in appendix
    assert AGAINST_LOOPING.search(appendix), (
        f"{APPENDIX} no longer says the complete gate is run over the finished tree "
        "rather than looped on, which is the half of the rule that stops a worker "
        "spending 28 minutes a round on findings the judged tier reports in two"
    )
    assert NOT_A_BUDGET.search(appendix) and STALE_GREEN.search(appendix), (
        f'{APPENDIX} no longer says that the "once" is a rule against looping rather '
        "than a budget, or no longer says that a green predating your last edit has not "
        "verified your work; without both, a worker may cite a stale green and be right "
        "by the letter of this file"
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


def test_the_gate_recipe_is_derived_rather_than_handed_over_as_a_fixed_name(
    appendix: str,
) -> None:
    """The defect above, asserted as a shape: a derived value, not a name.

    Only the base ref carried a `# confirm it:` derivation, so the gate recipe beside it
    read as fixed. What that derivation says is free to change; handing over a name
    again is not.
    """
    chain = DEFINITION.search(appendix)
    assert chain is not None, f"{APPENDIX} no longer defines `complete_gate`"

    named = BARE_GATE.search(chain["chain"])
    assert named is None, (
        f"{APPENDIX} puts {named.group(0)!r} in the complete gate as a fixed name. The "
        "recipe that is a repository's full bar is not always called `gate` — crozier's "
        "is `check` — and a bare name here is both a command that may not exist and a "
        "literal a judge compares a report against, which has already failed finished work"
    )

    substituted = [
        found["name"] for found in SUBSTITUTED.finditer(chain["chain"]) if found["name"] != "BASE"
    ]
    assert substituted, (
        f"{APPENDIX}'s complete gate names no substitution for the gate recipe "
        f"({chain['chain']!r}); a worker in a repository without that recipe is left to "
        "guess whether it may deviate from this file"
    )

    derived = {
        found["name"] for found in ASSIGNMENT.finditer(appendix) if DERIVATION.search(found["rest"])
    }
    for name in substituted:
        assert name in derived, (
            f"{APPENDIX} substitutes ${name} into the complete gate but never shows how "
            f"to derive it. `BASE` carries a `# confirm it:` command and that asymmetry "
            f"is what made the other slot read as fixed"
        )


def test_what_the_judge_checks_is_one_invocation_and_not_a_recipe_name(
    appendix: str,
) -> None:
    """Both halves: the rule against running the parts separately, and its actual subject.

    The rule is unchanged — the chain runs end to end, in one command, over the finished
    tree. What is stated now is what it was always about: that the chain *ran*, not that
    the report echoes this file's own spelling of it. A report naming the command the
    worker substituted is the better report, because it says what actually ran.
    """
    assert "one invocation" in appendix, (
        f"{APPENDIX} no longer requires the complete gate to run as one invocation, which "
        "is the rule the substitution must not be read as relaxing"
    )
    assert AGAINST_LOOPING.search(appendix), (
        f"{APPENDIX} no longer says the complete gate runs over the finished tree"
    )
    assert SUBSTITUTION_SATISFIES.search(appendix), (
        f"{APPENDIX} no longer says that a report naming the substituted command satisfies "
        "the one-invocation rule — without it the template's own spelling reads as the "
        "literal to check a report against, which is how correct work was failed"
    )


def test_the_appendix_says_what_does_not_answer_which_recipe_is_the_gate(
    appendix: str,
) -> None:
    """The two authorities a reader would otherwise reach for, both wrong, named here.

    `config/onevcs.rules.yml` carried a per-identity `gate:` until onevcs 0.11.0 removed
    the concept, so it is no longer an answer at all. `just repos`'s gate column is the
    registry's own detection from the origin and the checkout, and it prints `just gate`
    for repositories that have no such recipe. The repository's own `just --list` is what
    decides, and this file is now the only place a dispatch learns that.
    """
    paragraphs = [block for block in appendix.split("\n\n") if "onevcs.rules.yml" in block]
    assert paragraphs, (
        f"{APPENDIX} no longer says that `config/onevcs.rules.yml` does not answer which "
        "recipe is a repository's gate; onevcs 0.11.0 removed the `gate:` key and a reader "
        "who does not know that reaches for a file that decides nothing"
    )
    paragraph = paragraphs[0]
    for authority in ("just repos", "just --list"):
        assert authority in paragraph, (
            f"{APPENDIX} names `config/onevcs.rules.yml` as no answer but says nothing "
            f"about {authority!r} in the same breath. Both wrong authorities and the one "
            "right one belong together, or a reader talked out of the first falls into "
            "the second"
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
    assert CONCURRENT_GATES_ALLOWED.search(appendix), (
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
