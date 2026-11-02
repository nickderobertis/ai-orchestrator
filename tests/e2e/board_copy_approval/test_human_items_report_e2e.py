"""`just human-items report`, as a manager reads it for an update: short, and about its session.

Several manager sessions share one host's item store, so the report a manager relays is the
engine's default view: each item one compact entry, its body cut to one line, holding only
what the asking session or its runs raised and what holds its work from elsewhere — a host
hold, or a hold on a plan or run it launched — with a line counting the rest and naming
`--all`. `--full` prints every body whole and `--all` the whole host. These journeys raise
items as two sessions through `just human-items raise` and read the report back through the
same recipe as each session, against the installed engine and a runs root of their own.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_board_copy_design_approval_e2e import _recipe
from test_shared_human_item_defaults_e2e import _home

#: The asking manager, and another manager sharing the host.
ASKING = "report-asking-manager"
OTHER = "report-other-manager"

#: The asking session's own item, whose body runs past one line.
OWN = {
    "title": "Rotate the deploy key",
    "body": "Rotate it before Friday.\nThe old key leaks into three CI logs and has to go.",
    "priority": "medium",
    "block": {"scope": "nothing"},
}
#: Another session's item that holds nothing of the asking session's.
UNRELATED = {
    "title": "Renew the certificate",
    "body": "Renew it before it lapses.",
    "priority": "low",
    "block": {"scope": "nothing"},
}
#: Another session's hold on the whole host, which holds the asking session's work too.
HOLD = {
    "title": "Restore the build service",
    "body": "Restart the unavailable build service.",
    "priority": "urgent",
    "block": {"scope": "host"},
}


def _raised(
    environment: dict[str, str], tmp_path: Path, session: str, item: dict[str, object]
) -> None:
    """Raise ``item`` as ``session`` through the recipe."""
    request = tmp_path / f"{item['title']}.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dedup_key": f"report:{item['title']}",
                "kind": "task",
                "owner": {"session": session, "run": None, "plan": None},
                "approval": None,
                **item,
            }
        ),
        encoding="utf-8",
    )
    raised = _recipe(
        {**environment, "ONEPIPELINE_LAUNCHER_SESSION": session},
        *("human-items", "raise", "--input", str(request)),
    )
    assert raised.returncode == 0, raised.stdout + raised.stderr


def _report(environment: dict[str, str], *flags: str) -> str:
    """The report the asking session reads, with ``flags``."""
    read = _recipe(
        {**environment, "ONEPIPELINE_LAUNCHER_SESSION": ASKING}, "human-items", "report", *flags
    )
    assert read.returncode == 0, read.stdout + read.stderr
    return read.stdout


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] This journey is not expensive: about 0.2 s of six recipe calls to the installed engine over a temporary item store, reaching no external service, and it shares this tier's `_recipe` and `_home` helpers with the human-item journeys already here, so a project of its own would buy no time and duplicate that key.  # noqa: E501 - a directive is one line
def test_the_default_report_is_short_and_scoped_to_the_asking_session(tmp_path: Path) -> None:
    """With no flags: the session's own item and the host hold, one body line each, the other
    session's unrelated item counted on a line naming `--all`; `--full` restores every body
    and `--all` lists the whole host."""
    environment = _home(tmp_path, "reporting-host")
    _raised(environment, tmp_path, ASKING, OWN)
    _raised(environment, tmp_path, OTHER, UNRELATED)
    _raised(environment, tmp_path, OTHER, HOLD)

    brief = _report(environment)
    full = _report(environment, "--full")
    whole = _report(environment, "--all")

    assert "2 listed, 1 blocking" in brief, brief
    assert OWN["title"] in brief and HOLD["title"] in brief, brief
    assert "blocks all work on this host" in brief, brief
    assert UNRELATED["title"] not in brief, brief
    assert "1 other open item on this host (--all lists every item)" in brief, brief
    # Each body is cut to its first line, and says so.
    assert "Rotate it before Friday.…" in brief, brief
    assert "three CI logs" not in brief, brief

    assert "three CI logs and has to go." in full, full
    assert UNRELATED["title"] not in full, full

    assert "3 listed, 1 blocking" in whole and UNRELATED["title"] in whole, whole
    assert "three CI logs" not in whole, whole
    complete = _report(environment, "--full", "--all")
    assert UNRELATED["title"] in complete and "three CI logs and has to go." in complete
