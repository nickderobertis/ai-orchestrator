"""The board double's field-setup input, held to GitHub's complete published schema.

`tests/github_board.py` answers the store's `updateProjectV2Field` with what GitHub would do,
and refuses an input GitHub refuses — the `projectId` onetaskgraph 0.12.13 sent, which live
GitHub rejected while the double of that release accepted it, is how a defect shipped. So
every input shape the double declares it accepts (`STATUS_UPDATE_INPUT_KEYS`,
`KEPT_OPTION_KEYS`, `ADDED_OPTION_KEYS`) is put, beside each one-key departure from it, to
both the double and GitHub's static validation of the store's own document, and nothing the
double accepts may be one GitHub refuses. GitHub's side is onetaskgraph's own validator —
graphql-js from the tarball it pins, over the complete published schema it vendors — and the
document is the store's `STATUS_OPTIONS_UPDATE`, all read at the tag
`config/onetaskgraph.version` pins, so a store release that changes the request or the
schema it was checked against is what this reconciles.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] The
subject is a release of another repository read from its registered checkout, outside every
`nx.json` input key — hence `reads_checkouts`, the uncached tier — and
`tests/test_human_items_guidance.py` borrows the same checkout reader for the same reason.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from github_board import (
    ADDED_OPTION_KEYS,
    KEPT_OPTION_KEYS,
    STATUS_OPTIONS,
    STATUS_UPDATE_INPUT_KEYS,
    _Board,
    _BoardField,
)
from test_engine_contracts import Engine, _checkout, _pinned_tag

pytestmark = pytest.mark.reads_checkouts

#: The store's GitHub Projects plugin at the release this host pins.
STORE = Engine("onetaskgraph", _pinned_tag("onetaskgraph"), "crates/onetaskgraph-github-projects")
#: Its validator, the graphql-js tarball beside it, and the schema it validates against.
VALIDATOR = "tests/github_schema/validate.mjs"
GRAPHQL = "tests/github_schema/graphql-17.0.2.tgz"
SCHEMA = "tests/fixtures/github-schema.docs.graphql"
#: Where the store declares the document its field setup sends.
DOCUMENT = re.compile(r'pub const STATUS_OPTIONS_UPDATE: &str = r#"(?P<query>.*?)"#;')

#: A value for every key the double's shapes name.
VALUES: dict[str, object] = {
    "fieldId": str(_BoardField.STATUS),
    "id": STATUS_OPTIONS[0].id,
    "name": STATUS_OPTIONS[0].name,
    "color": "GRAY",
    "description": "",
}


def _shown(relative: str, into: Path) -> Path:
    """One file of the store's plugin at the pinned tag, written under ``into``."""
    target = into / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    read = subprocess.run(
        ["git", "-C", str(_checkout(STORE)), "show", f"{STORE.ref}:{STORE.source_root}/{relative}"],
        capture_output=True,
        check=False,
    )
    assert read.returncode == 0, (
        f"the onetaskgraph checkout cannot show {relative} at {STORE.ref}: "
        f"{read.stderr.decode().strip()}. Fetch that tag"
    )
    target.write_bytes(read.stdout)
    return target


def _github_refusals(inputs: list[dict[str, object]], tmp_path: Path) -> list[list[str]]:
    """GitHub's validation errors for the store's document sent with each input, in order."""
    source = _shown("src/lib.rs", tmp_path).read_text(encoding="utf-8")
    found = DOCUMENT.search(source)
    assert found is not None, f"onetaskgraph {STORE.ref} declares no STATUS_OPTIONS_UPDATE"
    validator, _ = _shown(VALIDATOR, tmp_path), _shown(GRAPHQL, tmp_path)
    schema = _shown(SCHEMA, tmp_path)
    requests = "".join(
        json.dumps(
            {
                "query": found["query"],
                "operationName": None,
                "variables": {"input": one},
                "coerce": True,
            }
        )
        + "\n"
        for one in inputs
    )
    ran = subprocess.run(
        ["bun", str(validator), str(schema)],
        input=requests,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    answers = [json.loads(line) for line in ran.stdout.splitlines()]
    assert answers and answers[0].get("ready") is True, ran.stdout + ran.stderr
    replies = answers[1:]
    assert len(replies) == len(inputs) and all("errors" in one for one in replies), replies
    return [[str(error["message"]) for error in one["errors"]] for one in replies]


def _double_accepts(payload: dict[str, object]) -> bool:
    board = _Board()
    try:
        board.update_status_options({"input": payload})
    except ValueError:
        return False
    return True


def _shaped(keys: frozenset[str], **extra: object) -> dict[str, object]:
    return {key: VALUES[key] for key in sorted(keys)} | extra


def _probes() -> dict[str, dict[str, object]]:
    """Each declared shape, every one-key removal from it and an unknown key added to it.

    Each probe sends the whole option list, as the store's setup does — GitHub replaces the
    field's options with it, so a list leaving out an option an item holds is one the double
    refuses for that reason alone — and varies only the one option it probes.
    """
    preserved = [option.rendered_in_full() for option in STATUS_OPTIONS[1:]]
    kept_first = STATUS_OPTIONS[0].rendered_in_full()
    options = {"kept": KEPT_OPTION_KEYS, "added": ADDED_OPTION_KEYS}

    def with_option(kind: str, option: dict[str, object]) -> dict[str, object]:
        listed = [option, *preserved] if kind == "kept" else [kept_first, *preserved, option]
        return _shaped(STATUS_UPDATE_INPUT_KEYS - {"singleSelectOptions"}) | {
            "singleSelectOptions": listed
        }

    def probed(kind: str, keys: frozenset[str], **extra: object) -> dict[str, object]:
        option = _shaped(keys, **extra)
        if kind == "added" and "name" in option:
            option["name"] = "Added"
        return with_option(kind, option)

    probes = {f"{name} option": probed(name, keys) for name, keys in options.items()}
    for name, keys in options.items():
        for key in sorted(keys):
            probes[f"{name} option without {key}"] = probed(name, keys - {key})
        probes[f"{name} option with an unknown key"] = probed(name, keys, unknown="x")
    whole = probes["kept option"]
    for key in sorted(STATUS_UPDATE_INPUT_KEYS):
        probes[f"input without {key}"] = {k: v for k, v in whole.items() if k != key}
    probes["input with the projectId 0.12.13 sent"] = whole | {"projectId": "PVT_board"}
    return probes


def test_nothing_the_double_accepts_of_a_status_update_is_an_input_github_refuses(
    tmp_path: Path,
) -> None:
    probes = _probes()
    refusals = _github_refusals(list(probes.values()), tmp_path)
    accepted_refused = [
        (name, errors)
        for (name, payload), errors in zip(probes.items(), refusals, strict=True)
        if _double_accepts(payload) and errors
    ]
    assert accepted_refused == [], accepted_refused
    by_name = dict(zip(probes, refusals, strict=True))
    # The declared shapes are ones the double and GitHub both take, and the input the store
    # sent before 0.13.1 is one both refuse — the regression this reconciliation exists for.
    for taken in ("kept option", "added option"):
        assert _double_accepts(probes[taken]) and by_name[taken] == [], (taken, by_name[taken])
    regression = "input with the projectId 0.12.13 sent"
    assert not _double_accepts(probes[regression]), regression
    assert any("projectId" in error for error in by_name[regression]), by_name[regression]
