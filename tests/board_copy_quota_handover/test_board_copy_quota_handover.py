"""What a board copy and its approval spend is budgeted in the plan store that spends it.

`just copy-plan` and `just approve-design` spend the board token's GraphQL allowance only
through the plan store's own calls — the copy its `project copy` and `document copy`, the
approval its `project show`, `task list`, `document list` and guarded `document metadata set`
— and the store measures exactly those calls in its own suite, with write screening on under a
synthetic policy. So this host registers no copy points or writes budget and no approval
points budget of its own: `tests/e2e/board_copy_approval/budgets.yaml` names the store's. The
store also budgets the simulated seconds of the same three copies, which this host's own
seconds budgets re-take on its real recipes, and its Python SDK's import CPU, which every
`orchestrator` module importing the SDK pays.

That hand-over holds only while the release this host adopts carries those budgets. These
checks read the store's budget files at the tag `config/onetaskgraph.version` names, from the
registered onetaskgraph checkout and fetching nothing, and fail on a pin naming a release that
lacks any of them, registers one at another threshold, or accepts an overrun of it. The same
checkout carries the measured placement delays its loopback board replays, which the host's
board double replays too.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from github_board import MEASURED_PLACEMENT_MILLIS
from sibling_facts import registered_checkouts

from orchestrator.root import REPO_ROOT

#: The plan store's repository identity, as `config/onevcs.checkouts` registers it.
ONETASKGRAPH = "github.com/nickderobertis/onetaskgraph"
#: Where the store registers its GitHub board journeys' budgets.
STORE_BUDGETS = "crates/onetaskgraph-github-projects-e2e/budgets.yaml"
#: Where the store registers its Python SDK's budgets.
SDK_BUDGETS = "sdks/python/budgets.yaml"
#: Where the store's loopback board keeps the placement delays it replays.
STORE_FIXTURES = "crates/onetaskgraph-e2e-support/src/fixtures.rs"
#: The host's budgets file for the board-copy journeys, which names the store's budgets.
HOST_BUDGETS = REPO_ROOT / "tests" / "e2e" / "board_copy_approval" / "budgets.yaml"


class Handed(NamedTuple):
    """One store budget this host relies on in place of a budget of its own."""

    id: str
    threshold: float
    unit: str
    #: The store's budgets file that registers it, at the pinned tag.
    path: str = STORE_BUDGETS

    @property
    def quota(self) -> bool:
        """Whether it budgets the board token's allowance, which this host budgets nowhere."""
        return self.unit in ("points", "writes")


#: The store's budgets for what these recipes spend, at the bars this host was promised: a
#: first copy, a re-copy after one task's body changed and an unchanged re-copy of the 10-item
#: plan, in GraphQL points and in simulated seconds; a first copy's content creations; an
#: approval's points; and the CPU seconds importing the store's Python SDK costs.
HANDED_OVER = (
    Handed("github-plan-copy-points-first", 12, "points"),
    Handed("github-plan-copy-points-changed", 4, "points"),
    Handed("github-plan-copy-points-unchanged", 3, "points"),
    Handed("github-plan-copy-content-creations", 40, "writes"),
    Handed("github-approval-calls-points", 5, "points"),
    Handed("github-plan-copy-seconds-first", 32, "seconds"),
    Handed("github-plan-copy-seconds-changed", 8, "seconds"),
    Handed("github-plan-copy-seconds-unchanged", 7, "seconds"),
    Handed("sdk-import-cpu-seconds", 0.35, "seconds", SDK_BUDGETS),
)

#: The seconds budgets this host registers for the same recipes' real elapsed time.
HOST_REGISTERED = [
    "plan-approval-seconds",
    "plan-copy-seconds-first",
    "plan-copy-seconds-changed",
    "plan-copy-seconds-unchanged",
]


def _at_the_pin(checkout: Path, path: str, pin: str | None = None) -> str:
    """``path`` as the release ``pin`` names carries it: by default, the adopted one."""
    if pin is None:
        pin = (REPO_ROOT / "config" / "onetaskgraph.version").read_text(encoding="utf-8").strip()
    shown = subprocess.run(
        ["git", "-C", str(checkout), "show", f"v{pin}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, (
        f"the onetaskgraph checkout {checkout} cannot read {path} at v{pin}, so it sits behind "
        f"its origin or the pin names no release: {shown.stderr}"
    )
    return shown.stdout


def _store_checkout() -> Path:
    checkouts = registered_checkouts()
    assert ONETASKGRAPH in checkouts, (
        f"no registered checkout of {ONETASKGRAPH} on this host: {sorted(checkouts)}"
    )
    return checkouts[ONETASKGRAPH]


def handover_gaps(registered: str, path: str = STORE_BUDGETS) -> list[str]:
    """What the store's budget file ``path``, reading ``registered``, lacks of its share of
    :data:`HANDED_OVER`, by budget."""
    document = yaml.safe_load(registered)
    entries = {
        entry["id"]: entry
        for entry in (document or {}).get("budgets", [])
        if isinstance(entry, dict) and "id" in entry
    }
    gaps: list[str] = []
    for handed in (handed for handed in HANDED_OVER if handed.path == path):
        entry = entries.get(handed.id)
        if entry is None:
            gaps.append(f"{handed.id} is not registered")
            continue
        if (entry.get("threshold"), entry.get("unit")) != (handed.threshold, handed.unit):
            gaps.append(
                f"{handed.id} is registered at {entry.get('threshold')} {entry.get('unit')}, "
                f"not {handed.threshold:g} {handed.unit}"
            )
        if entry.get("direction") != "max":
            gaps.append(f"{handed.id} is not a ceiling: direction {entry.get('direction')}")
        if "accepted-overrun" in (entry.get("labels") or []):
            gaps.append(f"{handed.id} accepts an overrun, so over it would only warn")
    return gaps


def _release_gaps(checkout: Path, pin: str | None = None) -> list[str]:
    """What the release ``pin`` names lacks of :data:`HANDED_OVER`, across its budget files.

    A file the release does not carry at all lacks every budget it was to register.
    """
    gaps: list[str] = []
    for path in dict.fromkeys(handed.path for handed in HANDED_OVER):
        try:
            registered = _at_the_pin(checkout, path, pin)
        except AssertionError:
            registered = ""
        gaps += [f"{path}: {gap}" for gap in handover_gaps(registered, path)]
    return gaps


@pytest.mark.reads_checkouts
def test_the_adopted_store_registers_the_budgets_this_host_handed_its_quota_to() -> None:
    """The pinned release budgets the copies, the approval and the SDK's import at the bars."""
    gaps = _release_gaps(_store_checkout())
    assert gaps == [], (
        "this host registers no copy points, writes or approval points budget because the "
        f"adopted onetaskgraph does, and the release lacks what it was handed: {gaps}"
    )


#: A release of the store before its SDK registered `sdk-import-cpu-seconds`.
BEFORE_THE_SDK_BUDGET = "0.12.5"


@pytest.mark.reads_checkouts
def test_the_registration_check_refuses_a_release_missing_a_handed_budget() -> None:
    """A pin naming a release that lacks one of the budgets fails, naming it."""
    gaps = _release_gaps(_store_checkout(), BEFORE_THE_SDK_BUDGET)
    assert f"{SDK_BUDGETS}: sdk-import-cpu-seconds is not registered" in gaps, gaps


def test_the_handover_check_refuses_a_release_missing_or_loosening_a_budget() -> None:
    """A budget file lacking one, raising one or accepting an overrun of one is named."""
    whole = yaml.safe_dump(
        {
            "budgets": [
                {"id": handed.id, "threshold": handed.threshold, "unit": handed.unit}
                | {"direction": "max", "labels": ["github"]}
                for handed in HANDED_OVER
            ]
        }
    )
    assert handover_gaps(whole) == []
    first, changed, *_ = HANDED_OVER
    missing = whole.replace(f"id: {first.id}\n", "id: github-plan-copy-points-renamed\n")
    assert handover_gaps(missing) == [f"{first.id} is not registered"]
    loosened = whole.replace("threshold: 4\n", "threshold: 6\n")
    assert handover_gaps(loosened) == [f"{changed.id} is registered at 6 points, not 4 points"]
    accepted = whole.replace("- github\n", "- accepted-overrun\n", 1)
    assert handover_gaps(accepted) == [f"{first.id} accepts an overrun, so over it would only warn"]


def test_the_host_budgets_file_names_the_store_budgets_and_registers_none_of_its_own() -> None:
    """No host budget measures the copy's or the approval's quota; the header names the store's.

    The host registers its recipes' seconds alone, each `host` and `host-variable`.
    """
    text = HOST_BUDGETS.read_text(encoding="utf-8")
    entries = yaml.safe_load(text)["budgets"]
    assert [entry["id"] for entry in entries] == HOST_REGISTERED, entries
    assert all(entry["labels"] == ["host", "host-variable"] for entry in entries), entries
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    quota = [handed.id for handed in HANDED_OVER if handed.quota]
    assert [handed for handed in quota if handed not in header] == [], header


@pytest.mark.reads_checkouts
def test_the_board_double_replays_the_placement_delays_the_store_measured_with() -> None:
    """The host's double places a created issue after the delays the store's loopback replays."""
    source = _at_the_pin(_store_checkout(), STORE_FIXTURES)
    declared = re.search(
        r"pub const MEASURED_PLACEMENT_MILLIS: \[u64; (\d+)\] = \[([^\]]*)\]", source
    )
    assert declared is not None, f"{STORE_FIXTURES} declares no MEASURED_PLACEMENT_MILLIS"
    samples = tuple(int(millis) for millis in re.findall(r"\d+", declared.group(2)))
    assert len(samples) == int(declared.group(1)), declared.group(0)
    assert samples == MEASURED_PLACEMENT_MILLIS, (samples, MEASURED_PLACEMENT_MILLIS)
