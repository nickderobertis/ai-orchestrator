"""The host overlay composes onto the tracked workspaces file by the rule the module states.

`orchestrator/workspaces_overlay.py` is the one place the overlay rule lives — the host
file's ``default`` keys win key by key, its rules replace by identical ``match`` or
append — and `just repos-apply` installs what it prints. These hold the rule and every
refusal at the boundary the host file crosses; `tests/e2e/test_repo_registry_apply_e2e.py`
drives the same composition through the recipe against the real `onevcs`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator.root import REPO_ROOT
from orchestrator.workspaces_overlay import OverlayRefused, compose, main

TRACKED = """version: 1
default:
  pool: 1
  overflow: unlimited
rules:
  - match: {host: github.com, owner: acme, name: app}
    delete: [".logs/"]
  - match: {host: github.com, owner: acme, name: crate}
    maintain: {command: ["cargo", "sweep", "--time", "7"], timeout: 30m}
"""

HOST = """version: 1
default:
  pool: 3
rules:
  - match: {host: github.com, owner: acme, name: crate}
    pool: 2
    overflow: 1
  - match: {host: github.com, owner: acme, name: site}
    pool: 0
"""


def test_the_hosts_default_keys_win_and_its_rules_replace_by_match_or_append() -> None:
    """The composed document is the tracked one with the host's say folded in, in order."""
    composed = compose(TRACKED, HOST)

    assert composed["version"] == 1
    assert composed["default"] == {"pool": 3, "overflow": "unlimited"}
    assert composed["rules"] == [
        {"match": {"host": "github.com", "owner": "acme", "name": "app"}, "delete": [".logs/"]},
        # Replaced whole, in the tracked rule's place: the host said what this
        # identity's rule is, and a `maintain` it did not restate is gone.
        {
            "match": {"host": "github.com", "owner": "acme", "name": "crate"},
            "pool": 2,
            "overflow": 1,
        },
        {"match": {"host": "github.com", "owner": "acme", "name": "site"}, "pool": 0},
    ]


def test_a_host_file_saying_nothing_composes_the_tracked_document_unchanged() -> None:
    """An overlay of only `version: 1`, or of no rules key at all, changes nothing."""
    tracked = yaml.safe_load(TRACKED)

    assert compose(TRACKED, "version: 1\n") == tracked
    assert compose(TRACKED, "version: 1\nrules:\n") == tracked
    assert compose("version: 1\ndefault:\n  pool: 1\n", "version: 1\n") == {
        "version": 1,
        "default": {"pool": 1},
        "rules": [],
    }


@pytest.mark.parametrize(
    ("host", "names"),
    [
        ("version: 2\ndefault:\n  pool: 3\n", "declares version 2, and the tracked one 1"),
        ("- not\n- a mapping\n", "the host workspaces file must be a mapping, not list"),
        ("version: 1\npools: 3\n", "carries ['pools'], which a workspaces file has no key for"),
        ("version: 1\ndefault:\n  slots: 3\n", "`default` carries ['slots']"),
        ("version: 1\ndefault: 3\n", "the host workspaces file's `default` must be a mapping"),
        ("version: 1\nrules: {a: b}\n", "`rules` must be a list, not dict"),
        ("version: 1\nrules:\n  - pool: 2\n", "the host workspaces file's rule 1 names no `match`"),
        (
            "version: 1\nrules:\n  - match: {name: x}\n    every: 7d\n",
            "the host workspaces file's rule 1 carries ['every']",
        ),
        ("version: 1\ndefault: [\n", "the host workspaces file is not valid YAML"),
    ],
    ids=(
        "version",
        "not-a-mapping",
        "unknown-top-key",
        "unknown-default-key",
        "default-not-mapping",
        "rules-not-list",
        "rule-without-match",
        "unknown-rule-key",
        "invalid-yaml",
    ),
)
def test_a_host_file_off_the_schema_is_refused_naming_what_is_wrong(host: str, names: str) -> None:
    """Every refusal names the file and the key, before anything is composed."""
    with pytest.raises(OverlayRefused, match=re.escape(names)):
        compose(TRACKED, host)


def test_the_tracked_file_is_held_to_the_same_shape() -> None:
    """A tracked document off the schema is refused by its own name, not the host's."""
    with pytest.raises(OverlayRefused, match="the tracked workspaces file's rule 1 names no"):
        compose("version: 1\nrules:\n  - pool: 2\n", "version: 1\n")


def test_the_command_prints_the_composed_document_and_says_where_it_came_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`orchestrator-workspaces-overlay TRACKED HOST` is what the recipe installs."""
    tracked = tmp_path / "tracked.yml"
    tracked.write_text(TRACKED, encoding="utf-8")
    host = tmp_path / "host.yml"
    host.write_text(HOST, encoding="utf-8")

    printed = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "orchestrator-workspaces-overlay"),
            str(tracked),
            str(host),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert printed.returncode == 0, printed.stderr
    assert printed.stdout.startswith("# Composed by `just repos-apply`"), printed.stdout
    assert str(host) in printed.stdout.splitlines()[1]
    assert yaml.safe_load(printed.stdout) == compose(TRACKED, HOST)
    # The same answer in this process, which is what the coverage floor measures.
    assert main([str(tracked), str(host)]) == 0
    assert capsys.readouterr().out == printed.stdout


def test_the_command_refuses_a_bad_host_file_and_a_missing_one_at_its_own_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 with the reason on stderr, and nothing on stdout the recipe could install."""
    tracked = tmp_path / "tracked.yml"
    tracked.write_text(TRACKED, encoding="utf-8")
    host = tmp_path / "host.yml"
    host.write_text("version: 2\n", encoding="utf-8")

    assert main([str(tracked), str(host)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "workspaces-overlay: the host workspaces file declares version 2" in captured.err

    assert main([str(tracked), str(tmp_path / "absent.yml")]) == 2
    captured = capsys.readouterr()
    assert "cannot read" in captured.err and "absent.yml" in captured.err

    assert main([str(tracked)]) == 2
    assert "usage: orchestrator-workspaces-overlay" in capsys.readouterr().err
