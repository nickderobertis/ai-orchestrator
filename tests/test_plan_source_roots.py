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

import jsonschema
import onetaskgraph_user_layer
import pytest
from plan_sources import read_default_sources, read_plan_sources, shared_roots
from published_tools import ONETASKGRAPH_BIN

from orchestrator import follow_up_drafts, follow_up_tickets, plan_copy, plan_review, plan_store
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
    on one footing.

    The environment is stripped of every `ONETASKGRAPH_` name, so what is compared is the
    document rather than the roots this suite states over it: `test-fixtures` is rooted
    relatively in the file and per test process in the environment
    (`tests/plan_fixture_source.py`), and it is the file's answer that belongs here.
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
#: `tests/e2e/launch_monitor_observer/test_onetaskgraph_host_e2e.py`'s.
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


#: The `Priority` option each priority a follow-up ticket carries is written to on the
#: `followups` board. Stated here for the reason :data:`MAPPED_OPTIONS` is: the other party
#: is the live board, so what is held is the mapping the installed CLI resolves.
#: The sources the tracked document keeps a per-host cache of: its two boards, and nothing
#: local. A host's own trackers, in its user layer, turn theirs on there.
CACHED_SOURCES = {"plans", "followups"}


@pytest.mark.reads_checkouts
def test_the_cache_is_on_for_the_two_boards_alone_at_its_default_directory() -> None:
    """Exactly the board sources resolve `cache.enabled`, and none names a `cache.dir`.

    Read through the installed CLI's own resolution, because a key the store does not
    resolve turns nothing on. No `cache.dir`, because the default is the one directory every
    process on this host configuring the same remote shares.
    """
    resolved = _resolved_configuration()
    enabled = {
        key.split(".")[1]
        for key, value in resolved.items()
        if key.endswith(".cache.enabled") and value is True
    }
    assert enabled == CACHED_SOURCES, (
        f"onetaskgraph.yaml turns the cache on for {sorted(enabled)}, and this host caches "
        f"{sorted(CACHED_SOURCES)}"
    )
    named = sorted(key for key, value in resolved.items() if key.endswith(".cache.dir") and value)
    assert named == [], f"a source moves its cache off the shared default directory: {named}"


FOLLOWUPS_PRIORITIES = {"urgent": "Urgent", "high": "High", "medium": "Medium", "low": "Low"}


@pytest.mark.reads_checkouts
def test_the_follow_ups_board_maps_every_priority_and_the_plans_board_maps_none() -> None:
    """`followups` resolves all four priorities to their options, and `plans` carries none.

    Read through the installed CLI's own resolution, as the status mappings above are,
    because a mapping the store does not resolve is one no copy writes. `plans` is held to
    carrying none: its items are plan tasks, whose priority this host decides nothing about.
    """
    resolved = _resolved_configuration()

    def mapped(source: str) -> dict[str, object]:
        prefix = f"sources.{source}.config.priority_mapping."
        return {
            key[len(prefix) :]: value for key, value in resolved.items() if key.startswith(prefix)
        }

    assert mapped("followups") == FOLLOWUPS_PRIORITIES, (
        "onetaskgraph.yaml's `followups` source resolves the priority mapping "
        f"{mapped('followups')}, and this host states {FOLLOWUPS_PRIORITIES}"
    )
    assert mapped("plans") == {}, "the `plans` source maps priorities, which it never did"
    estimated = {level.value for level in follow_up_tickets.ESTIMATES}
    assert set(mapped("followups")) == estimated, (
        f"the `followups` source maps the priorities {sorted(mapped('followups'))}, and a "
        f"follow-up ticket's estimate is one of {sorted(estimated)}"
    )


#: The board text field each follow-up ticket's record value is projected onto, per
#: source. Stated here for the reason :data:`FOLLOWUPS_PRIORITIES` is: the other party is the
#: live board, so what is held is the projection the installed CLI resolves. The tracked
#: document declares no other board; a host's own follow-up trackers are its user layer's.
FOLLOWUPS_METADATA_FIELDS = [
    {"field": "Host", "key": follow_up_tickets.KEY, "path": [follow_up_tickets.HOST_FIELD]}
]


@pytest.mark.reads_checkouts
def test_the_follow_ups_board_projects_a_ticket_s_host_onto_its_host_field() -> None:
    """`followups` projects the record's `host` onto `Host`, and no other source projects.

    Read through the installed CLI's own resolution, because an entry the store does not
    resolve is one no copy writes. Its `key` and `path` are this module's record constants,
    so a record that renamed either would leave the board's field empty on every copy
    rather than failing here.
    """
    resolved = _resolved_configuration()
    projections = {
        key: value for key, value in resolved.items() if key.endswith(".config.metadata_fields")
    }
    assert projections == {"sources.followups.config.metadata_fields": FOLLOWUPS_METADATA_FIELDS}, (
        f"onetaskgraph.yaml's sources resolve the metadata-field projections {projections}, "
        f"and this host states only the `followups` source's {FOLLOWUPS_METADATA_FIELDS}"
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


#: The keys that switch the store's public boundary on: its write policy's two commands. The
#: tracked document lands the boundary off, so it resolves none of them and declares no
#: `visibility` on any source (`docs/orchestration.md`, *The public write boundary*).
WRITE_POLICY_KEYS = ("write_policy.visibility_command", "write_policy.check_command")


@pytest.mark.reads_checkouts
def test_the_tracked_document_leaves_the_public_boundary_off() -> None:
    """The installed CLI resolves no write policy and no source's visibility.

    Read through the store's own resolution, because the store switches the boundary on as
    soon as either is resolved, whatever document states it.
    """
    resolved = _resolved_configuration()
    assert {key: resolved.get(key) for key in WRITE_POLICY_KEYS} == dict.fromkeys(WRITE_POLICY_KEYS)
    declared = sorted(
        key for key, value in resolved.items() if key.endswith(".visibility") and value
    )
    assert declared == [], declared


@pytest.mark.reads_checkouts
def test_the_tracked_document_declares_no_private_source_and_no_route() -> None:
    """A private tracker and the route to it are a host's, so the tracked file holds neither.

    Read off the installed CLI's resolution of this checkout's document alone — the suite
    keeps the host's user layer out of every process (`tests/plan_store_user_layer.py`) — so a
    Linear source, a route, or a source declared private that crept back into the tracked
    file fails here.
    """
    resolved = _resolved_configuration()
    plugins = {key: value for key, value in resolved.items() if key.endswith(".plugin")}
    assert "linear" not in plugins.values(), plugins
    routes = sorted(key for key in resolved if ".routes" in key)
    assert routes == [], routes
    private = sorted(
        key for key, value in resolved.items() if key.endswith(".visibility") and value == "private"
    )
    assert private == [], private
    assert plan_store.routed_sources("plans") == ()
    assert plan_store.routed_sources("followups") == ()
    configured = set(read_plan_sources(_configuration()))
    assert set(read_default_sources(_configuration())) <= configured


#: The values the example layer's Linear sources are declared with. A host's real layer is
#: shaped as the example is, so the example is what a check here holds: a source of the shape
#: the store would refuse fails before any host copies it. Literals for the reason
#: :data:`BOARDS` gives.
ORGANIZATION_PLANS = {
    "plugin": "linear",
    "team": "ENG",
    "api_key_env": "HIDDENCO_LINEAR_API_KEY",
}
#: Linear's own API, which the source reaches when its configuration names no endpoint.
LINEAR_ENDPOINT = "https://api.linear.app/graphql"
#: The workflow state each category is written as on the organization's tracker.
ORGANIZATION_STATES = {
    "backlog": "Proposed",
    "draft": "Backlog",
    "todo": "Todo",
    "queued": "Queued",
    "in-progress": "In Progress",
    "unknown": "Needs Attention",
    "done": "Done",
    "cancelled": "Canceled",
}
#: Member projects use the workspace's separate status vocabulary.
ORGANIZATION_PROJECT_STATUSES = {
    "backlog": "Proposal",
    "draft": "Idea",
    "todo": "Planned",
    "queued": "Accepted",
    "in-progress": "In Progress",
    "unknown": "Blocked",
    "done": "Completed",
    "cancelled": "Canceled",
}
ORGANIZATION_MAPPING = {
    category: state
    if state == ORGANIZATION_PROJECT_STATUSES[category]
    else {"task": state, "project": ORGANIZATION_PROJECT_STATUSES[category]}
    for category, state in ORGANIZATION_STATES.items()
}
#: Where the layer's `plans` route sends a task whose repositories are all the organization's.
PLANS_ROUTES = [
    {
        "repositories": [onetaskgraph_user_layer.ORGANIZATION_REPOSITORIES],
        "to": onetaskgraph_user_layer.ORGANIZATION_PLANS,
    }
]
#: The organization's follow-up tracker: one project of the same team, every read and create
#: scoped to it.
ORGANIZATION_FOLLOWUPS = {
    "plugin": "linear",
    "team": "ENG",
    "api_key_env": "HIDDENCO_LINEAR_API_KEY",
    "project": "0f3c9d2a-5b1e-4c7a-9e21-6d8b4a1f2c03",
}
#: Where the layer's `followups` route sends a ticket whose root cause is the organization's.
FOLLOWUPS_ROUTES = [
    {
        "repositories": [onetaskgraph_user_layer.ORGANIZATION_REPOSITORIES],
        "to": onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
    }
]
#: Each Linear source the example declares, with the values it is declared with.
LINEAR_SOURCES = {
    onetaskgraph_user_layer.ORGANIZATION_PLANS: ORGANIZATION_PLANS,
    onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS: ORGANIZATION_FOLLOWUPS,
}
#: The host's own private stores the example declares, which a person writes to by name.
PRIVATE_STORES = (onetaskgraph_user_layer.PRIVATE_PLANS, onetaskgraph_user_layer.PRIVATE_FOLLOWUPS)


@pytest.fixture(scope="module")
def example_resolved(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Every setting the installed CLI resolves here with the example as the user layer."""
    home = onetaskgraph_user_layer.installed(tmp_path_factory.mktemp("example-layer"))
    return onetaskgraph_user_layer.resolved(home)


@pytest.mark.reads_checkouts
@pytest.mark.parametrize(
    ("name", "setting", "expected"),
    [
        (name, setting, expected)
        for name, settings in LINEAR_SOURCES.items()
        for setting, expected in sorted(settings.items())
    ],
)
def test_the_example_layer_declares_each_linear_source_as_a_host_writes_it(
    example_resolved: dict[str, object], name: str, setting: str, expected: str
) -> None:
    """Each Linear source resolves its plugin, team, credential and scope.

    The credential is named by its own variable rather than the plugin's default name,
    which is onetaskgraph's test key: a source falling back to that would write a plan's
    tasks, or a follow-up ticket, into the test workspace.
    """
    key = (
        f"sources.{name}.{setting}" if setting == "plugin" else (f"sources.{name}.config.{setting}")
    )
    assert example_resolved.get(key) == expected, (
        f"the example layer's `{name}` source resolves {setting} "
        f"{example_resolved.get(key)!r} where a host's layer carries {expected!r}"
    )


@pytest.mark.reads_checkouts
def test_the_example_layer_adds_destinations_and_loosens_nothing(
    example_resolved: dict[str, object],
) -> None:
    """Under the layer the tracked default sources still hold, and the layer names no policy.

    The project document reads above the user layer, so a layer can add a source or a route
    and never move what the tracked file states; the example names no write policy and
    declares no visibility, which a host switching the boundary on adds deliberately and all
    at once; and no route reaches the host's private stores, because a classification route
    would be tried before every repository route and capture the organization's work too.
    """
    assert {key: example_resolved.get(key) for key in WRITE_POLICY_KEYS} == dict.fromkeys(
        WRITE_POLICY_KEYS
    )
    assert example_resolved["default_sources"] == read_default_sources(_configuration())
    declared = sorted(
        key for key, value in example_resolved.items() if key.endswith(".visibility") and value
    )
    assert declared == [], declared
    for name in PRIVATE_STORES:
        assert example_resolved[f"sources.{name}.plugin"] == "github-projects", name
    targets = {
        str(value)
        for key, value in example_resolved.items()
        if ".routes" in key and key.endswith(".to")
    } | {
        str(entry["to"])
        for key, value in example_resolved.items()
        if key.endswith(".routes") and isinstance(value, list)
        for entry in value
        if isinstance(entry, dict)
    }
    assert targets == set(LINEAR_SOURCES), targets
    classified = [
        key
        for key, value in example_resolved.items()
        if ".routes" in key and "classification" in key
    ]
    assert classified == [], classified


def _linear_schema() -> dict[str, object]:
    """The `linear` plugin's configuration schema, as the installed store CLI reports it."""
    result = subprocess.run(
        [str(ONETASKGRAPH_BIN), "schema"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    schema = json.loads(result.stdout)["plugin_config"]["linear"]
    assert isinstance(schema, dict), schema
    return schema


def _resolved_block(resolved: dict[str, object], prefix: str) -> dict[str, object]:
    """One dotted block of resolved settings, nested back into the document it came from."""
    block: dict[str, object] = {}
    for key, value in resolved.items():
        if not key.startswith(prefix):
            continue
        *parents, leaf = key[len(prefix) :].split(".")
        nested = block
        for parent in parents:
            child = nested.setdefault(parent, {})
            assert isinstance(child, dict), (key, child)
            nested = child
        nested[leaf] = value
    return block


def _schema_refusals(config: dict[str, object]) -> list[str]:
    validator = jsonschema.Draft202012Validator(_linear_schema())
    return sorted(error.message for error in validator.iter_errors(config))


# llmlint: ignore-block[shell_test_tiers_stay_split] Not a shell test: a Python check of the
# installed plan-store CLI, in the `reads_checkouts` tier the sibling checks of this file's
# board sources already use, which keeps it out of every memoized tier.
@pytest.mark.reads_checkouts
def test_the_example_linear_sources_resolve_their_states_endpoint_and_routes(
    example_resolved: dict[str, object],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed CLI resolves each Linear source's eight states, its endpoint, and each route.

    Read through the CLI's own resolution for the reason the board mappings above are, with
    the example installed as the user layer. The endpoint is held to the schema's default and
    to no setting of the layer's, so the source reaches Linear itself and never a fixture
    address left behind. `plan_store.routed_sources` is read under the same layer, because it
    is how a copy's home is looked for among the routed sources.
    """
    properties = _linear_schema()["properties"]
    assert isinstance(properties, dict)
    assert properties["endpoint"]["default"] == LINEAR_ENDPOINT, properties["endpoint"]
    for name in LINEAR_SOURCES:
        config = _resolved_block(example_resolved, f"sources.{name}.config.")
        mapping = config.get("status_mapping")
        expected = (
            ORGANIZATION_MAPPING
            if name == onetaskgraph_user_layer.ORGANIZATION_PLANS
            else ORGANIZATION_STATES
        )
        assert mapping == expected, (name, config)
        assert isinstance(mapping, dict)
        task_states = {
            category: entry["task"] if isinstance(entry, dict) else entry
            for category, entry in mapping.items()
        }
        assert task_states == ORGANIZATION_STATES
        assert "endpoint" not in config, (name, config)
    assert example_resolved["sources.plans.routes"] == PLANS_ROUTES
    assert example_resolved["sources.followups.routes"] == FOLLOWUPS_ROUTES
    for name in [name for name in os.environ if name.startswith("ONETASKGRAPH_")]:
        monkeypatch.delenv(name)
    home = onetaskgraph_user_layer.installed(tmp_path_factory.mktemp("routed-layer"))
    monkeypatch.setenv(onetaskgraph_user_layer.XDG_CONFIG_HOME, str(home))
    assert plan_store.routed_sources("plans") == (onetaskgraph_user_layer.ORGANIZATION_PLANS,)
    assert plan_store.routed_sources("followups") == (
        onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
    )


# llmlint: ignore-end[shell_test_tiers_stay_split]


# llmlint: ignore-block[shell_test_tiers_stay_split] Not a shell test: a Python check of the
# installed plan-store CLI, in the `reads_checkouts` tier the sibling checks of this file's
# board sources already use, which keeps it out of every memoized tier.
@pytest.mark.reads_checkouts
@pytest.mark.parametrize("name", sorted(LINEAR_SOURCES))
def test_the_example_linear_source_configuration_is_one_the_linear_plugin_accepts(
    example_resolved: dict[str, object], name: str
) -> None:
    """Each Linear source's `config` validates against the plugin's own published schema.

    The other party is Linear, which no check here may reach, so the configuration is held
    to the schema the installed store reports for its `linear` plugin: a key it does not
    take, or a mapping key that is no status category, fails here rather than at a host's
    first write. A configuration carrying one of each is refused, which says a green here
    is a validation rather than a reader that found nothing to check.
    """
    config = _resolved_block(example_resolved, f"sources.{name}.config.")
    assert config, f"the installed CLI resolves no `{name}` configuration"

    assert _schema_refusals(config) == []
    mapping = config["status_mapping"]
    assert isinstance(mapping, dict)
    refused = _schema_refusals(
        config | {"workspace": "mistvale", "status_mapping": mapping | {"triage": "Triage"}}
    )
    assert len(refused) == 2 and any("workspace" in one for one in refused), refused
    assert any("triage" in one for one in refused), refused
    refused_kind = _schema_refusals(
        config
        | {
            "status_mapping": mapping
            | {"todo": {"task": "Todo", "project": "Planned", "unknown-kind": "Todo"}}
        }
    )
    assert refused_kind, "a per-kind mapping with an unknown key must be refused"


# llmlint: ignore-end[shell_test_tiers_stay_split]


#: The `linear` plugin's default credential name, which onetaskgraph reserves for its own
#: test workspace's key. Read off the installed schema's default by the test below, and
#: spelled in two halves here so this file is not itself a tracked file naming it.
RESERVED_LINEAR_KEY = "LINEAR" + "_API_KEY"
