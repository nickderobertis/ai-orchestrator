"""No two configured plan sources store their records under one root directory.

Sharing a root is invisible to everything else this repository runs. Two `local-md`
sources over one directory both answer with every project in it, so a listing across
the default sources reports each plan twice — once under each source name — and
neither copy is wrong, no diagnostic is emitted, and a qualified `<source>:<project>`
id stops being unique in practice. The failure only shows up as a duplicated listing
somebody has to notice.

The check has to be able to fail, so it is exercised both ways: over the real
`onetaskgraph.yaml` and over a document that deliberately points two sources at one
root. The second is what says a green here means the roots are distinct rather than
that the reader found nothing.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from plan_sources import read_default_sources, read_plan_sources, shared_roots
from published_tools import ONETASKGRAPH_BIN

from orchestrator import follow_up_drafts, plan_copy, plan_review
from orchestrator.plan_store import WRITABLE_PLUGIN
from orchestrator.root import REPO_ROOT


def _configuration() -> str:
    return (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")


def test_no_two_configured_sources_share_a_root() -> None:
    """Every source `onetaskgraph.yaml` roots at a directory roots at its own."""
    shared = shared_roots(read_plan_sources(_configuration()))
    assert not shared, (
        "onetaskgraph.yaml points several sources at one root: "
        + "; ".join(
            f"{root} is claimed by {', '.join(sorted(names))}" for root, names in shared.items()
        )
        + " — every project under a shared root answers once per source, so a listing "
        "across the default sources reports each plan more than once"
    )


def test_the_check_names_both_sources_and_the_root_when_two_are_shared() -> None:
    """A document that shares a root fails, and the failure says which and where."""
    shared = shared_roots(
        read_plan_sources(
            "default_sources: [one, two]\n"
            "sources:\n"
            "  one:\n"
            "    plugin: local-md\n"
            "    config:\n"
            "      root: .plans\n"
            "  two:\n"
            "    plugin: local-md\n"
            "    config:\n"
            "      root: ./.plans/\n"
        )
    )
    assert shared == {".plans": ["one", "two"]}, (
        "two sources over one root must be reported with both names and the root, and "
        "two spellings of one directory must compare equal"
    )


#: The five values each GitHub Projects board this repository writes to is declared with,
#: which are never repointed. Literals rather than read from anywhere, because they are
#: the values this gate holds the file to.
BOARDS = {
    "plans": {
        "plugin": "github-projects",
        "owner": "nickderobertis",
        "project_number": "2",
        "repository": "nickderobertis/ai-orchestrator",
        "token_env": "GH_PROJECTS_TOKEN",
    },
    "followups": {
        "plugin": "github-projects",
        "owner": "nickderobertis",
        "project_number": "3",
        "repository": "nickderobertis/ai-orchestrator",
        "token_env": "GH_PROJECTS_TOKEN",
    },
}


@pytest.mark.parametrize(
    ("source", "setting", "expected"),
    [
        (source, setting, expected)
        for source, settings in BOARDS.items()
        for setting, expected in settings.items()
    ],
)
def test_the_board_source_is_left_exactly_as_it_was(
    source: str, setting: str, expected: str
) -> None:
    """Each board keeps every value it carries.

    `plans`: the retreat to a local Markdown store was designed to be undone by
    subtraction, and that only worked because `plans` was left alone throughout: it was
    deleted rather than repointed back. This survives the retirement because the reason
    outlives it — a live run's settlements are projected back to the project it was
    launched from, so repointing this source moves a running plan's store out from under
    it.

    `followups`: every session's verified tickets accumulate on that one board, and a
    follow-up run finds an open issue for its root cause by searching it — so repointing
    this source splits the accumulated tickets across two boards and every later run
    duplicates what the first one already filed.
    """
    board = read_plan_sources(_configuration())[source]
    actual = board.plugin if setting == "plugin" else board.settings.get(setting)
    assert actual == expected, (
        f"onetaskgraph.yaml's `{source}` source names {setting} {actual!r} where it carried "
        f"{expected!r}; this is a board this repository writes to, and it is never repointed"
    )


def test_the_follow_ups_board_is_no_default_source() -> None:
    """`followups` is read only by a command that names it.

    Every read that names no source is a plan listing — how `just check-plan` and the
    engine's own loader list a project's tasks — and a verified follow-up ticket is not a
    plan, so the board stays out of `default_sources`.
    """
    assert "followups" not in read_default_sources(_configuration()), (
        "the followups board is among default_sources, so every plan listing reads it"
    )


def _resolved_configuration() -> dict[str, object]:
    """Every setting the installed CLI resolves out of this checkout's own file."""
    # A launch exports `ONETASKGRAPH_SOURCES__…` names (the run's `drafts` root among
    # them), and the store layers every `ONETASKGRAPH_` name over the file, so reading
    # the file alone means handing the CLI none of them.
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONETASKGRAPH_")
    }
    result = subprocess.run(
        [str(ONETASKGRAPH_BIN), "--json", "config", "show"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload: object = json.loads(result.stdout)
    assert isinstance(payload, dict), payload
    settings = payload["settings"]
    assert isinstance(settings, list), settings
    resolved: dict[str, object] = {}
    for entry in settings:
        assert isinstance(entry, dict), entry
        key = entry["key"]
        assert isinstance(key, str), entry
        resolved[key] = entry["value"]
    return resolved


@pytest.mark.reads_checkouts
def test_the_reader_this_module_checks_roots_with_agrees_with_the_installed_cli() -> None:
    """The document reader above is reconciled against the release that really loads it.

    `tests/plan_sources.py` reads `onetaskgraph.yaml` with a reader for this document's
    own shape rather than with a YAML parser, so the shared-root check is worth exactly
    what that reader is worth — a restructured file it silently read as fewer sources
    would leave every assertion above passing over a configuration nobody checked.

    Uncached, because the subject is the installed producer rather than this workspace:
    a memo keyed on the tree here would replay across the very `onetaskgraph` upgrade
    that could change how a source document is loaded.

    A root is compared after being resolved against the directory holding the document,
    because that is what the adopted release answers: it makes a document's relative root
    absolute against the document's own directory rather than leaving each reading
    process to resolve it against a working directory of its own. The reader here reads
    what the document *says*, so resolving its answer the same way is what puts the two
    on one footing — and a root the document already states absolutely is left where it
    is by that join, which is how `test-fixtures` rides along as the control.
    """
    resolved = _resolved_configuration()
    text = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    read_here = read_plan_sources(text)
    assert {f"sources.{name}.plugin": source.plugin for name, source in read_here.items()} == {
        key: value for key, value in resolved.items() if key.endswith(".plugin")
    }
    assert {
        f"sources.{name}.config.root": str(REPO_ROOT / source.root)
        for name, source in read_here.items()
        if source.root is not None
    } == {key: value for key, value in resolved.items() if key.endswith(".config.root")}
    assert read_default_sources(text) == resolved["default_sources"]


#: The board option each status category this host states a mapping for is written to, per
#: source. The other party is the live board, which no check here may read, so what is held
#: is the mapping the installed CLI resolves out of the committed file — a `queued` that
#: stopped resolving to an option of its own would put a claimed item wherever `todo` or
#: `in progress` already sits, and a manager would read work in flight as free to take; a
#: `done` or `cancelled` left to a shipped default that moved would close a card at an
#: option that still reads as open on its face, which is what both boards stating them
#: prevents.
MAPPED_OPTIONS = {
    "plans": {
        "unknown": "Needs attention",
        "queued": "Queued",
        "done": "Done",
        "cancelled": "Cancelled",
    },
    "followups": {
        "backlog": "Proposal",
        "draft": "Deferred",
        "queued": "Queued",
        "done": "Done",
        "cancelled": "Cancelled",
    },
}
#: The options the two categories `queued` must not collide with reach, which this host
#: states no mapping for because the shipped defaults are already these. Held here so the
#: distinctness below is a comparison rather than an assumption; the wire half — that each
#: word really reaches its own option — is
#: `tests/e2e/test_onetaskgraph_host_e2e.py`'s.
UNMAPPED_OPTIONS = {"todo": "Todo", "in-progress": "In Progress"}


@pytest.mark.reads_checkouts
@pytest.mark.parametrize("source", sorted(MAPPED_OPTIONS))
def test_each_board_source_resolves_queued_to_an_option_of_its_own(source: str) -> None:
    """`queued` is mapped on both boards, to an option neither `todo` nor `in-progress` takes.

    Read through the installed CLI's own resolution rather than off the file, because the
    file is one layer of several and a `status_mapping` an environment variable overrode
    would leave this passing over a mapping nothing applies.

    Uncached for the reason its sibling above gives: the subject is the installed producer.
    """
    resolved = _resolved_configuration()
    prefix = f"sources.{source}.config.status_mapping."
    mapped = {
        key[len(prefix) :]: value for key, value in resolved.items() if key.startswith(prefix)
    }

    assert mapped == MAPPED_OPTIONS[source], (
        f"onetaskgraph.yaml's `{source}` source resolves {mapped}, and this host states "
        f"{MAPPED_OPTIONS[source]}"
    )
    assert mapped["queued"] not in UNMAPPED_OPTIONS.values(), (
        f"`queued` resolves to {mapped['queued']!r} on `{source}`, which is where "
        f"{UNMAPPED_OPTIONS} already sends a category: a claimed item would be "
        "indistinguishable from one nobody has taken"
    )


def test_every_source_a_planning_closeout_records_into_is_configured_and_writable() -> None:
    """`PLAN_SOURCES` restates names `onetaskgraph.yaml` owns, so it is reconciled here.

    A source renamed there and not here does not fail: it silently drops out of both the
    snapshot and the closeout, so a planning run stops recording what it authored and
    every plan of that store starts costing a judged turn to relaunch. Nothing else
    notices, which is exactly the drift a restated vocabulary needs a gate for.
    """
    configured = read_plan_sources(_configuration())
    for name in plan_review.PLAN_SOURCES:
        assert name in configured, (
            f"orchestrator/plan_review.py records a planner pass into the {name!r} source, "
            f"which onetaskgraph.yaml no longer configures; the two names are one "
            f"vocabulary and have to move together"
        )
        assert configured[name].plugin == WRITABLE_PLUGIN, (
            f"the {name!r} source is served by {configured[name].plugin!r}, and a review "
            f"record is only ever written into a {WRITABLE_PLUGIN!r} one"
        )


def test_the_store_a_planning_run_authors_into_is_recorded_into() -> None:
    """`just plan` writes into `authoring`, so a planning closeout has to record it.

    Named for what a planning run does rather than for "the plan store this repository
    plans against", which is the board and is a different question: a record cannot be
    written into a `github-projects` source at all, so the closeout covers the store a
    run authors into and `test_no_source_a_closeout_records_into_is_unwritable` above
    is what keeps the two from being confused.
    """
    authoring = read_plan_sources(_configuration())["authoring"]
    assert authoring.root == ".plans", authoring.root
    assert "authoring" in plan_review.PLAN_SOURCES, (
        "`just plan` writes a manager's brief into the 'authoring' source, so a planning "
        "run that authored one there must record a pass for it"
    )


def test_no_source_a_closeout_records_into_is_unwritable() -> None:
    """A closeout may only name sources a record can actually be written into.

    `plans` is the counter-example that gives this teeth: it is the store a plan of this
    repository lives on, it is the obvious name to reach for here, and a record can
    never be written into it — a closeout naming it would pass over every project on the
    board on every planning run, reporting a failure that is not one.
    """
    configured = read_plan_sources(_configuration())
    assert configured["plans"].plugin != WRITABLE_PLUGIN, (
        "this check is only worth running while a configured source exists that a record "
        "cannot be written into; `plans` was that source"
    )
    assert "plans" not in plan_review.PLAN_SOURCES, (
        "the 'plans' board is served by a plugin no review record can be written into, so "
        "a planning closeout must not try to record into it"
    )


def test_the_board_copy_plan_defaults_to_is_the_one_this_repository_plans_against() -> None:
    """`plan_copy.BOARD` restates a name `onetaskgraph.yaml` owns, so it is reconciled here.

    A source renamed there and not here does not fail loudly: `just copy-plan` naming no
    destination would refuse at the store with "no source named …", which reads as a typo
    in the invocation rather than as a constant that has gone stale — and the plan it was
    about would still be sitting in the local store, unreachable by the launch that wants
    it on the board.

    Held to being *defaulted to* as well as configured, because that is the property that
    makes it the board this repository plans against rather than merely a source it
    knows: a destination outside `default_sources` goes unlisted by every read that names
    no source, which is how the engine lists a project's tasks.
    """
    configured = read_plan_sources(_configuration())
    assert plan_copy.BOARD in configured, (
        f"orchestrator/plan_copy.py copies onto the {plan_copy.BOARD!r} source when a "
        f"caller names none, which onetaskgraph.yaml no longer configures (it configures "
        f"{sorted(configured)}); the two names are one vocabulary and move together"
    )
    assert plan_copy.BOARD in read_default_sources(_configuration()), (
        f"the {plan_copy.BOARD!r} source is configured but not defaulted to, so a plan "
        f"copied onto it is unlisted by every read that names no source — which is how "
        f"`just check-plan` and the engine's own loader list a project's tasks"
    )
    assert configured[plan_copy.BOARD].plugin != WRITABLE_PLUGIN, (
        f"the {plan_copy.BOARD!r} source is served by {WRITABLE_PLUGIN!r}, so a plan could "
        f"be reviewed there and `just copy-plan`'s whole reason for existing — that a "
        f"record can only be written where the plan is drafted — no longer holds"
    )


def test_the_drafts_source_is_a_local_directory_no_plan_listing_reads() -> None:
    """`drafts` holds unverified follow-ups as local Markdown, under an ignored root of its own.

    Local, because a draft is unverified and a board is where verified tickets go. Kept out
    of `default_sources`, because a draft is not a plan: every read that names no source is
    a plan listing, and a run's drafts would otherwise appear in each of them. Its root's
    distinctness from every other source is the shared-root check above; that nothing under
    it is ever committed is asked of git itself.
    """
    configured = read_plan_sources(_configuration())
    assert follow_up_drafts.SOURCE in configured, (
        f"onetaskgraph.yaml configures no {follow_up_drafts.SOURCE!r} source, which is where "
        "orchestrator/follow_up_drafts.py stores every draft"
    )
    drafts = configured[follow_up_drafts.SOURCE]
    assert (drafts.plugin, drafts.root) == (WRITABLE_PLUGIN, ".follow-ups"), (
        f"the drafts source is {drafts.plugin!r} rooted at {drafts.root!r}"
    )
    assert follow_up_drafts.SOURCE not in read_default_sources(_configuration()), (
        "the drafts source is among default_sources, so every plan listing shows drafts"
    )
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", ".follow-ups/tasks/run/drafts/x.md"],
        cwd=REPO_ROOT,
        check=False,
    )
    assert ignored.returncode == 0, "a draft under .follow-ups/ is not gitignored"
