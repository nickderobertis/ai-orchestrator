"""`just repos --audit-gate-coverage` says what can refuse a merge its verifier passed.

The published audit answers whether *something* verifies an identity's merge path and
prints one `merge-path coverage:` line saying which. Read as coverage, that line is
what hid this host's defect: `nick-derobertis-site` reported coverage, a dispatched
branch passed the gate `config/onevcs.rules.yml` then named, published as PR #77, and
the required `llmlint` check that gate never ran refused it — a full dispatch and a
full gate cycle spent before anybody learned the verification was incomplete.

onevcs 0.11.0 removed the gate, so nothing on this host front-runs a merge path any
more and *every* required check is one only the merge path runs. That makes the
question these journeys hold the answer to the only one left: what can refuse this
merge. The recipe, its wrapper, the filter, `onevcs`, and git are all real; only the
checkouts are scratch.

llmlint: ignore-file[e2e_not_mocked] The scratch checkouts stand in for this host's
own clones, which a test may not register or publish from. Everything under test —
the recipe, `scripts/repos.sh`, `scripts/merge-path-audit.py`, and the published
audit they wrap — is real, and the classification asserted is the tracked one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

FILTER = REPO_ROOT / "scripts" / "merge-path-audit.py"
INTERPRETER = REPO_ROOT / ".venv" / "bin" / "python3"
MERGE_PATH_CHECKS = REPO_ROOT / "config" / "merge-path-checks.json"

#: The identity whose merge path required a check the retired gate did not run, which
#: is the defect this whole surface was built for.
SITE = "github.com/nickderobertis/nick-derobertis-site"
SITE_REMOTE_CHECK = "classify-gate"
#: The identities that open no pull request, so their merge paths declare no required
#: checks and the inventory has nothing to list for them.
LOCAL_DIRECT = (
    "github.com/nickderobertis/ai-orchestrator",
    "github.com/nickderobertis/spanish-language-tutor",
)
#: The identity with the most required checks nothing here runs — cross-compilation,
#: installs of a published artifact, the pull request's own title, a hosted visual
#: baseline — so its report is where a list rather than a single name is read.
MANY_REMOTE_CHECKS = "github.com/nickderobertis/llmlint"
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `reads_checkouts` is
# the edge here, and it is the widest one on purpose: this suite's subject is another
# repository's live branch protection, which no `nx.json` key can name, so it runs in the
# uncached `orchestrator:test-checkouts` and is never memoized at all. A project of its
# own would give it a key and a memoized verdict about a repository that goes on changing
# — which is the thing this suite exists to catch. This change moves one constant onto an
# identity whose inventory is still empty.
#: A remote-publishing identity whose base branch declares no protection, so its
#: inventory is present and empty. That pairing is the one an operator most needs
#: told apart from an identity nobody inventoried at all: both have nothing to list,
#: and only one of them is a gap to go and look at.
#:
#: Which identity wears that shape is a fact about somebody else's branch protection and
#: moves without warning — this was `printobserver` until its own `main` grew three
#: required checks. The uncached `orchestrator:test-checkouts` tier is what notices, by
#: asking GitHub and failing when `config/merge-path-checks.json` disagrees; read a
#: failure here as this identity having grown one too, and move the constant onto another
#: the inventory still records as empty.
EMPTY_REMOTE_INVENTORY = "github.com/petsinc/cd-chat-tool-call-challenge"
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]

#: The smallest declaration the filter accepts. Every refusal below is this document
#: with exactly one thing wrong with it, so what each case demonstrates is that field
#: rather than an unrelated absence somewhere else in a hand-written fragment.
VALID = (
    '{"version": 2, "reasons": {"r": "why"}, '
    '"identities": {"a/b/c": {"branch": "main", "checks": {"llmlint": "r"}}}}'
)


class Registry(NamedTuple):
    """A scratch registry the real recipes can be run against."""

    home: Path


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] This suite predates
# this change, and the `reads_checkouts` marker is not a narrower tier of a memoized one:
# it moves a test out of every memoized tier into the uncached one, because its subject —
# another repository's branch protection — is outside this workspace and no `nx.json` key
# could name it. A project of its own would give it a key, which is the thing it must not
# have. This change moves one constant in it, onto an identity whose inventory is still
# empty.
@pytest.fixture(scope="module")
def registry(tmp_path_factory: pytest.TempPathFactory) -> Registry:
    """Registered by the real recipe: one identity per shape of answer the audit gives."""
    root = tmp_path_factory.mktemp("merge-path-audit")
    manifest = root / "checkouts"
    paths = []
    for identity in (
        "github.com/nickderobertis/ai-orchestrator",
        "github.com/nickderobertis/spanish-language-tutor",
        "github.com/nickderobertis/nick-derobertis-site",
        MANY_REMOTE_CHECKS,
        EMPTY_REMOTE_INVENTORY,
    ):
        checkout = root / "checkouts.d" / identity.rpartition("/")[2]
        checkout.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=checkout, check=True)
        # The owner is part of the identity, so it is taken from the identity rather than
        # composed: two of the repositories this host routes are not under one account,
        # and a checkout registered under the wrong owner resolves a different identity
        # and is reported as one nobody inventoried.
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


def repos(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a real recipe against a scratch registry root."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
    )


def coverage_lines(output: str) -> dict[str, list[str]]:
    """Each identity's coverage report: its `merge-path coverage:` line and what follows.

    Grouped by identity because that is the unit the answer is about — a checkout's
    line means nothing without the identity it was printed under.
    """
    reports: dict[str, list[str]] = {}
    identity = ""
    for line in output.splitlines():
        if not line.startswith(" "):
            identity = line.split("\t")[0]
            continue
        stripped = line.strip()
        if stripped.startswith("merge-path coverage:"):
            reports[identity] = [stripped]
        elif identity in reports and not stripped.startswith(("merge-path", "/")):
            reports[identity].append(stripped)
    return reports


# llmlint: ignore-block[tests_mirror_real_usage] These refusals cannot be reached
# through `just repos`; see this helper's docstring.
def filtered(stdin: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Drive the filter itself, across the boundary `scripts/repos.sh` spawns it over.

    `scripts/repos.sh` always spawns it with exactly one path, that path is always the
    tracked declaration, and what it pipes in is always a real audit — so the filter's
    own refusals are unreachable through the recipe by construction, and they are the
    only thing covered this way. Every journey the recipe *can* produce drives it.
    """
    return subprocess.run(
        [str(INTERPRETER), str(FILTER), *arguments],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=30,
    )


# llmlint: ignore-end[tests_mirror_real_usage]


def test_the_audit_names_the_required_checks_that_can_refuse_the_merge(
    registry: Registry,
) -> None:
    """The defect's own case: coverage is reported, and what can still refuse it is named."""
    audited = repos(registry.home, "repos", "--audit-gate-coverage")

    assert audited.returncode == 0, audited.stderr
    report = coverage_lines(audited.stdout)[SITE]
    assert report[0].endswith("— not the whole merge path"), report
    assert any("nothing on this host runs any of them" in line for line in report[1:]), report
    assert any(line.startswith(f"{SITE_REMOTE_CHECK} —") for line in report[1:]), report


def test_an_identity_with_no_required_checks_is_reported_as_having_none(
    registry: Registry,
) -> None:
    """The other half of an honest answer: an identity with nothing to list.

    A report that flagged every identity would be as useless as one that flagged none;
    `local-direct` publication opens no change request, so no required check exists to
    refuse a merge its own `pre-push` hook passed.

    The clause is about the *inventory* rather than about the verifier the published
    line just named, and deliberately: `onevcs` reports a checkout with no hook as
    covered by the host's required checks, and a clause answering "there are none"
    would contradict the sentence it is appended to instead of extending it.
    """
    audited = repos(registry.home, "repos", "--audit-gate-coverage")

    reports = coverage_lines(audited.stdout)
    for identity in LOCAL_DIRECT:
        report = reports[identity]
        assert report == [
            f"{report[0].split(' — ')[0]} — and config/merge-path-checks.json inventories "
            "no required check on main, so nothing recorded here can refuse a merge"
        ]


def test_an_identity_with_several_remote_checks_names_every_one_of_them(
    registry: Registry,
) -> None:
    """A list, not an exemplar: each one is a separate reason a merge can be refused.

    Naming one and counting the rest would leave an operator to guess which of eight
    checks is the one their branch is about to fail on.
    """
    declared = json.loads(MERGE_PATH_CHECKS.read_text(encoding="utf-8"))
    expected = declared["identities"][MANY_REMOTE_CHECKS]["checks"]

    audited = repos(registry.home, "repos", "--audit-gate-coverage")

    report = coverage_lines(audited.stdout)[MANY_REMOTE_CHECKS]
    assert report[0].endswith("— not the whole merge path"), report
    assert report[1].startswith(f"{len(expected)} required checks on main"), report
    assert report[1].endswith("nothing on this host runs any of them:"), report
    assert sorted(line.split(" — ")[0] for line in report[2:]) == sorted(expected)


def test_a_remote_identity_inventoried_with_no_required_checks_is_not_reported_unknown(
    registry: Registry,
) -> None:
    """Present and empty, which is a different answer from absent — through the recipe.

    `coverage unknown` is the verdict that sends somebody to go and look, so spending
    it on an identity somebody already answered is how a real gap stops being noticed.
    """
    audited = repos(registry.home, "repos", "--audit-gate-coverage")

    assert audited.returncode == 0, audited.stderr
    report = coverage_lines(audited.stdout)[EMPTY_REMOTE_INVENTORY]
    assert report == [
        f"{report[0].split(' — ')[0]} — and config/merge-path-checks.json inventories "
        "no required check on main, so nothing recorded here can refuse a merge"
    ]
    assert "coverage unknown" not in "\n".join(report), report


def test_the_plain_listing_is_untouched(registry: Registry) -> None:
    """`just repos` is still the registry listing, with no audit and nothing appended."""
    listed = repos(registry.home, "repos")

    assert listed.returncode == 0, listed.stderr
    assert SITE in listed.stdout
    assert "merge-path coverage" not in listed.stdout
    assert "refuse the merge" not in listed.stdout


def test_the_published_flag_spelling_reaches_the_same_report(registry: Registry) -> None:
    """Both spellings are accepted, and both get the audit this host means by it.

    `--audit-gates` is what `onevcs` calls it and `--audit-gate-coverage` is what the
    planner doctrine calls it, so a caller reaching for the published name must not
    quietly get the published answer instead of this one.
    """
    published = repos(registry.home, "repos", "--audit-gates")

    assert published.returncode == 0, published.stderr
    assert published.stdout == repos(registry.home, "repos", "--audit-gate-coverage").stdout


@pytest.mark.parametrize("arguments", [("repos", "--nope"), ("repos", "--audit-gates", "--nope")])
def test_a_refused_listing_stays_refused(registry: Registry, arguments: tuple[str, str]) -> None:
    """A rejected argument must not be swallowed by the pipe the audit path adds.

    The audit reads `onevcs`'s answer through a pipe, whose exit status is the last
    command's — so an unnoticed failure upstream would be reported as a clean audit of
    nothing at all, which is the shape of answer this whole change exists to remove.
    """
    refused = repos(registry.home, *arguments)

    assert refused.returncode != 0
    assert "refuse the merge" not in refused.stdout


def test_an_unclassified_identity_is_reported_unknown_rather_than_covered(
    tmp_path: Path,
) -> None:
    """The direction this has to fail in: a gap nobody recorded still sends you to look.

    A registered identity that `config/merge-path-checks.json` says nothing about is
    exactly the case where the published coverage line is least trustworthy, so it must
    not be the case where the answer reads as reassurance. Registered with the real
    recipe, because `just repos-apply` refuses a checkout no rule names — an identity
    only reaches this state by being registered on its own.
    """
    checkout = tmp_path / "unlisted"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/someone/unlisted"],
        cwd=checkout,
        check=True,
    )
    home = tmp_path / "onevcs"
    registered = repos(home, "register-repo", str(checkout))
    assert registered.returncode == 0, registered.stdout + registered.stderr

    audited = repos(home, "repos", "--audit-gate-coverage")

    assert audited.returncode == 0, audited.stderr
    report = coverage_lines(audited.stdout)["github.com/someone/unlisted"]
    assert "coverage unknown" in report[0], report
    assert "github.com/someone/unlisted" in report[0], report


def test_a_coverage_line_before_any_identity_is_not_attributed_to_one() -> None:
    """Nothing is guessed: a line with no identity above it is unknown, not the last one."""
    result = filtered(
        "    merge-path coverage: pre-push hook at /elsewhere\n", str(MERGE_PATH_CHECKS)
    )

    assert result.returncode == 0, result.stderr
    assert "coverage unknown" in result.stdout
    assert "this checkout's identity" in result.stdout


def test_the_filter_refuses_a_call_that_names_no_one_declaration() -> None:
    """Its one argument decides every verdict it prints, so it is not defaulted."""
    result = filtered("", str(MERGE_PATH_CHECKS), "extra")

    assert result.returncode == 2
    assert "just repos --audit-gate-coverage" in result.stderr


@pytest.mark.parametrize(
    ("content", "diagnostic"),
    [
        (None, "not a readable merge-path declaration"),
        ("[]", "must be a JSON object"),
        ("{not json", "not a readable merge-path declaration"),
        (VALID.replace('"version": 2', '"version": 1'), "`version` must be 2"),
        (VALID.replace('"version": 2', '"schema": 2'), "`version` must be 2"),
        (VALID.replace('"reasons": {"r": "why"}', '"reasons": []'), "reasons must be an object"),
        (
            VALID.replace('"reasons": {"r": "why"}', '"reasons": {"r": ""}'),
            "reasons must be an object mapping names to non-empty strings",
        ),
        (VALID.replace('"identities"', '"identities": 4, "unused"'), "`identities` must be"),
        (VALID.replace('"a/b/c"', '"b/c"'), "identities.b/c is not a `host/owner/name`"),
        (VALID.replace('{"branch"', '7, "d/e/f": {"branch"'), "identities.a/b/c must be an object"),
        (VALID.replace('"branch": "main"', '"branch": ""'), "branch must be the base branch"),
        (VALID.replace('"checks": {"llmlint": "r"}', '"checks": []'), "checks must be an object"),
        (VALID.replace('"checks": {"llmlint": "r"}', '"checks": {"llmlint": 1}'), "checks must"),
        (
            VALID.replace('"checks": {"llmlint": "r"}', '"checks": {"llmlint": "nowhere"}'),
            "names reasons ['nowhere'] that the top-level `reasons` object does not define",
        ),
    ],
)
def test_a_declaration_it_cannot_believe_is_refused_by_name(
    tmp_path: Path, content: str | None, diagnostic: str
) -> None:
    """What the operator is told can refuse a merge comes from this file, so it is checked.

    A record indexed without being validated fails as a traceback from inside a pipe,
    which says nothing about which entry is wrong or what would fix it — and the answer
    it interrupts is the one somebody is consulting before dispatching work.
    """
    declaration = tmp_path / "merge-path-checks.json"
    if content is not None:
        declaration.write_text(content, encoding="utf-8")

    result = filtered("", str(declaration))

    assert result.returncode == 2, result.stdout
    assert diagnostic in result.stderr
    assert "rerun `just repos --audit-gate-coverage`" in result.stderr


def test_a_declaration_that_is_not_even_text_is_refused_the_same_way(tmp_path: Path) -> None:
    """The read itself is part of the boundary: bytes that are not UTF-8 never parse."""
    declaration = tmp_path / "merge-path-checks.json"
    declaration.write_bytes(b'{"version": 2, "reasons": {"r": "\xff\xfe"}}')

    result = filtered("", str(declaration))

    assert result.returncode == 2, result.stdout
    assert "not a readable merge-path declaration" in result.stderr
    assert "rerun `just repos --audit-gate-coverage`" in result.stderr


def test_the_filter_forwards_an_audit_with_nothing_to_say() -> None:
    """An empty audit is still an audit; adding a line to one would be inventing output."""
    result = filtered("", str(MERGE_PATH_CHECKS))

    assert result.returncode == 0
    assert result.stdout == ""


def test_an_audit_this_filter_no_longer_recognizes_is_refused_rather_than_forwarded() -> None:
    """The upstream stream is an input too, and the direction it has to fail in is loud.

    `onevcs repos --audit-gates` owes this filter no output format. If a release respells
    `merge-path coverage:` or the tab ending an identity heading, every line stops
    matching — and forwarding them verbatim would exit 0, printing the published claim
    that *something* verifies each identity under the one command whose whole purpose is
    to say what can still refuse it. That is the reassurance PR #77 was published on, so
    an unrecognized stream is refused by name instead.
    """
    result = filtered(
        "registered repositories\n  someone/elsewhere gate=just check\n", str(MERGE_PATH_CHECKS)
    )

    assert result.returncode == 2, result.stdout
    assert result.stdout == ""
    assert "no identity heading and no `merge-path coverage:` line" in result.stderr
    assert "rerun `just repos --audit-gate-coverage`" in result.stderr


def test_an_identity_heading_alone_is_still_a_recognized_audit() -> None:
    """Either shape is enough, because a registered identity may head a block with no
    checkout under it — and refusing that would fail an audit that is simply short."""
    result = filtered(f"{LOCAL_DIRECT[0]}\tremote\tteam\tjust gate\n", str(MERGE_PATH_CHECKS))

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{LOCAL_DIRECT[0]}\tremote\tteam\tjust gate\n"


def test_a_host_with_nothing_registered_still_gets_its_audit(tmp_path: Path) -> None:
    """The empty registry is a real answer, and the structural check must not refuse it.

    `onevcs repos --audit-gates` does not go silent on an empty registry — it prints
    `no repositories registered` and exits 0. That line carries neither an identity
    heading nor a coverage claim, so a check that demands one of those shapes rejects
    the single case where there is genuinely nothing to rewrite, and tells the operator
    the published audit changed shape when all that happened is they registered nothing
    yet. Driven through the real recipe because the sentinel is upstream's wording, not
    this repository's, and a fixture repeating it would still pass if upstream moved.
    """
    home = tmp_path / "onevcs"
    home.mkdir()

    audited = repos(home, "repos", "--audit-gate-coverage")

    assert audited.returncode == 0, audited.stderr
    assert "no repositories registered" in audited.stdout


def test_every_reason_the_declaration_names_reaches_the_report(registry: Registry) -> None:
    """The prose an operator acts on is the tracked prose, not a paraphrase of it."""
    declared = json.loads(MERGE_PATH_CHECKS.read_text(encoding="utf-8"))
    site = declared["identities"][SITE]
    reason = declared["reasons"][site["checks"][SITE_REMOTE_CHECK]]

    audited = repos(registry.home, "repos", "--audit-gate-coverage")

    assert f"{SITE_REMOTE_CHECK} — {reason}" in audited.stdout
