"""Every repository keeps the policy it published under before `onevcs` owned the registry.

This host ran on a registry of its own until the adopted stack moved that mechanism
to `onevcs` — and moved none of the data, so `just repos` listed nothing and no
lifecycle dispatch could resolve a repository at all. `just repos-apply` is the
reproducible form of carrying it across, and what it carries is a merge path: an
identity re-registered a notch wider publishes to a base branch without the review
its repository requires, and nothing downstream notices.

So the pre-adoption registry is checked in at `tests/fixtures/pre-adoption-repos.json`
and this journey holds the tracked configuration to reproducing it. It builds a
checkout per pre-adoption checkout, carrying that repository's real origin, and
drives the real recipe with the real `config/onevcs.rules.yml` against a scratch
registry root — so what is asserted is what `onevcs` itself resolves, not a reading
of the YAML.

llmlint: ignore-file[e2e_not_mocked] The scratch checkouts stand in for this host's
own clones, which a test may not register or publish from; the recipe, the script,
`onevcs`, and git are all real, and the policy asserted is the one `onevcs` resolved.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

import pytest
from registered_checkouts import TRACKED_CHECKOUTS, RepoIdentity

from orchestrator.root import REPO_ROOT

#: The registry this host ran on before `onevcs` owned one, verbatim.
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "pre-adoption-repos.json"
#: The tracked rules file the recipe installs, which decides every listed checkout's
#: publication path — and, since onevcs 0.11.0, nothing else. It names no verifier,
#: because this host runs none.
TRACKED_RULES = REPO_ROOT / "config" / "onevcs.rules.yml"

#: The schema this host's rules file declares. At 3 a `gate:` anywhere is refused by
#: `deny_unknown_fields`; at 1 and 2 it is accepted, ignored, and reported once. Both
#: halves are driven below, because "we migrated" and "the engine still tolerates an
#: unmigrated host" are separate claims and only one of them is about this file.
RULES_SCHEMA_VERSION = 3

#: How every rule in the tracked file names the repository it matches. Reading them
#: back out is what makes a rule added later fail here, rather than at that
#: repository's first publication.
RULE_MATCH = re.compile(
    r"- match: \{host: (?P<host>[^,]+), owner: (?P<owner>[^,]+), name: (?P<name>[^}]+)\}"
)
#: The identities whose policy differs from every other rule in the file, with the
#: `(publication, approvals)` each one resolves. Unlike the historical golden below,
#: this also covers identities registered after the onevcs adoption.
POLICY_EXCEPTIONS = {
    "github.com/nickderobertis/ai-orchestrator": ("local-direct", "none"),
    "github.com/nickderobertis/spanish-language-tutor": ("local-direct", "none"),
    "github.com/petsinc/cd-chat-tool-call-challenge": ("change-open", "required"),
    "github.com/petsinc/hellopatient": ("change-open", "required"),
    "github.com/petsinc/org-apps": ("change-open", "required"),
    "github.com/petsinc/referral-app": ("change-open", "required"),
}
#: What every *other* ruled identity publishes under: a single-owner `nickderobertis`
#: repository whose change request merges itself once its merge path passes. The
#: golden covers this for the identities that predate the adoption; a rule written
#: since — `llmlint` was the first, `allowlister-remote` and `ev-trip-planner` the
#: latest — is covered by nothing else, so a new sibling given a policy of its own
#: fails here rather than at its first publication.
#:
#: The exceptions above are read the same way and are the reason this is a record
#: rather than an owner test: every `petsinc` identity here is a team repository whose
#: change request stays ready-for-review, and reading that off the owner would make a
#: `petsinc` repository quietly given `change-auto` pass.
SIBLING_POLICY = ("change-auto", "none")

#: The identity whose routing the journey below resolves. Spelled out rather than
#: derived from the tracked files, so that what the journey claims to prove is stated
#: independently of the configuration under test.
PRINTOBSERVER: RepoIdentity = "github.com/nickderobertis/printobserver"
#: The team identity whose routing the second such journey resolves, spelled out for
#: the same reason. It is the one whose rule a resolver cannot be taken on trust for:
#: `default:` resolves the reviewed pair too, so a policy read alone would answer
#: identically whether the rule matched or nothing did.
HELLOPATIENT: RepoIdentity = "github.com/petsinc/hellopatient"

RepoType = Literal["single-owner", "team"]
Workflow = Literal["local", "remote"]


class Identity(NamedTuple):
    """One repository as the pre-adoption registry described it."""

    #: The key the pre-adoption registry filed it under, which its checkouts name.
    url: str
    #: The identity `onevcs` normalizes this repository's origin URL to.
    key: RepoIdentity
    origin: str
    #: `single-owner` or `team`.
    repo_type: RepoType
    #: `local` or `remote`.
    workflow: Workflow

    @property
    def publication(self) -> str:
        """How a change here must publish now.

        This is the whole migration, stated once: the old registry split the
        decision across a repository type and a workflow, and the rules file states
        it as how a change publishes plus whether somebody else has to approve it.
        """
        if self.workflow == "local":
            return "local-direct"
        return "change-open" if self.repo_type == "team" else "change-auto"

    @property
    def approvals(self) -> str:
        """Whether somebody else has to approve a change here."""
        return "required" if self.repo_type == "team" else "none"


class Checkout(NamedTuple):
    """One local checkout as the pre-adoption registry described it."""

    path: str
    #: The pre-adoption identity URL this checkout belongs to.
    identity: str


def _golden() -> tuple[tuple[Identity, ...], tuple[Checkout, ...]]:
    """The pre-adoption registry, read once into records with a shape to check."""
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))
    identities = tuple(
        Identity(
            url=url,
            key=record["origin"].removeprefix("https://").removesuffix(".git"),
            origin=record["origin"],
            repo_type=record["repo_type"],
            workflow=record["workflow"],
        )
        for url, record in sorted(document["identities"].items())
    )
    checkouts = tuple(
        Checkout(path=record["path"], identity=record["identity"])
        for _, record in sorted(document["checkouts"].items())
    )
    return identities, checkouts


IDENTITIES, CHECKOUTS = _golden()
#: Each identity, keyed the way its checkout records name it.
BY_URL = {identity.url: identity for identity in IDENTITIES}
#: The `$HOME` the pre-adoption registry's paths are absolute under, taken from the
#: fixture itself. The registry was captured on one host and this suite runs on
#: others, so substituting *this* host's home would render the fixture's paths as
#: `~`-spellings no host has and make the tracked list unmatchable off that machine.
PRE_ADOPTION_HOME = os.path.commonpath([entry.path for entry in CHECKOUTS])


def checkout(directory: Path, origin: str) -> Path:
    """A real git checkout of `origin`, which is all registration reads of one."""
    directory.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=directory, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=directory, check=True)
    return directory


def apply_registry(manifest: Path, home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the real recipe against a scratch registry root."""
    return subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
    )


def onevcs(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--project", str(REPO_ROOT), "onevcs", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
    )


def ruled_identities() -> tuple[RepoIdentity, ...]:
    """Every identity `config/onevcs.rules.yml` names, in the order it names them."""
    text = TRACKED_RULES.read_text(encoding="utf-8")
    named = tuple(
        f"{rule['host']}/{rule['owner']}/{rule['name']}" for rule in RULE_MATCH.finditer(text)
    )
    assert len(named) == text.count("- match:"), (
        "config/onevcs.rules.yml no longer writes every rule's match as a one-line flow "
        f"mapping, so only {len(named)} of {text.count('- match:')} rules were read back"
    )
    return named


def reported(output: str, key: str) -> str:
    """One field of `onevcs rules check`, without its `(from rule 1)` annotation."""
    for line in output.splitlines():
        name, separator, value = line.partition(": ")
        if separator and name.strip() == key:
            return value.split(" (from ")[0].strip()
    raise AssertionError(f"no {key!r} in:\n{output}")


class Applied(NamedTuple):
    """One scratch registry, brought up to the tracked configuration."""

    home: Path
    manifest: Path
    result: subprocess.CompletedProcess[str]


class RegisteredCheckout(TypedDict):
    identity: str
    path: str


class RegistryDocument(TypedDict):
    identities: dict[str, object]
    checkouts: dict[str, RegisteredCheckout]


@pytest.fixture(scope="module")
def applied(tmp_path_factory: pytest.TempPathFactory) -> Applied:
    """The pre-adoption registry's checkouts, registered by the real recipe."""
    root = tmp_path_factory.mktemp("repo-registry")
    paths = [
        checkout(root / "checkouts" / Path(entry.path).name, BY_URL[entry.identity].origin)
        for entry in CHECKOUTS
    ]
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    result = apply_registry(manifest, home)
    assert result.returncode == 0, result.stdout + result.stderr
    return Applied(home=home, manifest=manifest, result=result)


@pytest.fixture(scope="module")
def ruled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A registry holding a checkout of *every* identity the tracked rules name.

    The `applied` fixture registers the pre-adoption checkouts, which is what the
    migration assertions are about; an identity registered since — `llmlint` is the
    first — has a rule there but no checkout, and `onevcs rules check` resolves a
    repository through a registered checkout. Registering one per rule is what lets
    the gate assertions below cover the whole file rather than only the rules that
    predate the adoption.
    """
    root = tmp_path_factory.mktemp("ruled-registry")
    paths = [
        checkout(root / "checkouts" / key.rpartition("/")[2], f"https://{key}")
        for key in ruled_identities()
    ]
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    result = apply_registry(manifest, home)
    assert result.returncode == 0, result.stdout + result.stderr
    return home


def test_every_pre_adoption_identity_and_checkout_is_registered(applied: Applied) -> None:
    registry: RegistryDocument = json.loads(
        (applied.home / "registry.json").read_text(encoding="utf-8")
    )
    assert set(registry["identities"]) == {identity.key for identity in IDENTITIES}
    actual = {(record["path"], record["identity"]) for record in registry["checkouts"].values()}
    expected = {
        (
            str(applied.manifest.parent / "checkouts" / Path(entry.path).name),
            BY_URL[entry.identity].key,
        )
        for entry in CHECKOUTS
    }
    assert actual == expected


def test_apply_reports_the_resolved_policy_table(applied: Applied) -> None:
    assert "apply-repo-registry: resolved policy" in applied.result.stdout
    assert "ai-orchestrator" in applied.result.stdout
    assert "local-direct" in applied.result.stdout
    assert "org-apps" in applied.result.stdout
    assert "change-open" in applied.result.stdout


def test_resolve_accepts_registered_spellings_and_refuses_bare_owner_name(
    applied: Applied,
) -> None:
    """Plans may name registry identities, aliases, origins, or checkout paths."""
    identity = IDENTITIES[0]
    registry: RegistryDocument = json.loads(
        (applied.home / "registry.json").read_text(encoding="utf-8")
    )
    alias, checkout_record = next(
        (alias, record)
        for alias, record in registry["checkouts"].items()
        if record["identity"] == identity.key
    )
    accepted = (
        identity.key,
        alias,
        identity.origin,
        checkout_record["path"],
    )
    for spelling in accepted:
        resolved = onevcs(applied.home, "resolve", spelling)
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert identity.key in resolved.stdout

    owner_name = identity.key.removeprefix("github.com/")
    refused = onevcs(applied.home, "resolve", owner_name)
    assert refused.returncode != 0, refused.stdout
    assert owner_name in refused.stderr


@pytest.mark.parametrize("identity", IDENTITIES, ids=lambda identity: identity.key)
def test_each_identity_publishes_the_way_it_did(applied: Applied, identity: Identity) -> None:
    checked = onevcs(applied.home, "rules", "check", identity.key)
    assert checked.returncode == 0, checked.stdout + checked.stderr

    assert reported(checked.stdout, "publication") == identity.publication
    assert reported(checked.stdout, "approvals") == identity.approvals
    assert reported(checked.stdout, "matched").startswith("rule ")


def test_the_team_repository_still_needs_a_review(applied: Applied) -> None:
    """The one identity where a wrong answer merges unreviewed work into a team's base."""
    checked = onevcs(applied.home, "rules", "check", "github.com/petsinc/org-apps")
    assert reported(checked.stdout, "publication") == "change-open"
    assert reported(checked.stdout, "approvals") == "required"


@pytest.mark.parametrize(
    "identity",
    (
        "github.com/nickderobertis/ai-orchestrator",
        "github.com/nickderobertis/spanish-language-tutor",
    ),
)
def test_local_direct_repositories_publish_locally(ruled: Path, identity: str) -> None:
    """Local-first identities open no change request and merge in place."""
    checked = onevcs(ruled, "rules", "check", identity)
    assert reported(checked.stdout, "publication") == "local-direct"
    assert reported(checked.stdout, "approvals") == "none"


def test_the_tracked_rules_declare_the_migrated_schema_and_name_no_gate() -> None:
    """The file itself: version 3, and no `gate:` anywhere in it.

    Asserted on the tracked bytes as well as on what `onevcs` resolves, because the two
    fail differently. A `gate:` reintroduced under `version: 3` is refused by the
    engine and shows up as a broken apply; one reintroduced together with a *downgrade*
    to `version: 2` resolves perfectly well, prints one deprecation nobody reads, and
    silently returns this host to running a verifier beside the real one.
    """
    text = TRACKED_RULES.read_text(encoding="utf-8")
    declared = [line for line in text.splitlines() if line.startswith("version:")]

    assert declared == [f"version: {RULES_SCHEMA_VERSION}"], declared
    named = [
        line for line in text.splitlines() if not line.lstrip().startswith("#") and "gate" in line
    ]
    assert not named, (
        "config/onevcs.rules.yml names a gate again; onevcs 0.11.0 removed the concept "
        f"and the merge path is the verifier: {named}"
    )


@pytest.mark.parametrize("key", sorted(frozenset(ruled_identities())))
def test_the_resolved_policy_is_publication_and_approvals_and_nothing_else(
    ruled: Path, key: RepoIdentity
) -> None:
    """Contract A, read off the engine: `rules check` reports no `gate:` line at all.

    The reading that matters is the tool's rather than the file's. A rules file this
    host writes could be silent about a gate while the engine still resolved one from
    a default, which is exactly the shape of the two wrong diagnoses this repository
    records — so what is asserted is the policy a publication would really run under.

    And that it is the policy this file *declares*, matched from a rule rather than
    fallen through to `default:`. An identity added since the migration has no row in
    the golden to check it against, so a rule written a notch wider — or one the
    engine never matched at all — would otherwise reach a publication before anything
    disagreed with it.
    """
    checked = onevcs(ruled, "rules", "check", key)
    assert checked.returncode == 0, checked.stdout + checked.stderr

    fields = {
        line.partition(": ")[0].strip() for line in checked.stdout.splitlines() if ": " in line
    }
    assert "gate" not in fields, checked.stdout
    assert {"publication", "approvals"} <= fields, checked.stdout
    assert reported(checked.stdout, "matched").startswith("rule ")

    resolved = (reported(checked.stdout, "publication"), reported(checked.stdout, "approvals"))
    assert resolved == POLICY_EXCEPTIONS.get(key, SIBLING_POLICY), checked.stdout


def test_a_gate_at_the_migrated_schema_version_is_refused_by_name(tmp_path: Path) -> None:
    """The half of Contract A that keeps this file from drifting back.

    At `version: 3` a rule is `{publication, approvals}` exactly and `gate:` is an
    unknown field, so a rules file carrying one does not load. Driven through the real
    recipe because that is where an operator meets it: an apply that installed a file
    the engine cannot read would leave every publication resolving the previous rules.
    """
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    gated = tmp_path / "gated.yml"
    gated.write_text(
        f"version: {RULES_SCHEMA_VERSION}\n"
        "rules:\n"
        "  - match: {host: github.com, owner: nickderobertis, name: onevcs}\n"
        "    publication: change-auto\n"
        "    approvals: none\n"
        '    gate: {command: ["just", "gate"]}\n'
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n",
        encoding="utf-8",
    )

    result = apply_registry(manifest, tmp_path / "home", "--rules", str(gated))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "is not a valid onevcs rules file" in result.stderr
    assert "gate" in result.stderr, result.stderr
    assert not (tmp_path / "home" / "rules.yml").exists()


def test_a_gate_at_an_older_schema_version_is_accepted_ignored_and_reported(
    tmp_path: Path,
) -> None:
    """The other half: an unmigrated host keeps publishing, and is told why once.

    This is the measurement that makes the removal real rather than nominal on this
    host. The same `gate: ["false"]` that an `onevcs` before 0.11.0 would have run and
    been refused by is now read, dropped, and named on stderr — so the *engine*, not
    just this repository's file, is what stopped running a tier of its own.
    """
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    legacy = tmp_path / "legacy.yml"
    legacy.write_text(
        "version: 2\n"
        "rules:\n"
        "  - match: {host: github.com, owner: nickderobertis, name: onevcs}\n"
        "    publication: change-auto\n"
        "    approvals: none\n"
        '    gate: {command: ["false"]}\n'
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"

    result = apply_registry(manifest, home, "--rules", str(legacy))
    assert result.returncode == 0, result.stdout + result.stderr

    checked = onevcs(home, "rules", "check", "github.com/nickderobertis/onevcs")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "gate" not in {
        line.partition(": ")[0].strip() for line in checked.stdout.splitlines() if ": " in line
    }, checked.stdout
    assert reported(checked.stdout, "publication") == "change-auto"
    assert "version 3 removed" in checked.stderr, checked.stderr
    assert str(home / "rules.yml") in checked.stderr, checked.stderr


def test_reapplying_the_configuration_changes_nothing(applied: Applied) -> None:
    """Idempotence, which is what makes this a recipe to re-run rather than a migration."""
    document = applied.home / "registry.json"
    before = document.read_bytes()
    again = apply_registry(applied.manifest, applied.home)
    assert again.returncode == 0, again.stdout + again.stderr
    assert document.read_bytes() == before
    assert "rules      unchanged" in again.stdout


def test_dry_run_parses_comments_whitespace_and_tilde_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "operator-home"
    present = checkout(fake_home / "checkout", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text("  # comment\n  ~/checkout   # local clone\n", encoding="utf-8")
    registry_home = tmp_path / "registry"
    monkeypatch.setenv("HOME", str(fake_home))

    result = apply_registry(manifest, registry_home, "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"would register  {present}" in result.stdout
    assert "rules      would install" in result.stdout
    assert "dry run — 1 checkout(s) would be registered, 0 skipped" in result.stdout
    assert not registry_home.exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--checkouts",), "usage: apply-repo-registry.sh"),
        (("--rules",), "usage: apply-repo-registry.sh"),
        (("--unknown",), "unknown argument"),
        (("--checkouts", "/does/not/exist"), "does not exist"),
    ],
)
def test_invalid_cli_input_is_rejected(
    tmp_path: Path, arguments: tuple[str, ...], message: str
) -> None:
    result = apply_registry(tmp_path / "unused", tmp_path / "home", *arguments)
    assert result.returncode == 2
    assert message in result.stderr


def test_help_is_successful(tmp_path: Path) -> None:
    result = apply_registry(tmp_path / "unused", tmp_path / "home", "--help")
    assert result.returncode == 0
    assert "usage: apply-repo-registry.sh" in result.stderr


# llmlint: ignore[tests_mirror_real_usage] Corrupt JSON must be seeded directly.
def test_malformed_existing_registry_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "registry.json").write_text("not json", encoding="utf-8")
    manifest = tmp_path / "checkouts"
    manifest.write_text("", encoding="utf-8")

    result = apply_registry(manifest, home)

    assert result.returncode == 1
    assert "cannot read a valid registry" in result.stderr


#: A rules file the adopted `onevcs` loads, standing in for whatever a host already has
#: installed when an invalid replacement is refused.
INSTALLED_RULES = (
    "version: 3\ntrailer_prefix: Orchestrator-\nrules: []\n"
    "default:\n  publication: change-open\n  approvals: required\n"
)


def test_invalid_rules_do_not_replace_the_installed_rules(tmp_path: Path) -> None:
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    installed = home / "rules.yml"
    # A file `onevcs` can load, because the adopted release's `register` resolves a
    # checkout's policy from the installed rules on the way in — an installed file it
    # cannot read fails the registration before the replacement is ever validated.
    installed.write_text(INSTALLED_RULES, encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("not: [valid", encoding="utf-8")

    result = apply_registry(manifest, home, "--rules", str(invalid))

    assert result.returncode == 1
    assert "is not a valid onevcs rules file" in result.stderr
    assert installed.read_text(encoding="utf-8") == INSTALLED_RULES


def test_invalid_rules_are_rejected_with_no_present_checkout(tmp_path: Path) -> None:
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{tmp_path / 'not-cloned'}\n", encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("not: [valid", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home", "--rules", str(invalid))

    assert result.returncode == 1
    assert "is not a valid onevcs rules file" in result.stderr
    assert not (tmp_path / "home" / "rules.yml").exists()


def test_empty_onevcs_home_is_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        ["just", "repos-apply"],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": ""},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "ONEVCS_HOME is set but empty" in result.stderr


@pytest.mark.parametrize("onevcs_home", ["relative/registry", "/"])
def test_unsafe_onevcs_home_is_rejected(tmp_path: Path, onevcs_home: str) -> None:
    result = subprocess.run(
        ["just", "repos-apply"],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": onevcs_home},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "must name an absolute directory other than /" in result.stderr


# llmlint: ignore[tests_mirror_real_usage] The legacy field must be seeded directly.
def test_registry_referencing_another_rules_file_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "registry.json").write_text(
        json.dumps({"rules": str(tmp_path / "elsewhere.yml")}), encoding="utf-8"
    )
    manifest = tmp_path / "checkouts"
    manifest.write_text("", encoding="utf-8")

    result = apply_registry(manifest, home)

    assert result.returncode == 1
    assert "would be ignored" in result.stderr


def test_checkout_registration_failure_is_reported(tmp_path: Path) -> None:
    not_a_checkout = tmp_path / "not-a-checkout"
    not_a_checkout.mkdir()
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{not_a_checkout}\n", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home")

    assert result.returncode == 1
    assert "registering" in result.stderr
    assert "failed" in result.stderr


def test_work_preserved_before_the_adoption_is_still_recognized(tmp_path: Path) -> None:
    """The provenance prefix is part of the registry's data, and stranding it is silent.

    `onevcs` spells its trailer keys `Onevcs-` when nothing configures one, and every
    branch this host preserved — plus every recovery attestation already on a base
    branch — carries `Orchestrator-`. A marker under an unconfigured prefix is
    neither read nor ignored: it is listed as unrecoverable and refused publication,
    so a rules file that dropped `trailer_prefix` would strand every preserved
    branch that predates the adoption while still reporting a green apply.
    """
    preserved = checkout(tmp_path / "preserved", "https://github.com/nickderobertis/onevcs.git")
    committer = ["-c", "user.email=t@example.com", "-c", "user.name=Test"]

    def commit(message: str) -> None:
        subprocess.run(["git", "add", "-A"], cwd=preserved, check=True)
        subprocess.run(["git", *committer, "commit", "-qm", message], cwd=preserved, check=True)

    (preserved / "file").write_text("base\n", encoding="utf-8")
    commit("chore: base")
    subprocess.run(["git", "checkout", "-qb", "preserved/work"], cwd=preserved, check=True)
    (preserved / "file").write_text("base\nwork\n", encoding="utf-8")
    commit("chore: orchestrated change (incomplete step)\n\nOrchestrator-Status: incomplete")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=preserved, check=True)

    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{preserved}\n", encoding="utf-8")
    home = tmp_path / "home"
    applied = apply_registry(manifest, home)
    assert applied.returncode == 0, applied.stdout + applied.stderr

    listed = onevcs(home, "recoverable")
    assert "preserved/work" in listed.stdout
    assert "incomplete step (provenance marker)" in listed.stdout
    assert "not configured to read" not in listed.stdout, listed.stdout
    checked = onevcs(home, "rules", "check", "github.com/nickderobertis/onevcs")
    assert reported(checked.stdout, "trailer_prefix") == "Orchestrator-"


def test_a_publication_checkout_and_its_safety_clone_register_as_one_identity(
    tmp_path: Path,
) -> None:
    """Two checkouts of one repository, through the real recipe, under one policy.

    That the pair collapses to one identity is `onevcs`'s doing rather than the tracked
    file's — it resolves an identity from a checkout's own `origin`, not from its path —
    so it is driven rather than read: neither checkout can end up a notch wider than the
    other.
    """
    origin = "https://github.com/nickderobertis/printobserver.git"
    publication = checkout(tmp_path / "checkouts" / "nickderobertis__printobserver", origin)
    execution = checkout(tmp_path / "checkouts" / "nickderobertis__printobserver-isolated", origin)
    manifest = tmp_path / "checkouts.list"
    manifest.write_text(f"{publication}\n{execution}\n", encoding="utf-8")
    home = tmp_path / "home"

    result = apply_registry(manifest, home)
    assert result.returncode == 0, result.stdout + result.stderr

    registry: RegistryDocument = json.loads((home / "registry.json").read_text(encoding="utf-8"))
    identity = "github.com/nickderobertis/printobserver"
    assert set(registry["identities"]) == {identity}
    assert {record["path"] for record in registry["checkouts"].values()} == {
        str(publication),
        str(execution),
    }

    # Either alias `onevcs` derived from a directory name is a spelling a plan node may
    # name, and both have to land on one identity and one policy: a safety clone resolving
    # a notch wider is how work executed in one checkout comes to publish differently from
    # work executed in the other.
    for alias in ("nickderobertis__printobserver", "nickderobertis__printobserver-isolated"):
        resolved = onevcs(home, "resolve", alias)
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert identity in resolved.stdout

        checked = onevcs(home, "rules", "check", alias)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-auto"
        assert reported(checked.stdout, "approvals") == "none"
        assert reported(checked.stdout, "matched").startswith("rule ")


def tracked_checkouts_of(repository: str) -> tuple[str, ...]:
    """Every entry of the tracked checkout list that is a checkout of `repository`.

    Read out of the file rather than restated beside it, because the tracked list is
    half of what the journey below evaluates: an entry dropped from it is a checkout
    this registration no longer has, and the count asserted there is what says so.
    """
    return tuple(
        entry
        for line in TRACKED_CHECKOUTS.read_text(encoding="utf-8").splitlines()
        if (entry := line.partition("#")[0].strip()) and which_checkout(entry) == repository
    )


def test_the_tracked_registration_routes_printobserver_through_its_own_rule(
    tmp_path: Path,
) -> None:
    """The tracked configuration alone, asked what it routes — nothing of this host's.

    Routing is an answer a resolver gives rather than a field a file contains: first
    match wins, so a rule reading exactly as intended proves nothing while a broader
    one above it could be deciding instead. Asking a resolver on this host would prove
    no more, since it answers out of a registry any earlier registration could have
    filled, so the registry here is a scratch one holding only what these files install.
    """
    entries = tracked_checkouts_of("printobserver")
    assert len(entries) == 2, entries

    # The scratch checkouts stand in for this host's own clones, which a test may not
    # register from; each keeps its listed directory name, because that name is what
    # `onevcs` derives the alias a plan node would name it by.
    origin = f"https://{PRINTOBSERVER}.git"
    paths = [checkout(tmp_path / "checkouts" / Path(entry).name, origin) for entry in entries]
    manifest = tmp_path / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = tmp_path / "onevcs"

    applied = apply_registry(manifest, home)
    assert applied.returncode == 0, applied.stdout + applied.stderr

    ruled = ruled_identities()
    assert ruled.count(PRINTOBSERVER) == 1, ruled
    host, owner, name = PRINTOBSERVER.split("/")
    matched = f"{{host: {host}, owner: {owner}, name: {name}}}"
    expected_rule = f"rule {ruled.index(PRINTOBSERVER) + 1} {matched}"

    # The identity itself and either alias, because a lifecycle node names a checkout
    # and a landing verb names the identity, and both have to reach the same rule.
    for argument in (PRINTOBSERVER, *(Path(entry).name for entry in entries)):
        checked = onevcs(home, "rules", "check", argument)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-auto", checked.stdout
        assert reported(checked.stdout, "approvals") == "none", checked.stdout
        assert reported(checked.stdout, "matched") == expected_rule, checked.stdout


def test_the_tracked_registration_routes_hellopatient_through_its_own_rule(
    tmp_path: Path,
) -> None:
    """The team half of the journey above: the tracked entry, and the rule it reaches.

    A team identity is where reading the resolved policy alone proves the least. Its
    rule and `default:` resolve the same `change-open` / `approvals: required` pair, so
    a policy read answers identically whether the rule matched or the checkout fell
    through to the fallthrough — and a checkout that falls through is one
    `just repos-apply` refuses rather than registers, which is a routing install broken
    for every other manager on this host rather than one repository publishing wrongly.
    So what is asserted here is the rule that decided, by its position in the file.

    Its listed checkout is registered by the real recipe rather than read out of the
    file, because that entry is what makes `onevcs` resolve the alias a lifecycle node
    would name it by: an identity ruled but unlisted has no checkout to publish from.
    """
    entries = tracked_checkouts_of("hellopatient")
    assert len(entries) == 1, entries

    # A scratch checkout under the listed directory name, standing in for this host's
    # own clone for the reason the journey above uses one: registering from the real
    # checkout would write the shared registry live dispatches resolve through.
    origin = f"https://{HELLOPATIENT}.git"
    path = checkout(tmp_path / "checkouts" / Path(entries[0]).name, origin)
    manifest = tmp_path / "checkouts.list"
    manifest.write_text(f"{path}\n", encoding="utf-8")
    home = tmp_path / "onevcs"

    applied = apply_registry(manifest, home)
    assert applied.returncode == 0, applied.stdout + applied.stderr

    registry: RegistryDocument = json.loads((home / "registry.json").read_text(encoding="utf-8"))
    assert set(registry["identities"]) == {HELLOPATIENT}

    ruled = ruled_identities()
    assert ruled.count(HELLOPATIENT) == 1, ruled
    host, owner, name = HELLOPATIENT.split("/")
    expected_rule = (
        f"rule {ruled.index(HELLOPATIENT) + 1} {{host: {host}, owner: {owner}, name: {name}}}"
    )

    for argument in (HELLOPATIENT, Path(entries[0]).name):
        checked = onevcs(home, "rules", "check", argument)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-open", checked.stdout
        assert reported(checked.stdout, "approvals") == "required", checked.stdout
        assert reported(checked.stdout, "matched") == expected_rule, checked.stdout


def test_a_checkout_this_host_does_not_have_is_skipped(tmp_path: Path) -> None:
    """A second machine holds a subset of these checkouts, and still registers it."""
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n{tmp_path / 'never-cloned'}\n", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skip       not on this host" in result.stdout
    assert "1 checkout(s) registered, 1 skipped" in result.stdout


def test_a_repository_no_rule_names_fails_the_apply(tmp_path: Path) -> None:
    """Falling through to the reviewed default is safe, and is nobody's configured policy.

    Reporting it as a pass is how a mistyped owner or a rule nobody wrote reaches a
    publication as a change request against a repository that wanted a local merge.
    """
    unlisted = checkout(tmp_path / "unlisted", "https://github.com/someone/unlisted.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{unlisted}\n", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no rule in" in result.stderr
    assert "unlisted" in result.stderr


#: The suffix a safety clone's directory carries, with the optional ordinal a host
#: holding more than one of them appends. A safety clone is a second checkout of the
#: repository it is named after — `~/ai-orchestrator-isolated` is a checkout of
#: `ai-orchestrator` — so which repository it belongs to is the name without it.
SAFETY_CLONE = re.compile(r"-isolated(-\d+)?$")


def which_checkout(path: str) -> str:
    """Which repository a checkout path names, whichever host's layout spelled it.

    The hosts this list serves lay the same checkouts out differently — one keeps
    this repository's clones at the top of `$HOME` and the engine repositories under
    `~/projects`, the other keeps everything under `~/projects` and lets the
    dispatcher clone the engines into `~/.ai-orchestrator/repos/<owner>__<name>` —
    so two spellings of one checkout agree on nothing but their last component, with
    that owner prefix removed. `onevcs` reads the same component to derive an alias.

    A safety clone's suffix comes off too, because the repository it is a checkout of
    is the one whose rule governs it: registration resolves an identity from the
    checkout's own `origin`, so `<name>-isolated` and `<name>` are one identity and
    one policy. The pre-adoption registry happens to name this repository's own
    safety clones, which is why the caller below covered them without this; a
    repository registered *since* has no such row, so its safety clone would read as
    a checkout no rule names while the rule matching it sits in the file.
    """
    return SAFETY_CLONE.sub("", Path(path).name.rpartition("__")[2])


def ruled_repositories() -> set[str]:
    """Every repository `config/onevcs.rules.yml` names outright.

    Each rule's `match:` is a one-line flow mapping ending in the repository name, so
    this reads the tracked file rather than depending on a YAML parser this package
    does not ship. A `match:` spelled any other way is simply not found here, which
    fails the caller below rather than passing it — the safe direction, since what
    that assertion is protecting is that no checkout is registered without a rule.
    """
    return set(re.findall(r"name:\s*([\w.-]+)\s*}", TRACKED_RULES.read_text(encoding="utf-8")))


def test_the_tracked_checkout_list_holds_every_pre_adoption_checkout() -> None:
    """The recipe's default input, against the registry it was built to reproduce.

    Two claims, because the list is longer than that registry and has to be: a
    checkout a host does not have is skipped by design, so both layouts' spellings
    are tracked here and each host registers the ones it holds. So every
    pre-adoption checkout must still be listed under the spelling it was migrated
    from — dropping one silently unregisters a repository on the host that has it —
    and everything listed beside them must be a checkout `config/onevcs.rules.yml`
    states a publication path for: another spelling of one the migration covered, or
    a repository registered since under a rule of its own. A checkout of anything
    else has only the reviewed default to fall through to, which `just repos-apply`
    refuses rather than registers.

    Nothing here reads this host's own `$HOME`: the fixture's paths are absolute
    under the home of the host the registry was captured on, and rendering them
    against any other one produces spellings that could never match.
    """
    listed = {
        entry
        for line in TRACKED_CHECKOUTS.read_text(encoding="utf-8").splitlines()
        if (entry := line.partition("#")[0].strip())
    }
    migrated = {entry.path.replace(PRE_ADOPTION_HOME, "~", 1) for entry in CHECKOUTS}
    covered = {which_checkout(entry.path) for entry in CHECKOUTS} | ruled_repositories()

    assert all(entry.startswith("~/") for entry in listed), listed
    assert migrated <= listed, migrated - listed
    unruled = {entry for entry in listed if which_checkout(entry) not in covered}
    assert not unruled, unruled
