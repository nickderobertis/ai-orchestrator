"""What `docs/dag-ui.md` says the DAG Observatory can do, against the reader's own contract.

`docs/dag-ui.md` lists the browser's supervising surface — the channel and its reply
composer, `attest`, `stop`, `adopt`, a held `watch`, the unwatched badge, the rendered
reads, the project list and the agents panel — and the refusal a stranger's stop meets.
Every one of those is `onepipeline-ui`'s: this repository builds none of the view and
none of the API, and the list is a copy of a contract somebody else versions. A copy of
a route list is exactly what goes stale in silence, because a route the reader dropped
still reads as a capability from here, and an operator told they can act from a tab
finds out otherwise only by trying.

So each capability the page names is held to a route the adopted reader declares, in
`onepipeline-ui`'s own `docs/contract.md` at `v<config/onepipeline-ui.version>`, and so
is every route the page spells in full. The refusal's status and code are held the same
way, since `tests/dag_ui/test_dag_ui_serving_e2e.py` asserts the served refusal and this
is what says the contract still promises it.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] The
subject is a registered checkout of another repository, which lives outside this
workspace and so outside every `nx.json` input key — hence `reads_checkouts`, the
uncached tier. `tests/test_engine_contracts.py` carries the same directive for the same
reason, and this module borrows its checkout reader rather than opening a second one.
"""

from __future__ import annotations

import re
from typing import NamedTuple

import pytest
from test_engine_contracts import Engine, _pinned_tag, _source

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The reader, at the release this host adopted, read out of its own documentation.
UI_API = Engine("onepipeline-ui", _pinned_tag("onepipeline-ui"), "docs")
CONTRACT = "contract.md"
#: This repository's page that restates the surface.
PAGE = REPO_ROOT / "docs" / "dag-ui.md"


class Capability(NamedTuple):
    """A capability the page names, and the route fragment the contract declares it as."""

    named: str
    route: str


#: Each capability the page names, and the route fragment the contract declares it as.
#: Written as fragments rather than whole routes because the page names capabilities in
#: words and the contract states them as paths: the pairing *is* the claim being held.
CAPABILITIES = (
    Capability("the channel", " /api/v2/runs/{run}/channel "),
    Capability("reply composer", "/channel/reply"),
    Capability("`attest`", "/api/v2/runs/{run}/attest"),
    Capability("`stop`", "/api/v2/runs/{run}/stop"),
    Capability("`adopt`", "/api/v2/runs/{run}/adopt"),
    Capability("`watch`", "/api/v2/runs/{run}/watch"),
    Capability("the unwatched badge", "/api/v2/unwatched"),
    Capability("`status`", "/api/v2/runs/{run}/status"),
    Capability("`results`", "/api/v2/runs/{run}/results"),
    Capability("`goals`", "/api/v2/goals"),
    Capability("`transcript`", "/api/v2/runs/{run}/transcript"),
    Capability("`telemetry`", "/api/v2/runs/{run}/telemetry"),
    Capability("`host`", "/api/v2/host"),
    Capability("landing project list", "/api/v2/projects"),
    Capability("per-project", "/api/v2/projects/{project}"),
    Capability("**Agents**", "/api/v2/runs/{run}/agents"),
    Capability("**Shut down this run**", "/api/v2/runs/{run}/shutdown"),
    Capability("**Shut down all my runs**", " /api/v2/shutdown "),
    Capability("**Shut down the entire host**", " /api/v2/shutdown "),
)


def _routes() -> str:
    """The contract's route block, the fenced list opening with `GET /healthz`.

    Found by its first line rather than by position, and required to be unique, so a
    contract that grew a second list says so rather than being read as whichever came
    first.
    """
    document = _source(UI_API, CONTRACT)
    blocks = [
        block
        for block in re.findall(r"```\n(.*?)\n```", document, re.DOTALL)
        if block.lstrip().startswith("GET /healthz")
    ]
    assert len(blocks) == 1, (
        f"{UI_API.crate}'s {CONTRACT} at {UI_API.ref} holds {len(blocks)} route "
        "blocks opening with `GET /healthz`, not one; the route list this module reads "
        "has moved, and every capability docs/dag-ui.md names has to be re-read against it"
    )
    # Padded so a fragment anchored on spaces can match the first and last route too.
    return f" {blocks[0]} "


def _page() -> str:
    """The page, whitespace-normalized: a reflowed sentence names the same capability."""
    return " ".join(PAGE.read_text(encoding="utf-8").split())


@pytest.mark.parametrize(("named", "route"), CAPABILITIES, ids=[n for n, _ in CAPABILITIES])
def test_each_capability_the_page_names_is_a_route_the_reader_declares(
    named: str, route: str
) -> None:
    """Both halves: the page still names it, and the adopted reader still serves it.

    Held together because either alone is the stale half. A page that dropped the word
    stops telling an operator they can do it; a contract that dropped the route leaves
    the page promising something a tab can no longer perform.
    """
    assert named in _page(), (
        f"docs/dag-ui.md no longer names {named!r}; either the capability left the page or "
        "its wording moved, and this module is holding a sentence that is not there"
    )
    routes = _routes()
    assert route in routes, (
        f"{UI_API.crate} {UI_API.ref} no longer declares {route.strip()!r}, and "
        f"docs/dag-ui.md tells an operator the browser can do {named!r} through it. "
        "Re-read the contract and correct the page in the same change"
    )


def test_every_route_the_page_spells_in_full_is_one_the_reader_declares() -> None:
    """A path written out on the page is a path a reader will type, so it has to exist."""
    routes = _routes()
    declared = set(re.findall(r"/api/v2/[A-Za-z0-9_{}/.-]*[A-Za-z0-9_}]", routes))
    spelled = set(re.findall(r"/api/v2/[A-Za-z0-9_{}/-]*[A-Za-z0-9_}]", _page()))

    missing = sorted(spelled - declared)
    assert not missing, (
        f"docs/dag-ui.md spells {missing}, which {UI_API.crate} {UI_API.ref} does not "
        "declare. A path on the page is one an operator will try"
    )


def test_the_refusal_the_page_describes_is_the_one_the_contract_promises() -> None:
    """`409 not_owner` naming the owner, and a stop an unattributed server cannot make.

    The page and the serving journey both lean on these two sentences, and both are the
    reader's to change; the served refusal is asserted in
    `tests/dag_ui/test_dag_ui_serving_e2e.py`, and this is what says the release still
    promises it rather than happening to answer it.
    """
    document = " ".join(_source(UI_API, CONTRACT).split())

    assert "`409 not_owner`" in document, (
        f"{UI_API.crate} {UI_API.ref} no longer promises `409 not_owner` for a run "
        "another session owns, and docs/dag-ui.md tells an operator that is what a "
        "stranger's stop meets"
    )
    assert "unattributed server owns nothing" in document, (
        f"{UI_API.crate} {UI_API.ref} no longer states that an unattributed server "
        "owns nothing, which is the reason docs/dag-ui.md gives for handing it a session"
    )
    assert "`409 not_owner`" in _page(), "docs/dag-ui.md no longer names the refusal"


def test_the_shutdown_the_page_describes_is_the_one_the_contract_promises() -> None:
    """The scopes, the grace and force defaults, and the report, as the release words them.

    `docs/dag-ui.md` tells an operator what a shutdown from the browser will act on and
    with what defaults, and every one of those is the reader's to change: a release that
    moved the default grace, made force the default, or answered an incomplete shutdown
    as an error would leave the page promising a dialog the view no longer shows.
    """
    document = " ".join(_source(UI_API, CONTRACT).split())
    page = _page()

    for promise, why in (
        ('`{scope: "mine"|"host", grace?: seconds, force?: false}`', "the two listing scopes"),
        ("(600, ten minutes)", "the engine's ten-minute default grace"),
        ("`force` does not lift it", "that force never lifts the ownership refusal"),
        ("`host` proceeds over ownership and names each run's owner", "the host scope"),
        ("answers `200` with that report", "an incomplete shutdown still answering its report"),
        ("**`session_key`**", "the key the confirm dialog tells this session's runs apart by"),
    ):
        assert promise in document, (
            f"{UI_API.crate} {UI_API.ref} no longer states {promise!r} — {why} — and "
            "docs/dag-ui.md describes the browser's shutdown on it"
        )
    for claim in ("ten minutes", "never the default", "other sessions own", "`session_key`"):
        assert claim in page, f"docs/dag-ui.md no longer says {claim!r} about the shutdown"
    assert "**Shutdown incomplete**" in page, "docs/dag-ui.md no longer names the incomplete report"
