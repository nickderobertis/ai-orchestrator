"""The clauses every dispatch shares say what is true of every dispatch, and stop there.

`config/onejudge.base.yaml` is where this host tells a dispatch what "done" means:
`system_prompt` is the preamble its worker reads, and `user.done_when` is the completion
criterion its judge is handed. What that clause says is said of a plan, a report, and a
diff alike — so a demand that is not true of all three fails the dispatches it is false
of, whatever they were actually asked for. Four nodes with finished, gate-green work were
failed that way: by a demand about *how* a criterion must be proven, which only the
criterion knows; and by one about state that exists only after the dispatch has ended,
which no worker can reach from inside its own run.

So the completion bar is read here twice — once for what it says, and once for the
vocabulary it may not say again. The second is the half that keeps working: a literal to
compare against is trivially updated alongside the file it mirrors, while a re-added
demand still carries the words of the class it belongs to.

`user.persona` used to be read against that same vocabulary and is now held **absent**,
which is a stronger guard than narrowing it ever was. That field is replaced rather than
merged — by a bare name resolving to a role built into the tool exactly as by a path into
`personas/` — and every dispatch names one of the two, so whatever it said reached
nothing. What it said last was a bound on the supervisor's authority, written after two
incidents in which a simulated user issued rulings on the manager's behalf; it was never
in force, and a field that reads like a protection while reaching no dispatch is the kind
a manager stops checking. So the guard is that there is no such field, rather than that
the field says something safe.

Nothing here was dropped rather than moved: what those demands carried is now each
node's own `## Acceptance criteria` to state, which `AGENTS.md` gives the plan's author.
One tail is deliberately kept, and `config/onejudge.base.yaml` records beside the clause
why it is not the next thing to remove.

The preamble half below is a guard about **which document** states a thing rather than
about what this one says. Dispatch policy — which checks a dispatch runs, and when its
work is done — has one source, `config/dispatch-appendix.md`, the only copy a worker and
its judge read together. So what is held below is that policy's absence, the one clause
that stays and the scope that makes it sayable, the pointer that keeps "states nothing"
from reading as "nothing is owed", and that no statement of it stands in both files.

`tests/e2e/test_orchestrate_launch_e2e.py` proves what this file cannot — that these
clauses are what a real launch hands a real worker and its real judge — from a launch
with only the paid model doubled. This file reads the file itself, because what is under
test here is what the words demand, not whether they arrive.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest
from shared_dispatch_bar import (
    BASE_CONFIG,
    appendix_text,
    judge_persona_default,
    phrases_in_both,
    shared_agent_preamble,
    shared_completion_bar,
)
from test_dispatch_appendix import DOWNSTREAM_CHECKS_ARE_NOT_JUDGED, WIDE_BAR

from orchestrator.criteria_guard import APPENDIX, CRITERIA_HEADING
from orchestrator.root import REPO_ROOT

#: The whole shared completion bar, so that changing it is a change to this file too.
#: What is left of it is the task's own criteria plus one clause about the tree the
#: dispatch leaves behind — nothing about how a criterion is proven, and nothing a
#: dispatch cannot settle before it ends.
SHARED_COMPLETION_BAR = (
    "every acceptance criterion stated in the task is met, with every change this "
    "dispatch made committed and nothing half-applied left behind"
)

#: Word stems no clause handed to every judge alike may use, each the surface of one of
#: the two failures above. A proof-method or check-tier word makes the shared clause
#: decide how a criterion is met, which only that criterion knows and which already
#: refused an argument from a repository's own contents. A word naming work that lands
#: after the dispatch points at state no worker can observe while it is still the one
#: that would have to satisfy it.
UNSHAREABLE = (
    "inspection",
    "verification",
    "gate",
    "check",
    "test",
    "lint",
    "coverage",
    "remote",
    "push",
    "pull request",
    "change request",
    "CI",
    "merge",
    "deploy",
    "publish",
)


def _uses(clause: str, stem: str) -> bool:
    """Whether `clause` uses `stem` as a word, in any inflection.

    Matched on a word boundary and an open suffix rather than as a substring: "check"
    has to catch "checks" and "CI" must not be found inside "decision", and a stem
    search that got either wrong would fail a clause that is fine or pass one that is
    not.
    """
    return re.search(rf"\b{re.escape(stem)}\w*", clause, re.IGNORECASE) is not None


def test_the_shared_completion_bar_is_the_tasks_criteria_and_the_commit_tail() -> None:
    """The one bar every dispatch is judged against, in full.

    Read as equality rather than as containment: what makes this clause correct is as
    much what it omits as what it states, and a containment check accepts every demand
    a later edit appends to it.
    """
    assert shared_completion_bar() == SHARED_COMPLETION_BAR, (
        f"{BASE_CONFIG}'s `user.done_when` is no longer the one clause every dispatch "
        "here is judged against; it may only state what is true of a plan, a report, "
        f"and a diff alike:\n{shared_completion_bar()}"
    )


@pytest.mark.parametrize("stem", UNSHAREABLE)
def test_the_shared_completion_bar_demands_nothing_only_some_dispatches_can_meet(
    stem: str,
) -> None:
    """The reduction stated as the class of demand it removed, not as one wording of it.

    This is what survives an edit that changes the literal above and the file together —
    the way a demand comes back is worded fresh, so it is caught by the vocabulary it
    cannot avoid rather than by the sentence it replaced.
    """
    bar = shared_completion_bar()
    assert not _uses(bar, stem), (
        f"{BASE_CONFIG}'s `user.done_when` uses {stem!r}, so the one clause every "
        "dispatch shares decides how a criterion is proven or points at state that "
        f"arrives after the dispatch ends; state it in that node's own "
        f"`## Acceptance criteria` instead:\n{bar}"
    )


def test_the_base_config_states_no_judge_persona_at_all() -> None:
    """The field a dispatch replaces states nothing, because it can reach nothing.

    Held as an absence rather than as a narrowing, and that is the whole of the repair.
    A `user.persona` here is not merged with the role a node names: `oneagentgraph`
    replaces it, whether that role is a file under `personas/` or a bare name resolving
    to one built into the tool, and every dispatch names one of the two. So a clause
    written here is read by nobody — which is how a bound on the supervisor's authority,
    written after two incidents that produced exactly that failure, came to be believed
    in force for as long as it existed while applying to nothing.

    Read in both shapes it could come back in, because the reader behind
    :func:`judge_persona_default` accepts a block scalar and a single quoted line alike.
    """
    stated = judge_persona_default()
    assert stated is None, (
        f"{BASE_CONFIG} states a `user.persona` again. Every dispatch replaces that "
        "field rather than merging it, so nothing there reaches a judge; state a review "
        "contract in the node's own persona or its `task`, where it will be read:\n"
        f"{stated}"
    )


class Policy(NamedTuple):
    """One check-selection policy the shared preamble may not state."""

    #: What it is, for the test id and the failure message.
    name: str
    #: The demand it cannot be made without, rather than the sentence it was.
    stated_by: re.Pattern[str]


#: Every policy about which checks a dispatch runs that this preamble has stated, held
#: **absent**. On the demand rather than the wording: a re-added policy is worded fresh.
CHECK_SELECTION_POLICY = (
    Policy(
        "rerunning the deterministic checks only for a change to code",
        re.compile(r"only\s+when\s+a\s+fix\s+touched\s+code|rather\s+than\s+prose", re.I),
    ),
    Policy(
        "the project's deterministic tier before every commit",
        re.compile(r"deterministic\s+(?:check\s+)?tier|deterministic\s+checks?", re.I),
    ),
    Policy(
        "which checks the acceptance criteria name",
        re.compile(r"run\s+the\s+checks\s+your\s+acceptance\s+criteria\s+name", re.I),
    ),
    Policy(
        "a ban on reporting success from inspection",
        re.compile(r"on\s+inspection\s+alone", re.IGNORECASE),
    ),
    Policy(
        "which checks a change earns",
        re.compile(r"checks?\s+that\s+exercise\s+what\s+you\s+changed", re.IGNORECASE),
    ),
    Policy(
        "how far to narrow a failing check",
        re.compile(r"narrowest\s+scope|iterate\s+against\s+that\s+check", re.IGNORECASE),
    ),
    Policy(
        "the refusal to bypass a hook",
        re.compile(r"pre-commit\s+or\s+pre-push|--no-verify", re.IGNORECASE),
    ),
    Policy(
        "what a dispatch that changed nothing tracked owes",
        re.compile(r"changed\s+nothing\s+tracked|complete\s+without\s+them", re.IGNORECASE),
    ),
    Policy(
        "what a retry after a refused publication is judged on",
        re.compile(
            r"checks-failed|not\s+runnable\s+by\s+a\s+worker|stated\s+local\s+acceptance"
            r"|downstream\s+check\s+passing",
            re.IGNORECASE,
        ),
    ),
)

#: Every rule about publication the preamble may not state, held **absent** the way the
#: check-selection policies above are. Publication is dispatch policy with one source:
#: `config/dispatch-appendix.md` states the rule and the two carve-outs a task may grant
#: — the worker's own draft, and a demonstration change request stacked on it — and a
#: preamble stating any rule about pushing, opening or landing a change request would be
#: a second copy the carve-outs then have to be carved out of twice. On the demand
#: rather than the wording, for the reason the policies above are.
PUBLICATION_POLICY = (
    Policy("a ban on pushing", re.compile(r"\bgit\s+push\b|\bno\s+push(?:es|ing)?\b", re.I)),
    Policy(
        "a rule about opening a change request",
        re.compile(
            r"\bgh\s+pr\b|\bopen(?:s|ed|ing)?\s+(?:a|the|its)\s+(?:pull|change)\s+request", re.I
        ),
    ),
    Policy(
        "a rule about landing or publishing the branch",
        re.compile(
            r"\bpublish(?:es|ed|ing)?\b|\bland(?:s|ed|ing)?\s+(?:it|the\s+branch|on)\b|\bmerg(?:e|es|ed|ing)\b",
            re.I,
        ),
    ),
    Policy("a rule about a draft", re.compile(r"\bdraft\b", re.I)),
)

#: The one thing the preamble may still say about a check, and the scope that makes it
#: sayable. Unscoped it read on every test in the repository, including ones failing for
#: the host's own reasons and ones the branch never touched.
COMPLETION_CLAUSE = re.compile(
    r"[Ww]ork\s+is\s+not\s+done\s+while\s+a\s+check\s+you\s+ran(?P<scope>[^.]*?)\s+is\s+failing"
)
CLAUSE_SCOPE = re.compile(r"bears\s+on\s+what\s+you\s+changed", re.IGNORECASE)

#: Where the preamble sends a reader for the policy it no longer states. Without it a
#: worker told nothing about checks concludes nothing is owed, which is the instruction
#: coming back through the worker's own judgment rather than through this file.
POINTS_AT_THE_TASK = re.compile(r"##\s+Additional\s+info")


@pytest.mark.parametrize("policy", CHECK_SELECTION_POLICY, ids=lambda row: row.name)
def test_the_preamble_states_no_policy_about_which_checks_a_dispatch_runs(policy: Policy) -> None:
    """Which checks a dispatch owes has one source, and this file is not it.

    One of these was not merely duplicated but false here: *rerun the deterministic
    checks only when a fix touched code rather than prose*, in a repository whose product
    is its tracked prose and whose documentation tier `tests/conftest.py` enforces.
    """
    preamble = " ".join(shared_agent_preamble().split())
    found = policy.stated_by.search(preamble)
    assert found is None, (
        f"{BASE_CONFIG}'s `system_prompt` states {policy.name} again ({found.group(0)!r}). "
        "Which checks a dispatch runs is `config/dispatch-appendix.md`'s to say — the "
        "one copy a worker and its judge read together — and two copies of it have "
        f"already disagreed:\n{preamble}"
    )


@pytest.mark.parametrize("policy", PUBLICATION_POLICY, ids=lambda row: row.name)
def test_the_preamble_states_no_rule_of_its_own_about_publication(policy: Policy) -> None:
    """What a dispatch may do to a remote has one source, and this file is not it.

    The appendix's publication paragraph keeps the rule and grants two carve-outs on the
    task's own say-so; `AGENTS.md`'s bypass rule points at that paragraph in one
    sentence. A third statement here — the shape the preamble's commit-as-you-go clause
    could easily grow — would be read by every worker and its judge as a rule the
    carve-outs do not reach, so the three are held to being one source, one pointer and
    one carve-out.
    """
    preamble = " ".join(shared_agent_preamble().split())
    found = policy.stated_by.search(preamble)
    assert found is None, (
        f"{BASE_CONFIG}'s `system_prompt` states {policy.name} ({found.group(0)!r}). "
        "What a dispatch may push, open or land is `config/dispatch-appendix.md`'s to "
        "say, carve-outs included; the preamble points at the task's own "
        f"`## Additional info` and states nothing of its own:\n{preamble}"
    )


def test_the_preamble_sends_a_reader_to_the_one_place_that_does_state_them() -> None:
    """Stating nothing is not the same as saying nothing, and the difference costs work.

    A worker handed a standing bar that never mentions checks concludes they are nobody's
    and runs whatever it judges best, which is the removed policy returning through its
    own judgment. So the preamble names where the policy is instead: the operational
    notes under the task's own `## Additional info`, which is `config/dispatch-appendix.md`
    verbatim, with the node's `## Acceptance criteria` stating what they have to show.
    """
    preamble = " ".join(shared_agent_preamble().split())
    assert POINTS_AT_THE_TASK.search(preamble), (
        f"{BASE_CONFIG}'s `system_prompt` states no check policy and names nowhere that "
        "does. A worker reads that as no checks being owed, or supplies its own list:\n"
        f"{preamble}"
    )
    assert CRITERIA_HEADING in preamble, (
        f"{BASE_CONFIG}'s `system_prompt` points at the operational notes without naming "
        f"{CRITERIA_HEADING}, which is what says what those checks have to show:\n"
        f"{preamble}"
    )


#: The preamble's one sentence about follow-ups, in the two routes it has to name: the
#: drafting command and where its shape is stated, and the channel for what cannot wait.
FOLLOW_UP_POINTER = re.compile(
    r"`\$ORCHESTRATOR_FOLLOW_UP_DRAFT`\s+\(its\s+`--help`\s+gives\s+the\s+shape"
)
BLOCKING_ROUTE = re.compile(r"ask\s+your\s+manager\s+now\s+over\s+`\$ORCHESTRATOR_ASK_MANAGER`")
#: Where follow-ups used to be sent, and where nothing ever read them.
FINAL_MESSAGE = re.compile(r"final\s+message", re.IGNORECASE)


def test_the_preamble_sends_follow_ups_to_the_drafting_command_and_blockers_to_the_channel() -> (
    None
):
    """A follow-up is drafted, anything blocking is asked now, and nothing goes in a report.

    The preamble is the only statement a dispatch whose task predates the appendix's
    drafting rule still gets, so its pointer at the command's `--help` has to be enough on
    its own — and it names the channel beside it, because a pointer at drafts alone reads
    as the place for everything a worker notices.

    A structural read, and a deliberate one: which route a model then takes is the paid
    model's, which no journey here drives. That the preamble carrying this sentence reaches
    a real worker verbatim is `tests/e2e/test_orchestrate_launch_e2e.py`'s, and that the
    command it names works from inside a real dispatch is
    `tests/ask_seam/follow_up_drafts_launch/test_follow_up_drafts_launch_e2e.py`'s.
    """
    preamble = " ".join(shared_agent_preamble().split())
    assert FOLLOW_UP_POINTER.search(preamble), (
        f"{BASE_CONFIG}'s `system_prompt` does not point follow-ups at "
        f"$ORCHESTRATOR_FOLLOW_UP_DRAFT and its --help:\n{preamble}"
    )
    assert BLOCKING_ROUTE.search(preamble), (
        f"{BASE_CONFIG}'s `system_prompt` points at drafting without the route for what "
        f"cannot wait:\n{preamble}"
    )
    assert not FINAL_MESSAGE.search(preamble), (
        f"{BASE_CONFIG}'s `system_prompt` still sends something to a final message, which "
        f"nothing reads:\n{preamble}"
    )


#: A repository path a comment cites, as this repository spells one.
CITED_PATH = re.compile(r"\b(?:tests|scripts|orchestrator|config|graphs|personas|docs)/[\w./-]*\w")


def test_the_base_config_asks_no_judge_for_an_assessment_and_cites_only_real_files() -> None:
    """The judge's follow-up list is gone, and no directive vouches for it through files.

    `assessment` asked every judge to summarise follow-ups into a report no view opens;
    drafts replaced it. The file-scoped directive that excused it cited three journeys
    that did not exist, so every path a remaining directive cites is held to existing.
    That no real dispatch's judge is asked one is read off the merged config a real launch
    wrote, by `tests/e2e/test_orchestrate_launch_e2e.py`.
    """
    document = (REPO_ROOT / BASE_CONFIG).read_text(encoding="utf-8")
    assert re.search(r"^assessment:", document, re.MULTILINE) is None, (
        f"{BASE_CONFIG} declares an `assessment` again; follow-ups are drafted with "
        "$ORCHESTRATOR_FOLLOW_UP_DRAFT, not summarised by a judge"
    )
    # Spelled in two pieces, because the judged lint reads the whole phrase anywhere in a
    # file as a directive of its own.
    for directive in re.findall("llm" + r"lint: ignore[^\n]*", document):
        for cited in CITED_PATH.findall(directive):
            assert (REPO_ROOT / cited).exists(), (
                f"{BASE_CONFIG}'s directive cites {cited}, which does not exist: {directive}"
            )


def test_the_preamble_scopes_its_one_completion_clause_to_the_change() -> None:
    """The clause a judge reads about a red check, held to the scope it needs.

    Unscoped it reaches every test in the repository, including ones failing for the
    host's own reasons, which no worker can clear beside the appendix's commit-as-you-go
    rule. The scope is what makes the two one instruction.
    """
    preamble = " ".join(shared_agent_preamble().split())
    clause = COMPLETION_CLAUSE.search(preamble)
    assert clause is not None, (
        f"{BASE_CONFIG}'s `system_prompt` no longer says that work is not done while a "
        "check it ran is failing. That is the one demand about checks this file makes, "
        f"and nothing else states it:\n{preamble}"
    )
    assert CLAUSE_SCOPE.search(clause["scope"]), (
        f"{BASE_CONFIG}'s `system_prompt` states that clause as {clause.group(0)!r}, "
        "which reads on every check in the repository. Scope it to a check that bears on "
        "what this dispatch changed"
    )


#: A project-wide verification demanded of every dispatch, in the wordings a re-added one
#: would take. Held as phrases rather than as stems because a preamble that named checks
#: at all used every stem inside them; kept as a constant because
#: `tests/e2e/test_persona_review_bar_e2e.py` reads a *persona's* delivered bar for the
#: same demand, and one vocabulary in two files is how those two answers stay one answer.
NO_PROJECT_WIDE_DEMAND = (
    "complete verification",
    "full verification",
    "complete gate",
    "whole gate",
    "entire gate",
    "at closeout",
)


def test_the_preamble_names_no_repository_wide_bar_at_all() -> None:
    """With no check policy left here, naming that bar has nothing to qualify.

    A regex and a phrase list, because they fail differently and a re-added demand is
    worded fresh: the regex catches a bar named complete, full, whole or entire, and the
    phrases catch wordings naming no bar at all — `at closeout` in particular.
    """
    preamble = shared_agent_preamble()
    named = WIDE_BAR.search(preamble)
    assert named is None, (
        f"{BASE_CONFIG}'s `system_prompt` names {named.group(0)!r}. Whose that bar is, "
        "and when it runs, is stated where a dispatch is told what it does owe"
    )
    for phrase in NO_PROJECT_WIDE_DEMAND:
        assert phrase.lower() not in preamble.lower(), (
            f"{BASE_CONFIG}'s `system_prompt` demands {phrase!r} of every dispatch again. "
            "That demand is what cost twenty to forty minutes a dispatch to learn what "
            "the merge path reports anyway; what a dispatch owes is the checks that "
            f"exercise its own change:\n{preamble}"
        )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_docs` routes a
# test between two targets of the project that already owns it, rather than standing in
# for a project. Both cases below read `config/dispatch-appendix.md`, which collection
# refuses without the marker.
@pytest.mark.reads_docs
def test_no_statement_of_dispatch_policy_stands_in_both_files() -> None:
    """The structural half: one policy, one document, and a gate rather than a rule.

    `AGENTS.md` has said since the suppression incident that this policy has one source
    and that it is `config/dispatch-appendix.md`; nothing checked it. Compared as a worker
    meets them: this preamble as a system prompt, the appendix as its task's
    `## Additional info`.
    """
    shared = phrases_in_both(shared_agent_preamble(), appendix_text())
    assert not shared, (
        f"{BASE_CONFIG}'s `system_prompt` and {APPENDIX} both state:\n"
        + "\n".join(f"  {run!r}" for run in shared)
        + f"\nDispatch policy has one source and it is {APPENDIX}; delete the copy here "
        "or state it there alone"
    )


@pytest.mark.reads_docs
def test_what_a_retry_is_judged_on_is_stated_in_the_appendix_alone() -> None:
    """The statement a judge assesses a `checks-failed` retry by, held to its one source.

    It is dispatch policy — which checks a dispatch is held to — so it is the appendix's,
    which every judge reads beside the task. Stated in `user.done_when` instead it would
    be a clause about checks handed to every judge of a plan, a report and a diff alike,
    which the vocabulary above refuses; stated in both it would be two answers to the one
    question a repaired retry was failed on.
    """
    appendix = " ".join(appendix_text().split())
    bar = shared_completion_bar()
    preamble = " ".join(shared_agent_preamble().split())
    for stated, what in DOWNSTREAM_CHECKS_ARE_NOT_JUDGED:
        assert stated.search(appendix), f"{APPENDIX} no longer says {what}"
        for field_name, clause in (("user.done_when", bar), ("system_prompt", preamble)):
            found = stated.search(clause)
            assert found is None, (
                f"{BASE_CONFIG}'s `{field_name}` says {what} ({found.group(0)!r}), which "
                f"{APPENDIX} already states; dispatch policy has one source"
            )


#: Sentences the appendix carries, each planted into the preamble to prove the gate below
#: catches a copy of it: the first a check-selection rule, the second the statement of what
#: a `checks-failed` retry is judged on.
PLANTED_SENTENCES = (
    "take the narrowest scope each one supports",
    "which can only happen once the retry has settled",
)


@pytest.mark.reads_docs
@pytest.mark.parametrize("copied", PLANTED_SENTENCES)
def test_a_policy_sentence_standing_in_both_files_fails_that_gate(copied: str) -> None:
    """The gate proven to catch what it is for, on the real files rather than on fixtures.

    Two documents that happen to agree today pass whether the gate discriminates or not,
    so a real appendix sentence is planted into the real preamble and the gate asked
    again.
    """
    appendix = appendix_text()
    assert copied in " ".join(appendix.split()), (
        f"{APPENDIX} no longer states {copied!r}, so this gate is being proven against a "
        "sentence that is not in it; plant one this file really carries"
    )

    planted = f"{shared_agent_preamble()}\n  - Pick those checks from your change, and {copied}."
    caught = phrases_in_both(planted, appendix)
    assert any(copied in run for run in caught), (
        f"the gate over {BASE_CONFIG} and {APPENDIX} passed a preamble carrying "
        f"{copied!r} verbatim out of the appendix, so it would not have caught the "
        f"duplication it exists for; it reported {caught}"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
