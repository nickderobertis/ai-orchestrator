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
import subprocess
from pathlib import Path

import pytest
from plan_sources import read_default_sources, read_plan_sources, shared_roots

from orchestrator import plan_review
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


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("plugin", "github-projects"),
        ("owner", "nickderobertis"),
        ("project_number", "2"),
        ("repository", "nickderobertis/ai-orchestrator"),
        ("token_env", "GH_PROJECTS_TOKEN"),
    ],
)
def test_the_board_source_is_left_exactly_as_it_was(setting: str, expected: str) -> None:
    """`plans` keeps every value it carried before, during and after the retreat.

    The retreat to a local Markdown store was designed to be undone by subtraction, and
    that only worked because `plans` was left alone throughout: it was deleted rather
    than repointed back. This survives the retirement because the reason outlives it — a
    live run's settlements are projected back to the project it was launched from, so
    repointing this source moves a running plan's store out from under it.
    """
    board = read_plan_sources(_configuration())["plans"]
    actual = board.plugin if setting == "plugin" else board.settings.get(setting)
    assert actual == expected, (
        f"onetaskgraph.yaml's `plans` source names {setting} {actual!r} where it carried "
        f"{expected!r}; this is the source this repository plans against"
    )


def _resolved_configuration() -> dict[str, object]:
    """Every setting the installed CLI resolves out of this checkout's own file."""
    result = subprocess.run(
        [str(Path.home() / ".local/bin/onetaskgraph"), "--json", "config", "show"],
        cwd=REPO_ROOT,
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
    """
    resolved = _resolved_configuration()
    text = (REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8")
    read_here = read_plan_sources(text)
    assert {f"sources.{name}.plugin": source.plugin for name, source in read_here.items()} == {
        key: value for key, value in resolved.items() if key.endswith(".plugin")
    }
    assert {
        f"sources.{name}.config.root": source.root
        for name, source in read_here.items()
        if source.root is not None
    } == {key: value for key, value in resolved.items() if key.endswith(".config.root")}
    assert read_default_sources(text) == resolved["default_sources"]


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
