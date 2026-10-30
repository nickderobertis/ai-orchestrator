"""What `docs/dag-ui.md` says the DAG Observatory can do, against the reader's own contract.

`docs/dag-ui.md` lists the browser's supervising surface — the project-centric sidebar,
the channel and its reply composer, `attest`, the per-run `stop`, the Host page with its
unwatched report and its one host-wide shutdown, and the rendered reads — the refusal a
stranger's stop meets, and that those controls are the whole of what the view acts through.
Every one of those is `onepipeline-ui`'s: this repository builds none of the view and
none of the API, and the list is a copy of a contract somebody else versions. A copy of
a route list is exactly what goes stale in silence, because a route the reader dropped
still reads as a capability from here, and an operator told they can act from a tab
finds out otherwise only by trying.

So each capability the page names is held to a route the adopted reader declares, in
`onepipeline-ui`'s own `docs/contract.md` at `v<config/onepipeline-ui.version>`, and so
is every route the page spells in full. The page's statement that those controls are the
whole of it is held to the same contract's route list and to the run view's own tabs in the
view's source at that tag.
The refusal's status and code are held the same
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
#: The view's own application shell at that release, where its run tabs are declared.
UI_VIEW = Engine("onepipeline-ui", _pinned_tag("onepipeline-ui"), "apps/dag-ui/src/app")
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
    Capability("The navigation is project-centric", " /api/v2/projects "),
    Capability("the channel", " /api/v2/runs/{run}/channel "),
    Capability("reply composer", "/channel/reply"),
    Capability("`attest`", "/api/v2/runs/{run}/attest"),
    Capability("per-run **Stop**", "/api/v2/runs/{run}/stop"),
    Capability("the unwatched route", "/api/v2/unwatched"),
    Capability("`status`", "/api/v2/runs/{run}/status"),
    Capability("`results`", "/api/v2/runs/{run}/results"),
    Capability("`goals`", "/api/v2/goals"),
    Capability("`transcript`", "/api/v2/runs/{run}/transcript"),
    Capability("`telemetry`", "/api/v2/runs/{run}/telemetry"),
    Capability("`host`", "/api/v2/host"),
    Capability("**Host** is a page of its own", "/api/v2/host"),
    Capability("**Shut down the entire host**", " /api/v2/shutdown "),
    Capability("**Action items** is another page", " /api/human-items "),
)

#: The page's statement that the controls it lists are all the view acts through.
WHOLE = "Those controls are the whole of what the view acts through."
#: Every write route the contract declares, against the words the page lists it in.
CONTROLS = {
    "POST /api/v2/runs/{run}/stop": "its per-run **Stop**",
    "POST /api/v2/runs/{run}/channel/next": "its channel's **Next**",
    "POST /api/v2/runs/{run}/channel/reply": "reply,",
    "POST /api/v2/runs/{run}/channel/surface": "raised surface",
    "POST /api/v2/runs/{run}/attest": "raised surface and `attest`, a human",
    "POST /api/v2/shutdown": "the host by its one host-wide shutdown",
    "POST /api/human-items": "a human item by its raise,",
    "POST /api/human-items/{id}/answer": "answer,",
    "POST /api/human-items/{id}/complete": "completion",
    "POST /api/human-items/{id}/dismiss": "and dismissal",
}
#: The write routes a tab reads its state through, which act on no run and no host.
SUBSCRIPTION_ROUTES = {
    "POST /api/v2/subscriptions",
    "DELETE /api/v2/subscriptions/{subscription}",
}

#: The run view's tabs, as the page names them, against the view's own declaration.
RUN_TABS = {"graph": "**Graph**", "overall": "**Overall**", "channel": "**Channel**"}


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


def test_the_page_and_contract_keep_browser_driving_removed() -> None:
    """The removal remains documented beside the surviving browser controls."""
    document = " ".join(_source(UI_API, CONTRACT).split())
    assert "The binary no longer drives runs" in document
    assert "404 removed_route" in document
    page = _page()
    assert (
        "Watching, adopting and shutting down one run or one session's runs are not the "
        "browser's" in page
    )
    assert "The browser adopts nothing" in page
    assert "The browser holds no watch on any run" in page
    assert "every other scope" in page and "`400 removed_shutdown_scope`" in page
    for route in ("/adopt", "/watch", "/runs/{run}/shutdown"):
        assert route not in _routes()


def test_the_page_says_human_items_are_unavailable_without_a_sample() -> None:
    """The page tells an operator the **Action items** page is empty here, and why.

    The contract serves those routes only over `--human-items-sample`, which this host
    never passes, so the page's claim is held to the contract still saying so.
    """
    document = " ".join(_source(UI_API, CONTRACT).split())
    assert "--human-items-sample" in document
    assert "`503 human_items_unavailable`" in document
    page = _page()
    assert "`--human-items-sample`" in page
    assert "`503 human_items_unavailable`" in page


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


def test_the_controls_the_page_lists_are_every_write_route_and_tab_the_release_declares() -> None:
    """The page's controls are the whole of it: every write route, and every run tab.

    The page says once that its listed controls are all the view acts through, and an
    operator reads anything else as "do this from a terminal". The view reaches the
    engine only through the reader's routes, so a control anywhere in it — a new tab or
    a new button inside an existing one — is a route other than a `GET`. So the
    contract's complete set of those routes, less the subscription a tab reads its
    state through, is compared with the set the page lists as controls, in both
    directions; a removed route answers `404 removed_route`; and the run view's own
    tabs, read off the view's application shell at the pinned tag, are exactly the
    three the page names.
    """
    page = _page()
    assert WHOLE in page, f"docs/dag-ui.md no longer says {WHOLE!r}"
    for named in CONTROLS.values():
        assert named in page, f"docs/dag-ui.md no longer lists the control {named!r}"
    writes = {
        f"{method} {path}"
        for method, path in re.findall(r"^(POST|PUT|PATCH|DELETE) (\S+)", _routes(), re.MULTILINE)
    }
    controls = writes - SUBSCRIPTION_ROUTES
    assert controls == set(CONTROLS), (
        f"{UI_API.crate} {UI_API.ref} declares the write routes {sorted(controls)} beside "
        f"its subscription, and docs/dag-ui.md lists controls for {sorted(CONTROLS)}; a "
        "route only one side names is a control the page omits or one the view no longer "
        "offers, so correct the page in the same change"
    )
    assert writes >= SUBSCRIPTION_ROUTES, (
        f"{UI_API.crate} {UI_API.ref} no longer declares {sorted(SUBSCRIPTION_ROUTES - writes)}; "
        "re-read how a tab subscribes before treating every other write route as a control"
    )
    assert "`404 removed_route`" in " ".join(_source(UI_API, CONTRACT).split())
    declared = set(re.findall(r'<TabsTrigger value="([a-z-]+)">', _source(UI_VIEW, "App.tsx")))
    assert declared == set(RUN_TABS), (
        f"{UI_VIEW.crate} {UI_VIEW.ref}'s run view declares the tabs {sorted(declared)}, and "
        f"docs/dag-ui.md names {sorted(RUN_TABS)}; correct the page in the same change"
    )
    for named in RUN_TABS.values():
        assert named in page, f"docs/dag-ui.md no longer names the {named} tab"


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
    """The host scope alone, the grace and force defaults, and the report, as the release
    words them.

    `docs/dag-ui.md` tells an operator what a shutdown from the browser will act on and
    with what defaults, and every one of those is the reader's to change: a release that
    restored a narrower scope, made force the default, or answered an incomplete shutdown
    as an error would leave the page promising a control the view no longer offers.
    """
    document = " ".join(_source(UI_API, CONTRACT).split())
    # The view's own page, which states what its dialog defaults to and names.
    view = " ".join(_source(UI_API, "dag-ui.md").split())
    page = _page()

    for promise, why, source in (
        ('`{scope: "host", grace?: seconds, force?: false}`', "the host scope alone", document),
        ("`400 removed_shutdown_scope`", "the refusal of every other scope", document),
        ("Host scope proceeds over ownership", "the host scope acting over owners", document),
        ("still answers `200` with its report", "an incomplete shutdown's report", document),
        ("optional `session_key`", "the key the Host page reads", document),
        ("The grace defaults to ten minutes", "the dialog's default grace", view),
        ("Force defaults off", "force never the default", view),
        ("reading the listing to its end", "the dialog naming every run it acts on", view),
    ):
        assert promise in source, (
            f"{UI_API.crate} {UI_API.ref} no longer states {promise!r} — {why} — and "
            "docs/dag-ui.md describes the browser's shutdown on it"
        )
    for claim in (
        "ten minutes",
        "never the default",
        "other sessions own",
        "`session_key`",
        "`400 removed_shutdown_scope`",
    ):
        assert claim in page, f"docs/dag-ui.md no longer says {claim!r} about the shutdown"
    assert "**Shutdown incomplete**" in page, "docs/dag-ui.md no longer names the incomplete report"
