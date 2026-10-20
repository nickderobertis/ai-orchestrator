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

The tracked rules and checkout list name public repositories only. A private
repository's rule lives in the host's own overlay, outside every checkout, so every
journey about one installs a host overlay over synthetic identities under a scratch
XDG configuration home and reads the composed policy back through the installed
`onevcs rules check`.

llmlint: ignore-file[e2e_not_mocked] The scratch checkouts stand in for this host's
own clones, which a test may not register or publish from; the recipe, the script,
`onevcs`, and git are all real, and the policy asserted is the one `onevcs` resolved.

llmlint: ignore-file[shell_test_tiers_stay_split] This module is the one journey of `just
repos-apply`, and every journey of that recipe lives here — the ones this change adds
beside the ones the migration wrote — so they select the same tier and read the same
key. Moving the recipe's journeys to a project of their own is a change to `nx.json`,
`orchestrator/project.json` and `tests/nx_inputs.py` together, for every one of them at
once, and is not one more journey's to make.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal, NamedTuple, NotRequired, TypedDict, cast

import pytest
from registered_checkouts import TRACKED_CHECKOUTS, RepoIdentity
from scratch_identity import seeded
from sibling_facts import declaration, default_target_complaint, override_default_targets

from orchestrator.gate_impact import UV_LOCK
from orchestrator.host_installs import AWAITING_FIRST_ADOPTION, INSTALLED, Installed, by_producer
from orchestrator.root import REPO_ROOT

#: The registry this host ran on before `onevcs` owned one, verbatim.
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "pre-adoption-repos.json"
#: The tracked rules file the recipe installs, which decides every listed checkout's
#: publication path — and, since onevcs 0.11.0, nothing else. It names no verifier,
#: because this host runs none.
TRACKED_RULES = REPO_ROOT / "config" / "onevcs.rules.yml"

#: The tracked release override the recipe installs beside the rules file, which
#: decides the rung a node of each repository adopts a dependency's release on and
#: which of a producer's targets a consumer naming none waits for.
TRACKED_RELEASES = REPO_ROOT / "config" / "onevcs.releases.yml"
#: The tracked workspaces file the recipe installs as the fourth, which sizes each
#: identity's pool of warm worktree slots and what a return clears.
TRACKED_WORKSPACES = REPO_ROOT / "config" / "onevcs.workspaces.yml"
#: What that file says every identity no rule names gets, which is the shared default
#: for a pool every host starts from: one warm slot, and sessions past it admitted
#: without bound. A host raises it in its own overlay, never in the tracked file.
SHARED_POOL = 1
SHARED_OVERFLOW = "unlimited"
#: Where the recipe reads a host's overlay from under its XDG configuration home, the
#: directory the Claude identities file lives in. Every journey here points that home
#: at scratch, so the host this suite runs on says nothing into a registry a journey
#: is asserting the tracked file alone reached.
HOST_OVERLAY = Path("ai-orchestrator") / "workspaces.yml"
#: The identity `config/onevcs.releases.yml` gives the one non-default rung, and the
#: rung: a node of this repository waits for a producer's release rather than adopting
#: its branch.
THIS_REPOSITORY: RepoIdentity = "github.com/nickderobertis/ai-orchestrator"
THIS_REPOSITORY_RUNG = "published"
#: The rung every producer resolves — the global one, because the override names none
#: for them — and what `onevcs` answers for a repository the override never mentions.
GLOBAL_RUNG = "fast"

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
#: The identities whose policy differs from every other rule in the tracked file, with
#: the `(publication, approvals)` each one resolves. Unlike the historical golden below,
#: this also covers identities registered after the onevcs adoption.
POLICY_EXCEPTIONS = {
    "github.com/nickderobertis/ai-orchestrator": ("local-direct", "none"),
    "github.com/nickderobertis/spanish-language-tutor": ("local-direct", "none"),
}
#: What every *other* tracked identity publishes under: a single-owner `nickderobertis`
#: repository whose change request merges itself once its merge path passes. The
#: golden covers this for the identities that predate the adoption; a rule written
#: since — `llmlint` was the first, `allowlister-remote` and `cloud-agent-dev-env` the
#: latest — is covered by nothing else, so a new sibling given a policy of its own
#: fails here rather than at its first publication.
#:
#: The tracked file names public repositories only. A team repository whose change
#: request stays ready-for-review, or a private one publishing locally, is ruled by the
#: host's own overlay, which `HOST_RULES_OVERLAY_TEXT` below stands in for.
SIBLING_POLICY = ("change-auto", "none")

#: The identity whose routing the journey below resolves. Spelled out rather than
#: derived from the tracked files, so that what the journey claims to prove is stated
#: independently of the configuration under test.
PRINTOBSERVER: RepoIdentity = "github.com/nickderobertis/printobserver"
#: The team identity whose routing the host-overlay journey resolves, spelled out for
#: the same reason. It is the one whose rule a resolver cannot be taken on trust for:
#: `default:` resolves the reviewed pair too, so a policy read alone would answer
#: identically whether the rule matched or nothing did. Synthetic, and private: no
#: tracked file names it, and its rule exists only in the host overlay below.
QUIETHARBOR: RepoIdentity = "github.com/hiddenco/quietharbor"
#: The pre-adoption registry's one team repository, synthetic and private like the
#: identity above, so its policy too comes from the host overlay alone.
STILLWATER: RepoIdentity = "github.com/hiddenco/stillwater"
#: A private personal repository that publishes locally, ruled by the overlay alone.
PEBBLECRAFT: RepoIdentity = "github.com/nickderobertis/pebblecraft"
#: The public sibling the tracked list and rules were extended with, spelled out for
#: the reason `PRINTOBSERVER` is.
CLOUD_AGENT_DEV_ENV: RepoIdentity = "github.com/nickderobertis/cloud-agent-dev-env"
#: Where the recipe reads a host's rules overlay from under its XDG configuration home.
HOST_RULES = Path("ai-orchestrator") / "onevcs.rules.yml"
#: A host's own rules overlay over the synthetic private identities: the shape a host
#: that also works in private repositories keeps outside every checkout. It is at the
#: schema that admits `visibility`, so the host is never asked about them.
HOST_RULES_OVERLAY_TEXT = (
    "version: 4\n"
    "rules:\n"
    "  - match: {host: github.com, owner: hiddenco, name: quietharbor}\n"
    "    publication: change-open\n"
    "    approvals: required\n"
    "    visibility: private\n"
    "  - match: {host: github.com, owner: hiddenco, name: stillwater}\n"
    "    publication: change-open\n"
    "    approvals: required\n"
    "    visibility: private\n"
    "  - match: {host: github.com, owner: nickderobertis, name: pebblecraft}\n"
    "    publication: local-direct\n"
    "    approvals: none\n"
    "    visibility: private\n"
)


class OverlayPolicy(NamedTuple):
    """What the overlay states for one identity, and the rule position it holds there."""

    publication: str
    approvals: str
    position: int


#: What that overlay states for each identity, restated independently of its text; the
#: composed file keeps each rule's position ahead of every tracked rule.
OVERLAY_POLICY: dict[RepoIdentity, OverlayPolicy] = {
    QUIETHARBOR: OverlayPolicy("change-open", "required", 1),
    STILLWATER: OverlayPolicy("change-open", "required", 2),
    PEBBLECRAFT: OverlayPolicy("local-direct", "none", 3),
}
#: The producer whose override rule precedes its first release, spelled out for the
#: same reason, with the target its plan contracts as the wheel this host will install.
ONEBUDGETSPEC: RepoIdentity = "github.com/nickderobertis/onebudgetspec"
ONEBUDGETSPEC_TARGET = "pypi"
ONEBUDGETSPEC_ARTIFACT = "pypi:onebudgetspec-cli"
#: The single-owner siblings this host installs nothing from and registers so plans can
#: dispatch into them, spelled out for the same reason: the shared pruner each Rust
#: repository's declared pool maintenance runs, and the library answering a GraphQL
#: document's node count and rate-limit points that other repositories' query gates
#: depend on.
PRUNER: RepoIdentity = "github.com/nickderobertis/cargo-prune-superseded"
GRAPHQL_NODE_COUNT: RepoIdentity = "github.com/nickderobertis/github-graphql-node-count"

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


def declaring_checkout(directory: Path, origin: str, targets: dict[str, str]) -> Path:
    """A checkout of `origin` whose base declares each ``name: id`` of ``targets``.

    Committed on `main` rather than left in the working tree, because `onevcs` reads a
    producer's declaration at the publication checkout's base and nowhere else. What it
    declares is what this repository decides of the producer — the target the override
    names, under the artifact the table installs — beside one target nothing here names,
    because a producer declares more than this host consumes; never a copy of the real
    producer's declaration, which is that repository's to move.
    """
    directory = checkout(directory, origin)
    (directory / "release-targets.toml").write_text(
        declaration({**targets, "crate": f"crate:{directory.name}"}), encoding="utf-8"
    )
    committer = ["-c", "user.email=t@example.com", "-c", "user.name=Test"]
    subprocess.run(["git", "add", "-A"], cwd=directory, check=True)
    subprocess.run(
        ["git", *committer, "commit", "-qm", "chore: declare"], cwd=directory, check=True
    )
    return directory


def apply_registry(
    manifest: Path,
    home: Path,
    *arguments: str,
    config_home: Path | None = None,
    temporary: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real recipe against a scratch registry root.

    ``config_home`` is the XDG configuration home the recipe resolves the host's
    workspaces and rules overlays under; a scratch sibling of ``home`` holding nothing
    when a journey names none, so the tracked files are installed alone whatever this
    host has. ``temporary``, when named, is the recipe's ``TMPDIR``, for a journey that
    reads what a run left there.
    """
    if config_home is None:
        config_home = home.parent / f"{home.name}-config"
    config_home.mkdir(parents=True, exist_ok=True)
    scratch = {"TMPDIR": str(temporary)} if temporary is not None else {}
    return subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), *arguments],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "ONEVCS_HOME": str(home),
            "XDG_CONFIG_HOME": str(config_home),
            **scratch,
        },
        text=True,
        capture_output=True,
    )


def host_config(config_home: Path, rules: str = HOST_RULES_OVERLAY_TEXT) -> Path:
    """An XDG configuration home holding a host rules overlay, and nothing else."""
    (config_home / HOST_RULES).parent.mkdir(parents=True, exist_ok=True)
    (config_home / HOST_RULES).write_text(rules, encoding="utf-8")
    return config_home


def onevcs(
    home: Path, *arguments: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--project", str(REPO_ROOT), "onevcs", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        input=stdin,
        capture_output=True,
    )


def visibility(home: Path, identity: RepoIdentity) -> str:
    """What `onevcs boundary inspect` answers for ``identity`` under ``home``."""
    asked = onevcs(
        home,
        "boundary",
        "inspect",
        "--input",
        "-",
        stdin=json.dumps({"repository": identity}),
    )
    assert asked.returncode == 0, asked.stdout + asked.stderr
    return cast(str, json.loads(asked.stdout)["visibility"])


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


def registered_paths(home: Path) -> set[str]:
    """Every checkout path the registry under ``home`` holds; none when it has no registry."""
    document = home / "registry.json"
    if not document.exists():
        return set()
    registry: RegistryDocument = json.loads(document.read_text(encoding="utf-8"))
    return {record["path"] for record in registry["checkouts"].values()}


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
    """The pre-adoption registry's checkouts, registered by the real recipe.

    The registry held one private team repository, whose rule no tracked file carries
    any longer: it comes from the host's own rules overlay, installed here under a
    scratch XDG configuration home, so every pre-adoption row is still asserted against
    what `onevcs` resolves — the public ones from the tracked rules, the private one
    from the overlay composed over them.
    """
    root = tmp_path_factory.mktemp("repo-registry")
    paths = [
        checkout(root / "checkouts" / Path(entry.path).name, BY_URL[entry.identity].origin)
        for entry in CHECKOUTS
    ]
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    result = apply_registry(manifest, home, config_home=host_config(root / "xdg"))
    assert result.returncode == 0, result.stdout + result.stderr
    return Applied(home=home, manifest=manifest, result=result)


@pytest.fixture(scope="module")
def overlaid(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A registry holding a checkout of every identity the host overlay names.

    Beside one tracked identity of each publication the tracked file routes, so the
    composition is read from both sides: the overlay's identities resolve its rules,
    and the tracked ones still resolve theirs.
    """
    root = tmp_path_factory.mktemp("overlaid-registry")
    keys = (*OVERLAY_POLICY, THIS_REPOSITORY, "github.com/nickderobertis/onevcs")
    paths = [
        checkout(root / "checkouts" / key.rpartition("/")[2], f"https://{key}") for key in keys
    ]
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    result = apply_registry(manifest, home, config_home=host_config(root / "xdg"))
    assert result.returncode == 0, result.stdout + result.stderr
    return home


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
    assert "stillwater" in applied.result.stdout
    assert "change-open" in applied.result.stdout
    overlay = applied.manifest.parent / "xdg" / HOST_RULES
    assert (
        f"rules      the rules composed from 2 file(s) are at {applied.home / 'rules.yml'}"
        f"  (from {TRACKED_RULES} overlaid by {overlay})"
    ) in applied.result.stdout, applied.result.stdout


def test_apply_installs_the_tracked_release_override_and_says_so(applied: Applied) -> None:
    """The third tracked file, installed whole beside the rules file and reported."""
    installed = applied.home / "releases.yml"
    assert installed.read_bytes() == TRACKED_RELEASES.read_bytes()
    assert f"releases   installed  {installed}" in applied.result.stdout


def test_apply_installs_the_tracked_workspaces_file_and_the_pool_resolves_from_it(
    applied: Applied,
) -> None:
    """The fourth tracked file, installed whole and read back through the verb that owns it.

    Installed bytes prove the copy; `onevcs pool status` proves the install is the one
    a session open resolves its placement out of, answering this host's default for an
    identity the file's rules do not name.
    """
    installed = applied.home / "workspaces.yml"
    assert installed.read_bytes() == TRACKED_WORKSPACES.read_bytes()
    assert (
        f"workspaces installed  {installed}  (from {TRACKED_WORKSPACES})" in applied.result.stdout
    )
    status = onevcs(applied.home, "pool", "status", "stillwater", "--json")
    assert status.returncode == 0, status.stdout + status.stderr
    capacity = json.loads(status.stdout)["capacity"]
    assert capacity["pool"] == SHARED_POOL, capacity
    assert capacity["overflow"] == SHARED_OVERFLOW, capacity


#: A host's overlay: a bigger default pool than the shared one, a rule of its own for an
#: identity the tracked file has none for, and then a rule that replaces the tracked rule
#: with the same `match` — `ai-orchestrator`'s, which deletes `.logs/` on every return —
#: so the host says what that rule is instead.
HOST_OVERLAY_TEXT = (
    "version: 1\n"
    "default:\n"
    "  pool: 3\n"
    "rules:\n"
    "  - match: {host: github.com, owner: nickderobertis, name: onevcs}\n"
    "    pool: 2\n"
    "  - match: {host: github.com, owner: nickderobertis, name: ai-orchestrator}\n"
    "    pool: 4\n"
    "    overflow: 2\n"
)


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] An existing journey of `just repos-apply`, retargeted at the one tracked rule left; every journey of that recipe lives in this module, and the module docstring's ignore of `shell_test_tiers_stay_split` says why moving them to a project of their own is a change for all of them at once rather than this edit's.  # noqa: E501
def test_the_hosts_overlay_is_composed_onto_the_tracked_file_and_installed(
    tmp_path: Path,
) -> None:
    """With a host file under the XDG home, the installed file is the tracked one overlaid.

    Read back through `onevcs pool status`, which resolves the installed document: the
    ruled identity answers the host's rule, an identity the host did not rule answers
    the host's default over the tracked one, and the report says both sources. The
    tracked file itself is untouched, which is the point of the overlay.
    """
    present = checkout(
        tmp_path / "ai-orchestrator", "https://github.com/nickderobertis/ai-orchestrator.git"
    )
    other = checkout(tmp_path / "onejudge", "https://github.com/nickderobertis/onejudge.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n{other}\n", encoding="utf-8")
    config_home = tmp_path / "xdg"
    (config_home / HOST_OVERLAY).parent.mkdir(parents=True)
    (config_home / HOST_OVERLAY).write_text(HOST_OVERLAY_TEXT, encoding="utf-8")
    home = tmp_path / "home"
    tracked_before = TRACKED_WORKSPACES.read_bytes()

    result = apply_registry(manifest, home, config_home=config_home)

    assert result.returncode == 0, result.stdout + result.stderr
    installed = home / "workspaces.yml"
    assert (
        f"workspaces installed  {installed}  (from {TRACKED_WORKSPACES} overlaid by "
        f"{config_home / HOST_OVERLAY})"
    ) in result.stdout
    assert TRACKED_WORKSPACES.read_bytes() == tracked_before
    assert installed.read_bytes() != tracked_before
    ruled = json.loads(onevcs(home, "pool", "status", "ai-orchestrator", "--json").stdout)
    assert (ruled["capacity"]["pool"], ruled["capacity"]["overflow"]) == (4, 2), ruled
    unruled = json.loads(onevcs(home, "pool", "status", "onejudge", "--json").stdout)["capacity"]
    assert (unruled["pool"], unruled["overflow"]) == (3, SHARED_OVERFLOW), unruled
    # The rule replaced the tracked one in place rather than being appended after the
    # host's own new rule, so first-match-wins still reads the file in the tracked order.
    text = installed.read_text(encoding="utf-8")
    assert text.count("name: ai-orchestrator}") == 1, text
    assert text.index("name: ai-orchestrator}") < text.index("name: onevcs}"), text

    again = apply_registry(manifest, home, config_home=config_home)

    assert again.returncode == 0, again.stdout + again.stderr
    assert "workspaces unchanged" in again.stdout


def test_a_host_overlay_the_composer_refuses_installs_nothing(tmp_path: Path) -> None:
    """A host file at another schema version is refused by name before validation."""
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    config_home = tmp_path / "xdg"
    (config_home / HOST_OVERLAY).parent.mkdir(parents=True)
    (config_home / HOST_OVERLAY).write_text("version: 2\ndefault:\n  pool: 3\n", encoding="utf-8")
    home = tmp_path / "home"

    result = apply_registry(manifest, home, config_home=config_home)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "declares version 2, and the tracked one 1" in result.stderr, result.stderr
    assert f"{config_home / HOST_OVERLAY} cannot be overlaid on" in result.stderr, result.stderr
    assert not (home / "workspaces.yml").exists()
    assert not (home / "rules.yml").exists()


class Producers(NamedTuple):
    """A scratch registry holding one declaring checkout per producer this host installs."""

    home: Path
    result: subprocess.CompletedProcess[str]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Nine `git init`s and
# one registry apply — the cost class of the `applied` and `ruled` fixtures above, which
# register thirteen and twenty-five — in the module every journey of this recipe lives
# in; the project edge those would all sit behind is the module docstring's follow-up.
# Block-scoped because the rule reads the fixture's body, not its decorator line.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] The edge this fixture
# would sit behind is the one the module docstring's `shell_test_tiers_stay_split` note
# defers: a project of this recipe's own is a change to `nx.json`,
# `orchestrator/project.json` and `tests/nx_inputs.py` for all three module fixtures and
# every journey here at once, and one more fixture of the same cost class in the same
# module is not the change that makes it.
@pytest.fixture(scope="module")
def producers(tmp_path_factory: pytest.TempPathFactory) -> Producers:
    """Every producer identity, declaring the target this host installs, plus this repository.

    Registered from checkouts whose base carries a scratch declaration, because what
    `onevcs release targets` answers for a `default_target` depends on the producer
    declaring that target: a producer whose declaration cannot be read is *refused*
    for naming one, not answered without it, so a registry of empty checkouts would
    prove nothing about the override.
    """
    root = tmp_path_factory.mktemp("producers")
    paths = [
        declaring_checkout(
            root / "checkouts" / row.producer.rpartition("/")[2],
            f"https://{row.producer}.git",
            {row.target: row.artifact},
        )
        for row in INSTALLED
    ]
    paths.append(checkout(root / "checkouts" / "ai-orchestrator", f"https://{THIS_REPOSITORY}.git"))
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    result = apply_registry(manifest, home)
    assert result.returncode == 0, result.stdout + result.stderr
    return Producers(home=home, result=result)


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


class Probe(TypedDict):
    """A declared target's probe: the declaration's script, given the target's id."""

    script: str
    args: list[str]


class Target(TypedDict):
    name: str
    style: str
    probe: Probe


class ReleaseTargets(TypedDict):
    """The `onevcs release targets --json` answer, as far as these journeys read it.

    `default_target` is present only where the override resolves one, which is itself
    asserted on: a repository the override gives no default must answer without the key.
    """

    identity: str
    adoption: str
    default_target: NotRequired[str]
    targets: list[Target]


def release_targets(home: Path, identity: str) -> ReleaseTargets:
    """What `onevcs release targets --json` answers for ``identity`` under ``home``.

    Cast rather than validated, because the installed `onevcs`'s own answer is the
    subject: a response that lost one of these keys fails the journey as the drift it
    is, with the read naming the key.
    """
    asked = onevcs(home, "release", "targets", identity, "--json")
    assert asked.returncode == 0, asked.stdout + asked.stderr
    return cast(ReleaseTargets, json.loads(asked.stdout))


@pytest.mark.parametrize("row", INSTALLED, ids=lambda row: row.producer)
def test_each_producer_resolves_the_wheel_this_host_installs_as_its_default(
    producers: Producers, row: Installed
) -> None:
    """The override's answer per producer, read through the verb a dispatch resolves it with.

    Three things per row: the default target is the row's, it is a target the
    declaration carries under the row's artifact id, and the producer sits on the
    global rung — the override names no rung for a producer, so one resolving anything
    else has been given a rule this file does not state.
    """
    answer = release_targets(producers.home, row.producer)
    assert answer["default_target"] == row.target, answer
    assert answer["adoption"] == GLOBAL_RUNG, answer
    declared = {target["name"]: target["probe"]["args"][0] for target in answer["targets"]}
    assert declared[row.target] == row.artifact, declared


def test_this_repository_resolves_the_published_rung(producers: Producers) -> None:
    """A node of this repository waits for a producer's release, by the override alone.

    It declares nothing and names no default target — only the rung, because its
    `local-direct` policy refuses the draft a `fast` node behind a release would open.
    """
    answer = release_targets(producers.home, THIS_REPOSITORY)
    assert answer["adoption"] == THIS_REPOSITORY_RUNG, answer
    assert "default_target" not in answer, answer
    assert answer["targets"] == [], answer


def test_apply_reports_what_each_installed_producer_resolves(producers: Producers) -> None:
    """Below the policy table, one line per producer this host installs, as read back.

    An operator applying the configuration reads what a consumer will get from it, and
    the lines are read back through `onevcs release targets` rather than composed from
    the file, so a producer the override describes wrongly reads wrongly here.
    """
    stdout = producers.result.stdout
    table = stdout.index("apply-repo-registry: release adoption")
    assert table > stdout.index("apply-repo-registry: resolved policy")
    reported = stdout[table:]
    for row in INSTALLED:
        line = next(line for line in reported.splitlines() if row.producer in line)
        assert re.search(rf"default target {re.escape(row.target)}\s", line), line
        assert line.rstrip().endswith(f"adoption {GLOBAL_RUNG}"), line


def test_a_producer_this_registry_does_not_hold_is_reported_not_failed(
    applied: Applied,
) -> None:
    """A host holding a subset of the producers reads which ones it lacks.

    The `applied` registry holds the pre-adoption checkouts, none of which carries a
    declaration and one of which — `onetaskgraph` — is not among them at all, so the
    table below the policy one names each line's state rather than the apply failing
    for a checkout's momentary shape: an unregistered producer as not registered, and a
    registered one whose base declares nothing as unresolved with `onevcs`'s own reason.
    """
    reported = applied.result.stdout[applied.result.stdout.index("release adoption") :]
    assert re.search(r"github\.com/nickderobertis/onetaskgraph\s+not registered here", reported)
    assert re.search(r"github\.com/nickderobertis/onepipeline\s+unresolved: ", reported)
    assert applied.result.returncode == 0


def test_a_scratch_identity_seeds_beside_the_override_untouched_by_it(tmp_path: Path) -> None:
    """`tests/e2e/scratch_identity.py` registers through the same recipe, and still does.

    Every journey that publishes into a seeded identity registers it this way, so the
    override installed beside its rules has to leave a path identity exactly as it was:
    no rule matches it, so it resolves the global rung, no default target, no targets.
    """
    identity = seeded(tmp_path)

    assert (identity.home / "releases.yml").read_bytes() == TRACKED_RELEASES.read_bytes()
    answer = release_targets(identity.home, str(identity.publication))
    assert answer["adoption"] == GLOBAL_RUNG, answer
    assert "default_target" not in answer, answer
    assert answer["targets"] == [], answer


def test_a_different_release_override_is_installed_in_place_of_the_tracked_one(
    tmp_path: Path,
) -> None:
    """`--releases FILE` installs FILE, observed as the installed bytes becoming its own."""
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    candidate = tmp_path / "candidate.yml"
    candidate.write_text(
        "version: 1\ndefault:\n  adoption: published\nrepositories: []\n", encoding="utf-8"
    )
    home = tmp_path / "home"

    result = apply_registry(manifest, home, "--releases", str(candidate))

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / "releases.yml").read_bytes() == candidate.read_bytes()
    assert (home / "releases.yml").read_bytes() != TRACKED_RELEASES.read_bytes()
    answer = release_targets(home, "github.com/nickderobertis/onevcs")
    assert answer["adoption"] == "published", answer


#: A release override the adopted `onevcs` loads, standing in for whatever a host
#: already has installed when a replacement is refused or fails.
INSTALLED_RELEASES = "version: 1\ndefault:\n  adoption: fast\nrepositories: []\n"
#: A workspaces file the adopted `onevcs` loads, pooling nothing — the shipped default
#: written out — standing in for whatever a host already has installed.
INSTALLED_WORKSPACES = "version: 1\ndefault:\n  pool: 0\n  overflow: unlimited\nrules: []\n"


def test_a_different_workspaces_file_is_installed_in_place_of_the_tracked_one(
    tmp_path: Path,
) -> None:
    """`--workspaces FILE` installs FILE, observed through the pool the identity then resolves."""
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    candidate = tmp_path / "candidate.yml"
    candidate.write_text(
        "version: 1\ndefault:\n  pool: 3\n  overflow: 1\nrules: []\n", encoding="utf-8"
    )
    home = tmp_path / "home"

    result = apply_registry(manifest, home, "--workspaces", str(candidate))

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / "workspaces.yml").read_bytes() == candidate.read_bytes()
    assert (home / "workspaces.yml").read_bytes() != TRACKED_WORKSPACES.read_bytes()
    status = onevcs(home, "pool", "status", "onevcs", "--json")
    assert status.returncode == 0, status.stdout + status.stderr
    capacity = json.loads(status.stdout)["capacity"]
    assert (capacity["pool"], capacity["overflow"]) == (3, 1), capacity


def test_a_workspaces_file_onevcs_refuses_leaves_the_installed_one_intact(
    tmp_path: Path,
) -> None:
    """A candidate `onevcs` cannot load is refused by name, and replaces nothing.

    Validated in the scratch home the rules and the override already are, through
    `pool status` — the one read that loads the document whole — so a default that
    admits no session at all is refused before the live copy is touched.
    """
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / "rules.yml").write_text(INSTALLED_RULES, encoding="utf-8")
    (home / "releases.yml").write_text(INSTALLED_RELEASES, encoding="utf-8")
    installed = home / "workspaces.yml"
    installed.write_text(INSTALLED_WORKSPACES, encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text(
        "version: 1\ndefault:\n  pool: 0\n  overflow: 0\nrules: []\n", encoding="utf-8"
    )

    result = apply_registry(manifest, home, "--workspaces", str(invalid))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{invalid} is not a valid onevcs workspaces file" in result.stderr
    assert "admits no session at all" in result.stderr, result.stderr
    assert installed.read_text(encoding="utf-8") == INSTALLED_WORKSPACES
    assert "workspaces installed" not in result.stdout


def test_an_override_onevcs_refuses_leaves_the_installed_one_intact(tmp_path: Path) -> None:
    """A candidate `onevcs` cannot load is refused by name, and replaces nothing.

    Validated in the scratch home the rules already are, through `release targets` —
    the one verb that loads the document — so a malformed rung or a `default_target`
    naming no target is refused before the live copy is touched.
    """
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / "rules.yml").write_text(INSTALLED_RULES, encoding="utf-8")
    installed = home / "releases.yml"
    installed.write_text(INSTALLED_RELEASES, encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text(
        "version: 1\ndefault:\n  adoption: bogus\nrepositories: []\n", encoding="utf-8"
    )

    result = apply_registry(manifest, home, "--releases", str(invalid))

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{invalid} is not a valid onevcs release override" in result.stderr
    assert "unknown variant `bogus`" in result.stderr, result.stderr
    assert installed.read_text(encoding="utf-8") == INSTALLED_RELEASES
    assert "releases   installed" not in result.stdout


def test_a_replacement_that_fails_after_validation_leaves_the_installed_copy_intact(
    tmp_path: Path,
) -> None:
    """The install is a rename of a staged copy, so a failure part-way truncates nothing.

    The destination is made unwritable after the candidates have been validated — the
    registry root itself, since every staged copy and its rename live in it — and what
    is asserted is each previous install byte for byte, not merely present. The rules
    are the first install, by `onevcs rules apply`, so that is where the run stops,
    naming it, with the release override behind it never touched.
    """
    manifest = tmp_path / "checkouts"
    manifest.write_text("", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    rules = home / "rules.yml"
    rules.write_text(INSTALLED_RULES, encoding="utf-8")
    installed = home / "releases.yml"
    installed.write_text(INSTALLED_RELEASES, encoding="utf-8")
    home.chmod(0o555)
    try:
        result = apply_registry(manifest, home)
    finally:
        home.chmod(0o755)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"installing the rules from {TRACKED_RULES} failed" in result.stderr, result.stderr
    assert f"cannot write {home}" in result.stderr, result.stderr
    assert rules.read_text(encoding="utf-8") == INSTALLED_RULES
    assert installed.read_text(encoding="utf-8") == INSTALLED_RELEASES
    assert "releases   installed" not in result.stdout
    assert sorted(path.name for path in home.iterdir()) == ["releases.yml", "rules.yml"]


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
    """The one identity where a wrong answer merges unreviewed work into a team's base.

    Its rule is the host overlay's, so what is asserted beside the policy is the rule
    that decided — the overlay's, ahead of every tracked rule — because `default:`
    resolves the same reviewed pair and a policy read alone could not tell them apart.
    """
    checked = onevcs(applied.home, "rules", "check", STILLWATER)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert reported(checked.stdout, "publication") == "change-open"
    assert reported(checked.stdout, "approvals") == "required"
    host, owner, name = STILLWATER.split("/")
    position = OVERLAY_POLICY[STILLWATER].position
    expected = f"rule {position} {{host: {host}, owner: {owner}, name: {name}}}"
    assert reported(checked.stdout, "matched") == expected, checked.stdout


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


@pytest.mark.parametrize("identity", sorted(OVERLAY_POLICY))
def test_each_overlay_identity_resolves_the_overlays_rule_and_is_private(
    overlaid: Path, identity: RepoIdentity
) -> None:
    """A private identity is routed by the host overlay alone, and the host is never asked.

    Read back through the installed `onevcs rules check`, so what is asserted is the
    composed file the recipe installed: the overlay's rule decides, at its own position
    ahead of the tracked rules, with the publication and approvals it declares — a
    local-first private repository included — and `onevcs boundary inspect` answers
    `private` off the overlay's `visibility` rather than off a host it would have to ask.
    """
    publication, approvals, position = OVERLAY_POLICY[identity]
    checked = onevcs(overlaid, "rules", "check", identity)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert reported(checked.stdout, "publication") == publication, checked.stdout
    assert reported(checked.stdout, "approvals") == approvals, checked.stdout
    host, owner, name = identity.split("/")
    expected = f"rule {position} {{host: {host}, owner: {owner}, name: {name}}}"
    assert reported(checked.stdout, "matched") == expected, checked.stdout
    assert reported(checked.stdout, "rules") == str(overlaid / "rules.yml"), checked.stdout
    assert visibility(overlaid, identity) == "private"


def test_the_overlay_leaves_a_local_and_a_sibling_tracked_rule_as_the_file_states_them(
    overlaid: Path,
) -> None:
    """Composition adds the host's rules and moves neither tracked policy kind.

    One identity of each policy the tracked file states — this repository's local one and
    a sibling's change-request one — keeps that policy, matched from its own tracked rule
    shifted by exactly the overlay's rules.
    """
    for identity in (THIS_REPOSITORY, "github.com/nickderobertis/onevcs"):
        checked = onevcs(overlaid, "rules", "check", identity)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        resolved = (reported(checked.stdout, "publication"), reported(checked.stdout, "approvals"))
        assert resolved == POLICY_EXCEPTIONS.get(identity, SIBLING_POLICY), checked.stdout
        position = ruled_identities().index(identity) + 1 + len(OVERLAY_POLICY)
        assert reported(checked.stdout, "matched").startswith(f"rule {position} "), checked.stdout


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] Keeps shared fixture ownership.
def test_the_host_uses_the_shipped_base_merge_and_check_wait_defaults(
    ruled: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed rules resolve defaults through the pinned CLI, without a host override."""
    monkeypatch.delenv("ONEVCS_CHECKS_TIMEOUT_SECONDS", raising=False)
    observer = onevcs(ruled, "rules", "check", PRINTOBSERVER)
    assert observer.returncode == 0, observer.stderr
    assert reported(observer.stdout, "base merge") == "on-conflict"
    assert reported(observer.stdout, "checks wait") == "3600 s"
    local = onevcs(ruled, "rules", "check", THIS_REPOSITORY)
    assert local.returncode == 0, local.stderr
    assert reported(local.stdout, "base merge") == "always"


def test_the_optional_base_merge_and_wait_keys_are_accepted_and_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header's optional keys work through the CLI, and malformed policy is refused.

    Each candidate goes through the real recipe as its base rules file, because the
    verb it installs with is the one that validates it: a malformed `base_merge` is
    refused by name and leaves the configured install resolving exactly as before.
    """
    monkeypatch.delenv("ONEVCS_CHECKS_TIMEOUT_SECONDS", raising=False)
    observer = checkout(tmp_path / "printobserver", f"https://{PRINTOBSERVER}.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{observer}\n", encoding="utf-8")
    home = tmp_path / "onevcs"
    configured = tmp_path / "configured.yml"
    configured.write_text(
        TRACKED_RULES.read_text(encoding="utf-8").replace(
            "    publication: change-auto",
            "    publication: change-auto\n"
            "    base_merge: always\n"
            "    checks: {wait_seconds: 7200}",
        ),
        encoding="utf-8",
    )
    applied = apply_registry(manifest, home, "--rules", str(configured))
    assert applied.returncode == 0, applied.stdout + applied.stderr
    resolved = onevcs(home, "rules", "check", PRINTOBSERVER)
    assert resolved.returncode == 0, resolved.stderr
    assert reported(resolved.stdout, "base merge") == "always"
    assert reported(resolved.stdout, "checks wait") == "7200 s"
    installed = (home / "rules.yml").read_bytes()
    invalid = tmp_path / "invalid.yml"
    invalid.write_text(
        configured.read_text(encoding="utf-8").replace("base_merge: always", "base_merge: invalid"),
        encoding="utf-8",
    )
    refused = apply_registry(manifest, home, "--rules", str(invalid))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "invalid" in refused.stderr
    assert f"{invalid} is not a valid onevcs rules composition" in refused.stderr
    assert (home / "rules.yml").read_bytes() == installed
    recovered = onevcs(home, "rules", "check", PRINTOBSERVER)
    assert recovered.returncode == 0, recovered.stderr
    assert reported(recovered.stdout, "checks wait") == "7200 s"


class DraftLifecycle(NamedTuple):
    """What `onevcs rules check` resolves for one identity's draft lifecycle."""

    #: Whether a publication opens its change request as a draft: `on` or `off`.
    drafts: str
    #: What a green draft becomes: `lift`, `keep for review`, or `not applicable`.
    green_draft: str


#: What the pinned `onevcs` resolves the installed rules into, per kind of identity.
#: One identity of each publication they route — a single-owner `change-auto` sibling
#: and this repository from the tracked file, and a team repository from the host
#: overlay composed over it.
DRAFT_LIFECYCLE: dict[RepoIdentity, DraftLifecycle] = {
    "github.com/nickderobertis/onevcs": DraftLifecycle("on", "lift"),
    STILLWATER: DraftLifecycle("on", "keep for review"),
    "github.com/nickderobertis/ai-orchestrator": DraftLifecycle("on", "not applicable"),
}


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501 - llmlint reads a directive's rule list off one line
# Every journey of `just repos-apply` lives in this module, in the tier it already runs in,
# and shares its `overlaid` fixture, as the module's file-scoped reason says; giving them
# a project of their own is one change to the project graph for all of them at once, not
# one more journey's to make.
@pytest.mark.parametrize(("key", "expected"), sorted(DRAFT_LIFECYCLE.items()))
def test_each_identity_resolves_the_intended_draft_lifecycle(
    overlaid: Path, key: RepoIdentity, expected: DraftLifecycle
) -> None:
    """A green change is lifted, kept for its user's review, or never a change at all.

    The publication lifecycle itself is the engines' to prove, against their own
    testing hosts; what this host owns is that the rules file it installs reads into
    the behaviour the user asked for. A team repository's green draft has to stay a
    draft until its own user has looked at it, a sibling that merges automatically has
    to be lifted so it can, and a `local-direct` identity opens no change request to
    draft. An `onevcs` that predates the lifecycle prints neither line, so this fails
    there rather than passing on a silence.
    """
    checked = onevcs(overlaid, "rules", "check", key)
    assert checked.returncode == 0, checked.stdout + checked.stderr

    resolved = DraftLifecycle(
        drafts=reported(checked.stdout, "drafts"),
        green_draft=reported(checked.stdout, "green draft"),
    )
    assert resolved == expected, checked.stdout


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501


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

    At `version: 3` a rule is `{publication, approvals}` — with an optional `drafts:`
    since onevcs 0.36.0 — and `gate:` is an unknown field, so a rules file carrying one
    does not load. Driven through the real recipe because that is where an operator
    meets it: an apply that installed a file
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
    assert f"{gated} is not a valid onevcs rules composition" in result.stderr
    assert "gate" in result.stderr, result.stderr
    assert not (tmp_path / "home" / "rules.yml").exists()
    assert registered_paths(tmp_path / "home") == set()


# llmlint: ignore[tests_mirror_real_usage] An unmigrated host's rules must be seeded directly.
def test_a_gate_at_an_older_schema_version_is_accepted_ignored_and_reported(
    tmp_path: Path,
) -> None:
    """The other half: an unmigrated host keeps publishing, and is told why once.

    This is the measurement that makes the removal real rather than nominal on this
    host. The same `gate: ["false"]` that an `onevcs` before 0.11.0 would have run and
    been refused by is now read, dropped, and named on stderr — so the *engine*, not
    just this repository's file, is what stopped running a tier of its own.

    The unmigrated file is the one a host already has installed, which is where the
    engine meets it: written at the registry's own rules reference after a real
    registration. Applying it afresh is a different question, and `onevcs rules apply`
    answers it by refusing the gate by name and installing nothing, so the recipe
    never installs a gate even at a schema the engine still reads.
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
    result = apply_registry(manifest, home)
    assert result.returncode == 0, result.stdout + result.stderr

    refused = apply_registry(manifest, home, "--rules", str(legacy))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "gate" in refused.stderr, refused.stderr
    assert f"{legacy} is not a valid onevcs rules composition" in refused.stderr
    assert (home / "rules.yml").read_text(encoding="utf-8") != legacy.read_text(encoding="utf-8")

    # An unmigrated host: the installed file is the older schema, gate and all.
    shutil.copyfile(legacy, home / "rules.yml")
    checked = onevcs(home, "rules", "check", "github.com/nickderobertis/onevcs")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "gate" not in {
        line.partition(": ")[0].strip() for line in checked.stdout.splitlines() if ": " in line
    }, checked.stdout
    assert reported(checked.stdout, "publication") == "change-auto"
    assert "version 3 removed" in checked.stderr, checked.stderr
    assert str(home / "rules.yml") in checked.stderr, checked.stderr


#: The fields `onevcs` stamps on an identity's record when it observes the identity's
#: visibility — off a rule's `visibility`, for an identity the host overlay rules.
OBSERVATION_FIELDS = ("visibility", "observation", "observed_at")


def registry_without(document: bytes, fields: tuple[str, ...]) -> dict[str, object]:
    """The registry ``document`` with ``fields`` dropped from every identity's record."""
    registry = json.loads(document)
    for record in registry["identities"].values():
        for field in fields:
            record.pop(field, None)
    return cast(dict[str, object], registry)


def test_reapplying_the_configuration_changes_nothing(applied: Applied) -> None:
    """Idempotence, which is what makes this a recipe to re-run rather than a migration.

    Every identity and checkout the first run registered is registered again exactly as
    it was. The one movement is `onevcs`'s own: a re-registration of an identity whose
    rule declares its `visibility` records that observation, stamped with when it was
    made, so the private identity's record gains the observation on the second run and
    re-stamps only its time on every run after; everything else is byte for byte.
    """
    document = applied.home / "registry.json"
    before = document.read_bytes()
    override = applied.home / "releases.yml"
    override_before = override.read_bytes()
    override_installed_at = override.stat().st_mtime_ns
    rules_before = (applied.home / "rules.yml").read_bytes()
    config_home = applied.manifest.parent / "xdg"
    again = apply_registry(applied.manifest, applied.home, config_home=config_home)
    assert again.returncode == 0, again.stdout + again.stderr
    second = document.read_bytes()
    assert registry_without(second, OBSERVATION_FIELDS) == json.loads(before)
    observed = json.loads(second)["identities"]
    stamped = {key for key, record in observed.items() if "observed_at" in record}
    assert stamped == set(OVERLAY_POLICY) & {identity.key for identity in IDENTITIES}, observed
    assert {observed[key]["visibility"] for key in stamped} == {"private"}, observed
    third = apply_registry(applied.manifest, applied.home, config_home=config_home)
    assert third.returncode == 0, third.stdout + third.stderr
    assert registry_without(document.read_bytes(), ("observed_at",)) == registry_without(
        second, ("observed_at",)
    )
    # The rules are re-composed and re-installed by `onevcs rules apply` on every run,
    # so what stays the same is the installed document, not a line saying so.
    installed_rules = applied.home / "rules.yml"
    assert (
        f"rules      the rules composed from 2 file(s) are at {installed_rules}" in again.stdout
    ), again.stdout
    assert installed_rules.read_bytes() == rules_before
    assert "releases   unchanged" in again.stdout
    assert "workspaces unchanged" in again.stdout
    assert override.read_bytes() == override_before
    assert override.stat().st_mtime_ns == override_installed_at, "the override was rewritten"
    assert (applied.home / "workspaces.yml").read_bytes() == TRACKED_WORKSPACES.read_bytes()


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
    assert "releases   would install" in result.stdout
    assert "workspaces would install" in result.stdout
    assert "dry run — 1 checkout(s) would be registered, 0 skipped" in result.stdout
    assert not registry_home.exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--checkouts",), "usage: apply-repo-registry.sh"),
        (("--rules",), "usage: apply-repo-registry.sh"),
        (("--releases",), "usage: apply-repo-registry.sh"),
        (("--workspaces",), "usage: apply-repo-registry.sh"),
        (("--workspaces", "/does/not/exist"), "does not exist"),
        (("--unknown",), "unknown argument"),
        (("--checkouts", "/does/not/exist"), "does not exist"),
        (("--releases", "/does/not/exist"), "does not exist"),
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
    assert f"the registry at {home / 'registry.json'}" in result.stderr, result.stderr
    assert (home / "registry.json").read_text(encoding="utf-8") == "not json"
    assert not (home / "rules.yml").exists()


#: A rules file the adopted `onevcs` loads, standing in for whatever a host already has
#: installed when an invalid replacement is refused.
INSTALLED_RULES = (
    "version: 3\ntrailer_prefix: Orchestrator-\nrules: []\n"
    "default:\n  publication: change-open\n  approvals: required\n"
)


def test_invalid_rules_do_not_replace_the_installed_rules(tmp_path: Path) -> None:
    """A refused rules file leaves the one already installed exactly as it was.

    The installed stand-in has to be a rules file the pinned `onevcs` itself accepts,
    because registration reads it before the supplied file is ever validated: onevcs
    0.20.0 resolves an identity's publication policy from the rules at `register`, and
    a file with no `default:` is refused there as malformed — so a stand-in written as
    the bare `version: 2` / `rules: []` of earlier releases fails the recipe one step
    before the refusal this journey is about, and proves nothing about replacement.
    """
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

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{invalid} is not a valid onevcs rules composition" in result.stderr
    assert installed.read_text(encoding="utf-8") == INSTALLED_RULES
    assert registered_paths(home) == set()


def test_an_invalid_host_rules_overlay_installs_nothing_and_registers_nothing(
    tmp_path: Path,
) -> None:
    """A host overlay the verb refuses is refused by name before anything moves.

    The overlay is composed and validated with the tracked base by `onevcs rules
    apply --dry-run` before the first registration, so an overlay that cannot be read
    — or one naming a field its schema does not admit — leaves the installed rules
    exactly as they were and the registry holding no checkout at all. The host's
    workspaces overlay beside it has been composed into a scratch file by then, and the
    refusal leaves nothing in the temporary directory it was handed.
    """
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    installed = home / "rules.yml"
    installed.write_text(INSTALLED_RULES, encoding="utf-8")
    for overlay in (
        "not: [valid",
        # `visibility` is a version-4 field, so a version-3 overlay naming it is refused.
        HOST_RULES_OVERLAY_TEXT.replace("version: 4", "version: 3"),
    ):
        config_home = host_config(tmp_path / "xdg", overlay)
        (config_home / HOST_OVERLAY).write_text(HOST_OVERLAY_TEXT, encoding="utf-8")
        temporary = tmp_path / "temporary"
        temporary.mkdir(exist_ok=True)

        result = apply_registry(manifest, home, config_home=config_home, temporary=temporary)

        assert result.returncode == 1, result.stdout + result.stderr
        assert (
            f"{TRACKED_RULES} overlaid by {config_home / HOST_RULES} is not a valid onevcs "
            "rules composition"
        ) in result.stderr, result.stderr
        assert installed.read_text(encoding="utf-8") == INSTALLED_RULES
        assert registered_paths(home) == set()
        assert "rules      " not in result.stdout
        # uv's environment locks are left by every `uv run`, and the push gate's leftover
        # budget does not count them either.
        left = sorted(p.name for p in temporary.iterdir() if not UV_LOCK.fullmatch(p.name))
        assert left == [], f"a refusal left scratch behind: {left}"


def test_invalid_rules_are_rejected_with_no_present_checkout(tmp_path: Path) -> None:
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{tmp_path / 'not-cloned'}\n", encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("not: [valid", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home", "--rules", str(invalid))

    assert result.returncode == 1
    assert f"{invalid} is not a valid onevcs rules composition" in result.stderr
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


# llmlint: ignore[tests_mirror_real_usage] A registry's rules reference must be seeded directly.
def test_rules_are_installed_at_the_rules_file_the_registry_references(tmp_path: Path) -> None:
    """`onevcs rules apply` installs where the registry looks for rules, wherever that is.

    A registry whose `rules` names another file than `rules.yml` beside it is honoured
    rather than refused: the composed rules land at that reference, the report names
    it, and `onevcs rules check` reads the policy out of it — so the recipe can never
    install a file the registry then ignores.
    """
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    home = tmp_path / "home"
    first = apply_registry(manifest, home)
    assert first.returncode == 0, first.stdout + first.stderr
    document = home / "registry.json"
    registry = json.loads(document.read_text(encoding="utf-8"))
    # The file it references already exists, as an earlier install there would have left
    # it: registration reads the referenced rules before this run installs any.
    elsewhere = tmp_path / "elsewhere.yml"
    elsewhere.write_text(INSTALLED_RULES, encoding="utf-8")
    registry["rules"] = str(elsewhere)
    document.write_text(json.dumps(registry), encoding="utf-8")
    beside = (home / "rules.yml").read_bytes()

    result = apply_registry(manifest, home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"rules      the rules composed from 1 file(s) are at {elsewhere}" in result.stdout
    assert "trailer_prefix: Orchestrator-" in elsewhere.read_text(encoding="utf-8")
    assert (home / "rules.yml").read_bytes() == beside
    checked = onevcs(home, "rules", "check", "github.com/nickderobertis/onevcs")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert reported(checked.stdout, "rules") == str(elsewhere), checked.stdout
    assert reported(checked.stdout, "publication") == "change-auto", checked.stdout


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


def test_a_host_overlay_routes_a_private_team_identity_through_its_own_rule(
    tmp_path: Path,
) -> None:
    """The team half of the journey above: a private identity, and the overlay rule it reaches.

    A team identity is where reading the resolved policy alone proves the least. Its
    rule and `default:` resolve the same `change-open` / `approvals: required` pair, so
    a policy read answers identically whether the rule matched or the checkout fell
    through to the fallthrough — and a checkout that falls through is one
    `just repos-apply` refuses rather than registers, which is a routing install broken
    for every other manager on this host rather than one repository publishing wrongly.
    So what is asserted here is the rule that decided, by its position in the installed
    composition.

    The identity is private, so neither tracked file names it: its checkout is listed
    by the host and its rule is the host overlay's, under a scratch XDG configuration
    home. Without that overlay the same registration falls through and is refused,
    which is the proof that the overlay's rule, and nothing tracked, routes it.
    """
    assert tracked_checkouts_of("quietharbor") == ()
    assert QUIETHARBOR not in ruled_identities()

    path = checkout(tmp_path / "checkouts" / "quietharbor", f"https://{QUIETHARBOR}.git")
    manifest = tmp_path / "checkouts.list"
    manifest.write_text(f"{path}\n", encoding="utf-8")

    unruled = apply_registry(manifest, tmp_path / "bare")
    assert unruled.returncode == 1, unruled.stdout + unruled.stderr
    assert "no rule in" in unruled.stderr and "quietharbor" in unruled.stderr, unruled.stderr

    home = tmp_path / "onevcs"
    config_home = host_config(tmp_path / "xdg")
    applied = apply_registry(manifest, home, config_home=config_home)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert f"(from {TRACKED_RULES} overlaid by {config_home / HOST_RULES})" in applied.stdout

    registry: RegistryDocument = json.loads((home / "registry.json").read_text(encoding="utf-8"))
    assert set(registry["identities"]) == {QUIETHARBOR}

    host, owner, name = QUIETHARBOR.split("/")
    position = OVERLAY_POLICY[QUIETHARBOR].position
    expected_rule = f"rule {position} {{host: {host}, owner: {owner}, name: {name}}}"
    for argument in (QUIETHARBOR, "quietharbor"):
        checked = onevcs(home, "rules", "check", argument)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-open", checked.stdout
        assert reported(checked.stdout, "approvals") == "required", checked.stdout
        assert reported(checked.stdout, "matched") == expected_rule, checked.stdout
        assert reported(checked.stdout, "green draft") == "keep for review", checked.stdout
    assert visibility(home, QUIETHARBOR) == "private"


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501 - llmlint reads a directive's rule list off one line
# A sibling of the printobserver and quietharbor routing journeys beside it: every journey
# of `just repos-apply` lives in this module, in the tier it already runs in, as the
# module's file-scoped reason says, and a project of their own is one change to the
# project graph for all of them at once, not one more journey's to make.
def test_the_tracked_registration_routes_onebudgetspec_and_names_the_wheel_its_row_installs(
    tmp_path: Path,
) -> None:
    """A producer registered ahead of its first release and since adopted, by the tracked files.

    Its rule has to decide its publication — `change-auto` with no approvals, by its own
    position in the file rather than the reviewed default — and the release override has
    to answer, as its default target, the wheel the table's row now installs, because a
    `published` node depending on it names no `consumes` and would otherwise wait on a
    target nothing resolves. The scratch checkout carries the producer's origin and a
    scratch declaration of the target the override names for it, committed at its base,
    where `onevcs` reads it.
    """
    row = by_producer(ONEBUDGETSPEC)
    assert row is not None
    assert (row.target, row.artifact) == (ONEBUDGETSPEC_TARGET, ONEBUDGETSPEC_ARTIFACT)
    assert ONEBUDGETSPEC not in AWAITING_FIRST_ADOPTION
    entries = tracked_checkouts_of("onebudgetspec")
    assert len(entries) == 1, entries

    path = declaring_checkout(
        tmp_path / "checkouts" / Path(entries[0]).name,
        f"https://{ONEBUDGETSPEC}.git",
        {ONEBUDGETSPEC_TARGET: ONEBUDGETSPEC_ARTIFACT},
    )
    manifest = tmp_path / "checkouts.list"
    manifest.write_text(f"{path}\n", encoding="utf-8")
    home = tmp_path / "onevcs"

    applied = apply_registry(manifest, home)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (home / "releases.yml").read_bytes() == TRACKED_RELEASES.read_bytes()

    ruled = ruled_identities()
    assert ruled.count(ONEBUDGETSPEC) == 1, ruled
    host, owner, name = ONEBUDGETSPEC.split("/")
    expected_rule = (
        f"rule {ruled.index(ONEBUDGETSPEC) + 1} {{host: {host}, owner: {owner}, name: {name}}}"
    )
    for argument in (ONEBUDGETSPEC, Path(entries[0]).name):
        checked = onevcs(home, "rules", "check", argument)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-auto", checked.stdout
        assert reported(checked.stdout, "approvals") == "none", checked.stdout
        assert reported(checked.stdout, "matched") == expected_rule, checked.stdout

    answer = release_targets(home, ONEBUDGETSPEC)
    assert answer["default_target"] == ONEBUDGETSPEC_TARGET, answer
    assert answer["adoption"] == GLOBAL_RUNG, answer
    declared = {target["name"]: target["probe"]["args"][0] for target in answer["targets"]}
    assert declared[ONEBUDGETSPEC_TARGET] == ONEBUDGETSPEC_ARTIFACT, declared
    # The resolution `tests/e2e/publish_and_landing/test_release_adoption_in_force_e2e.py`
    # reads off this host, taken here off a real one: the resolved default target is the
    # row's.
    assert (
        default_target_complaint(
            ONEBUDGETSPEC,
            answer.get("default_target"),
            override_default_targets(TRACKED_RELEASES.read_text(encoding="utf-8")),
        )
        is None
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501 - llmlint reads a directive's rule list off one line
# A sibling of the routing journeys above, for the reason their directive states: every
# journey of `just repos-apply` lives in this module, in the tier it already runs in.
@pytest.mark.parametrize(
    "identity", [PRUNER, GRAPHQL_NODE_COUNT], ids=["pruner", "graphql-node-count"]
)
def test_the_tracked_registration_routes_a_sibling_through_its_own_rule(
    tmp_path: Path, identity: RepoIdentity
) -> None:
    """Both tracked spellings of the sibling's checkout, registered, reach its own rule.

    `change-auto` with no approvals by a rule rather than `default:`, read off the real
    `onevcs rules check` for the identity and for each alias a node could name.
    """
    host, owner, name = identity.split("/")
    entries = tracked_checkouts_of(name)
    assert len(entries) == 2, entries

    origin = f"https://{identity}.git"
    paths = [checkout(tmp_path / "checkouts" / Path(entry).name, origin) for entry in entries]
    manifest = tmp_path / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = tmp_path / "onevcs"

    applied = apply_registry(manifest, home)
    assert applied.returncode == 0, applied.stdout + applied.stderr

    ruled = ruled_identities()
    assert ruled.count(identity) == 1, ruled
    expected_rule = (
        f"rule {ruled.index(identity) + 1} {{host: {host}, owner: {owner}, name: {name}}}"
    )

    for argument in (identity, *(Path(entry).name for entry in entries)):
        checked = onevcs(home, "rules", "check", argument)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-auto", checked.stdout
        assert reported(checked.stdout, "approvals") == "none", checked.stdout
        assert reported(checked.stdout, "matched") == expected_rule, checked.stdout


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501 - llmlint reads a directive's rule list off one line
# A sibling of the routing journeys above, for the reason their directive states: every
# journey of `just repos-apply` lives in this module, in the tier it already runs in.
def test_the_tracked_registration_routes_cloud_agent_dev_env_through_its_own_rule(
    tmp_path: Path,
) -> None:
    """The public sibling the tracked list and rules carry, registered, reaches its own rule.

    `change-auto` with no approvals by an explicit rule rather than `default:`, read off
    the real `onevcs rules check` for the identity and for the alias a node could name.
    """
    entries = tracked_checkouts_of("cloud-agent-dev-env")
    assert len(entries) == 1, entries

    origin = f"https://{CLOUD_AGENT_DEV_ENV}.git"
    path = checkout(tmp_path / "checkouts" / Path(entries[0]).name, origin)
    manifest = tmp_path / "checkouts.list"
    manifest.write_text(f"{path}\n", encoding="utf-8")
    home = tmp_path / "onevcs"

    applied = apply_registry(manifest, home)
    assert applied.returncode == 0, applied.stdout + applied.stderr

    ruled = ruled_identities()
    assert ruled.count(CLOUD_AGENT_DEV_ENV) == 1, ruled
    host, owner, name = CLOUD_AGENT_DEV_ENV.split("/")
    matched = f"{{host: {host}, owner: {owner}, name: {name}}}"
    expected_rule = f"rule {ruled.index(CLOUD_AGENT_DEV_ENV) + 1} {matched}"
    for argument in (CLOUD_AGENT_DEV_ENV, Path(entries[0]).name):
        checked = onevcs(home, "rules", "check", argument)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert reported(checked.stdout, "publication") == "change-auto", checked.stdout
        assert reported(checked.stdout, "approvals") == "none", checked.stdout
        assert reported(checked.stdout, "matched") == expected_rule, checked.stdout


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge, test_tiers_split_by_project_not_by_marker]  # noqa: E501


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
    are tracked here and each host registers the ones it holds. So every public
    pre-adoption checkout must still be listed under the spelling it was migrated
    from — dropping one silently unregisters a repository on the host that has it —
    and everything listed beside them must be a checkout `config/onevcs.rules.yml`
    states a publication path for: another spelling of one the migration covered, or
    a repository registered since under a rule of its own. A checkout of anything
    else has only the reviewed default to fall through to, which `just repos-apply`
    refuses rather than registers.

    A private pre-adoption checkout is the third claim: the tracked list names public
    repositories only, so it must *not* be listed, its registration being the host's
    own list's and its rule the host overlay's (`OVERLAY_POLICY`).

    Nothing here reads this host's own `$HOME`: the fixture's paths are absolute
    under the home of the host the registry was captured on, and rendering them
    against any other one produces spellings that could never match.
    """
    listed = {
        entry
        for line in TRACKED_CHECKOUTS.read_text(encoding="utf-8").splitlines()
        if (entry := line.partition("#")[0].strip())
    }
    private = {entry for entry in CHECKOUTS if BY_URL[entry.identity].key in OVERLAY_POLICY}
    assert private, "the pre-adoption fixture no longer holds a private checkout"
    public = set(CHECKOUTS) - private
    migrated = {entry.path.replace(PRE_ADOPTION_HOME, "~", 1) for entry in public}
    covered = {which_checkout(entry.path) for entry in public} | ruled_repositories()

    assert all(entry.startswith("~/") for entry in listed), listed
    assert migrated <= listed, migrated - listed
    unruled = {entry for entry in listed if which_checkout(entry) not in covered}
    assert not unruled, unruled
    leaked = {entry.path for entry in private} & {
        entry.replace("~", PRE_ADOPTION_HOME, 1) for entry in listed
    }
    assert not leaked, leaked


def test_the_tracked_configuration_names_no_identity_the_host_overlay_rules() -> None:
    """The tracked list and rules are public-only: every private identity is the host's.

    Read off both tracked files, so a private repository's checkout or rule written back
    into either fails here, and the identities the overlay journeys route are each one
    the tracked configuration says nothing about.
    """
    ruled = set(ruled_identities())
    names = {key.rpartition("/")[2] for key in OVERLAY_POLICY}
    listed = {
        which_checkout(entry)
        for line in TRACKED_CHECKOUTS.read_text(encoding="utf-8").splitlines()
        if (entry := line.partition("#")[0].strip())
    }
    assert not ruled & set(OVERLAY_POLICY), ruled & set(OVERLAY_POLICY)
    assert not listed & names, listed & names
    assert {key.split("/")[1] for key in ruled} == {"nickderobertis"}, ruled
