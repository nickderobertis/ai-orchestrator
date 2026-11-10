"""Reconcile release-change discovery with producers' live release tooling."""

import tomllib

import pytest
from registered_checkouts import discover_checkouts
from sibling_facts import RELEASE_RULE

from orchestrator.host_installs import AWAITING_FIRST_ADOPTION
from orchestrator.root import REPO_ROOT

RELEASES = REPO_ROOT / "config" / "onevcs.releases.yml"


@pytest.mark.reads_checkouts
def test_release_change_rules_match_producers_release_tooling() -> None:
    """Only release-plz producers have a head glob, using their declared prefix.

    A producer awaiting its first adoption may carry its glob before its checkout carries
    the release tooling its bootstrap lands, as it may carry its default target before its
    `release-targets.toml`; once its tooling is there, the prefix is held to it.
    """
    discovered = discover_checkouts()
    assert not discovered.unreadable, discovered.unreadable
    for rule in RELEASE_RULE.finditer(RELEASES.read_text(encoding="utf-8")):
        identity = f"{rule['host']}/{rule['owner']}/{rule['name']}"
        checkouts = discovered.held.get(identity, [])
        if not checkouts:
            continue
        tooling = checkouts[0] / "release-plz.toml"
        fields = rule["fields"]
        if tooling.is_file():
            workspace = tomllib.loads(tooling.read_text(encoding="utf-8")).get("workspace", {})
            prefix = workspace.get("pr_branch_prefix", "release-plz-")
            assert f'release_change: {{head: "{prefix}*"}}' in fields, identity
        elif identity not in AWAITING_FIRST_ADOPTION:
            assert "release_change:" not in fields, identity
