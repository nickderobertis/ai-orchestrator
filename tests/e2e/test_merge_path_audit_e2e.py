"""`just repos --audit-gate-coverage` names, per identity, each check its merge path requires.

The published audit used to answer whether *something* verifies an identity's merge path
and print one `merge-path coverage:` line saying which. Read as coverage, that line is
what hid this host's defect: `nick-derobertis-site` reported coverage, a dispatched branch
passed the gate `config/onevcs.rules.yml` then named, published as PR #77, and the
required `llmlint` check that gate never ran refused it — a full dispatch and a full gate
cycle spent before anybody learned the verification was incomplete.

This repository answered that with a tracked copy of every sibling's required checks, a
filter that rewrote the audit out of it, and a test at the end of the gate that compared
the copy with GitHub — so whenever a sibling renamed a check, every branch here failed a
whole gate to learn it, on a defect no retry could fix. The adopted `onevcs` reads those
checks off the host itself — https://github.com/nickderobertis/onevcs/pull/136 — and
prints one `required checks:` line per identity above its checkouts, so the copy, the
filter and the drift gate are gone and these journeys hold the recipe to the answer the
tool now gives. The recipe, its wrapper, `onevcs`, git, and GitHub's own branch
protection are all real; only the checkouts are scratch, and they carry the real origins
so the audit asks about the real repositories.

llmlint: ignore-file[e2e_not_mocked] The scratch checkouts stand in for this host's own
clones, which a test may not register or publish from. Everything under test — the
recipe, `scripts/repos.sh`, and the published audit it wraps — is real, and the checks
asserted are the ones GitHub requires of the real repositories today.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The identity whose merge path required a check the retired gate did not run, which
#: is the defect this whole surface was built for; the check is required by that
#: repository's branch protection on `master`, which is why the audit has to say which
#: branch it read.
SITE = "github.com/nickderobertis/nick-derobertis-site"
SITE_REMOTE_CHECK = "classify-gate"
SITE_BRANCH = "master"
#: This repository, whose merge path is its own `pre-push` hook: it publishes
#: `local-direct`, opens no change request, and its base declares no required check.
LOCAL_DIRECT = "github.com/nickderobertis/ai-orchestrator"
#: The identity with the most required checks — cross-compilation, installs of a
#: published artifact, the pull request's own title, a hosted visual baseline — so its
#: report is where a list rather than a single name is read.
MANY_REMOTE_CHECKS = "github.com/nickderobertis/llmlint"
#: Two of the checks that repository requires and this host cannot run: the check that
#: validates the change request's title, and a build on another platform.
AMONG_MANY = ("pr-title", "cross (macos-latest)")

#: `onevcs`'s own wording, not this suite's: the two absence assertions below would
#: pass vacuously against an audit that renamed it, so the presence ones are what
#: keep this string honest.
REQUIRED = "required checks:"

# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is
# not a narrower tier of a memoized one: it moves this suite out of every memoized tier
# into the uncached `orchestrator:test-checkouts`, because its subject — what another
# repository's branch protection requires — is outside this workspace and no `nx.json`
# key could name it. A project of its own would give it a key, which is the thing it
# must not have.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same site, same
# reason: a narrow project edge is what earns a memo, and a memoized verdict about a
# repository that goes on changing is what this suite exists to catch.
pytestmark = pytest.mark.reads_checkouts


class Registry(NamedTuple):
    """A scratch registry the real recipes can be run against."""

    home: Path


@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> Registry:
    """Registered by the real recipe: one identity per shape of answer the audit gives."""
    root = tmp_path_factory.mktemp("merge-path-audit")
    manifest = root / "checkouts"
    paths = []
    for identity in (LOCAL_DIRECT, SITE, MANY_REMOTE_CHECKS):
        checkout = root / "checkouts.d" / identity.rpartition("/")[2]
        checkout.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=checkout, check=True)
        # The owner is part of the identity, so it is taken from the identity rather than
        # composed: a checkout registered under the wrong owner resolves a different
        # identity, and the audit would ask GitHub about a repository nobody meant.
        subprocess.run(
            ["git", "remote", "add", "origin", f"https://{identity}"],
            cwd=checkout,
            check=True,
        )
        paths.append(checkout)
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    applied = repos(home, "repos-apply", "--checkouts", str(manifest))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    return Registry(home=home)


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def repos(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a real recipe against a scratch registry root."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
    )


def required_lines(output: str) -> dict[str, str]:
    """Each identity's `required checks:` line, as the audit prints it under the heading.

    Keyed by identity because that is the unit the answer is about: the line is printed
    once per identity, above every checkout of it, and a checkout's own lines say which
    policy it publishes under rather than what can refuse the merge.
    """
    reports: dict[str, str] = {}
    identity = ""
    for line in output.splitlines():
        if not line.startswith(" "):
            identity = line.split("\t")[0]
            continue
        stripped = line.strip()
        if stripped.startswith(REQUIRED):
            reports[identity] = stripped
    return reports


def test_the_audit_names_the_required_check_that_can_refuse_the_merge(registry: Registry) -> None:
    """The defect's own case: the check the retired gate never ran is named, by branch."""
    audited = repos(registry.home, "repos", "--audit-gate-coverage")

    assert audited.returncode == 0, audited.stderr
    report = required_lines(audited.stdout)[SITE]
    assert SITE_REMOTE_CHECK in report, report
    assert f"for {SITE_BRANCH}" in report, report


def test_an_identity_with_several_remote_checks_reports_a_list_not_an_exemplar(
    registry: Registry,
) -> None:
    """A list, not an exemplar: each one is a separate reason a merge can be refused.

    Naming one and counting the rest would leave an operator to guess which of a dozen
    checks is the one their branch is about to fail on. The two named here are ones this
    host could never run — the change request's title has no meaning before there is a
    change request, and the other platform is not this one.
    """
    audited = repos(registry.home, "repos", "--audit-gate-coverage")

    report = required_lines(audited.stdout)[MANY_REMOTE_CHECKS]
    for check in AMONG_MANY:
        assert check in report, report


def test_an_identity_with_no_required_check_is_reported_as_requiring_none(
    registry: Registry,
) -> None:
    """The other half of an honest answer: an identity with nothing to list says so.

    A report that flagged every identity would be as useless as one that flagged none.
    This repository publishes `local-direct`, opens no change request, and its base
    declares no protection, so no required check exists to refuse a merge its own
    `pre-push` hook passed — and the audit says that rather than going silent, which is
    what parts "nothing is required" from "nothing was asked".
    """
    audited = repos(registry.home, "repos", "--audit-gate-coverage")

    report = required_lines(audited.stdout)[LOCAL_DIRECT]
    assert "none required" in report, report


def test_the_plain_listing_is_untouched(registry: Registry) -> None:
    """`just repos` is still the registry listing, with no audit and nothing appended."""
    listed = repos(registry.home, "repos")

    assert listed.returncode == 0, listed.stderr
    assert SITE in listed.stdout
    assert REQUIRED not in listed.stdout
    assert "merge-path coverage" not in listed.stdout


def test_the_published_flag_spelling_reaches_the_same_report(registry: Registry) -> None:
    """Both spellings are accepted, and both get the same audit.

    `--audit-gates` is what `onevcs` calls it and `--audit-gate-coverage` is what the
    planner doctrine calls it, so a caller reaching for either must get the one answer.
    """
    published = repos(registry.home, "repos", "--audit-gates")

    assert published.returncode == 0, published.stderr
    assert published.stdout == repos(registry.home, "repos", "--audit-gate-coverage").stdout


@pytest.mark.parametrize("arguments", [("repos", "--nope"), ("repos", "--audit-gates", "--nope")])
def test_a_refused_listing_stays_refused(registry: Registry, arguments: tuple[str, str]) -> None:
    """A rejected argument is refused by the recipe, whichever spelling asked for the audit."""
    refused = repos(registry.home, *arguments)

    assert refused.returncode != 0
    assert REQUIRED not in refused.stdout


def test_a_host_with_nothing_registered_still_gets_its_audit(tmp_path: Path) -> None:
    """An empty registry is a complete audit of nothing, not a failure of the recipe."""
    home = tmp_path / "empty-onevcs"
    home.mkdir()
    (home / "rules.yml").write_text(
        "version: 3\ntrailer_prefix: Orchestrator-\nrules: []\n"
        "default:\n  publication: change-auto\n  approvals: none\n",
        encoding="utf-8",
    )

    audited = repos(home, "repos", "--audit-gate-coverage")

    assert audited.returncode == 0, audited.stderr
    assert "no repositories registered" in audited.stdout
