"""Every party's guidance says whose ruling binds and how a manager's is declined.

The engine records whether a ruling is the manager's own or the user's words relayed, and
renders that attribution beside every ruling a dispatch reads; what this repository adds
is what each party is told that difference means. A party told nothing treats a manager's
ruling exactly as it treats the user's own words, so a wrong one binds every node it
reaches; each statement is therefore held here, by phrase, in the file its reader loads:
the manager's in `AGENTS.md` and the allowlist reason beside its grants, the planner's in
`personas/planner.yaml`, and the monitor's in `personas/orchestrator.yaml`. The worker's
and its judge's are held where they are read, by `tests/test_dispatch_appendix.py` and the
launch journey's check of a real worker's system prompt.
"""

from __future__ import annotations

import pytest

from orchestrator.root import REPO_ROOT

#: Each statement, as the file its reader loads states it.
STATEMENTS = (
    (
        "AGENTS.md",
        "the manager's replies are its own ruling by default",
        "`just channel-reply` and `just human-items` record your own ruling by default, "
        "which a dispatch may decline with its reason",
    ),
    (
        "AGENTS.md",
        "`--as user` relays only the user's own words",
        "`--as user` marks the user's own words, which bind, and is used only to relay them",
    ),
    (
        "AGENTS.md",
        "an answer from chat is recorded as the user's",
        '`just human-items answer <item> --choice <id>` and/or `--text "<their words>"` '
        "`--as user`",
    ),
    (
        "AGENTS.md",
        "a declined ruling is read like any surface and escalated where the goal is at stake",
        "A `declined-ruling` surface is read like any planner surface: weigh its reason, and "
        "raise it as a question item where the user's goal is at stake",
    ),
    (
        "AGENTS.md",
        "the authority the base config states",
        "a user ruling binds absolutely, while a manager ruling may be declined, only through "
        "`$ORCHESTRATOR_DECLINE_RULING` with its reason, never silently",
    ),
    (
        "AGENTS.md",
        "the test that holds that clause",
        "`tests/e2e/launch_monitor_observer/test_orchestrate_launch_e2e.py` holds that on a "
        "real dispatched worker's system prompt",
    ),
    (
        "config/manager-allowlist.toml",
        "the grant reason's account of `--as user`",
        "Each records the manager's own ruling unless `--as user`, which these `:*` grants "
        "already admit, marks it as the user's words relayed",
    ),
    (
        "personas/planner.yaml",
        "a manager's answer is declined through the route, never silently",
        "a reason you surface to the manager through `$ORCHESTRATOR_DECLINE_RULING`, never "
        "silently, while a ruling in the user's own words binds",
    ),
    (
        "personas/planner.yaml",
        "an answer arrives attributed",
        "Every answer arrives attributed: the reply the ask prints names its `provenance`, "
        "`manager` or `user`",
    ),
    (
        "personas/planner.yaml",
        "the decline command the planner runs",
        '| "$ORCHESTRATOR_DECLINE_RULING" --ruling "<the answer\'s ruling reference>"',
    ),
    (
        "personas/planner.yaml",
        "a user's answer binds and cannot be declined",
        "An answer attributed to the user binds, carries no reference, and the command "
        "refuses to decline it",
    ),
    (
        "personas/orchestrator.yaml",
        "the monitor reads provenance off the journal",
        "Tell the two apart by that key and never by how the text is worded",
    ),
    (
        "personas/orchestrator.yaml",
        "the monitor never sets a user ruling aside",
        "never ground a finding or an edit in setting one aside",
    ),
    (
        "personas/orchestrator.yaml",
        "a declined ruling is the manager's to read",
        "surface — the manager's to read, and to raise as a question item where the user's "
        "goal is at stake",
    ),
)


@pytest.mark.parametrize(
    ("relative", "what", "phrase"),
    STATEMENTS,
    ids=[f"{relative}: {what}" for relative, what, _ in STATEMENTS],
)
def test_each_party_is_told_whose_ruling_binds(relative: str, what: str, phrase: str) -> None:
    said = " ".join((REPO_ROOT / relative).read_text(encoding="utf-8").split())
    assert " ".join(phrase.split()) in said, f"{relative} no longer states {what}: {phrase!r}"
