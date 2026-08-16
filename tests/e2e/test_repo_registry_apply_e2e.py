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
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

import pytest

from orchestrator.root import REPO_ROOT

#: The registry this host ran on before `onevcs` owned one, verbatim.
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "pre-adoption-repos.json"
#: The tracked checkout list the recipe registers by default.
TRACKED_CHECKOUTS = REPO_ROOT / "config" / "onevcs.checkouts"
#: The tracked rules file the recipe installs, which decides every listed checkout's
#: publication path and is the rule of every gate asserted here.
TRACKED_RULES = REPO_ROOT / "config" / "onevcs.rules.yml"

#: The tracked record of what each repository's merge path really requires, and which
#: of those checks the gate above runs. `config/onevcs.rules.yml` decides the gate;
#: this decides whether that gate is the whole bar, and the tests below are what hold
#: the two together.
MERGE_PATH_CHECKS = REPO_ROOT / "config" / "merge-path-checks.json"

#: A comparison identity no repository's fallback could produce, so a gate that
#: renders it demonstrably read the environment rather than a hardcoded base.
COMPARISON = ("upstream", "release")

#: Every command line each identity's gate must run, in order, against the fixture
#: command surface below. `{base}` is the comparison identity the gate resolved.
#:
#: This is the successor to the pre-adoption gate column in
#: `tests/fixtures/pre-adoption-repos.json`, which is no longer what a gate must
#: reproduce: those gates named each repository's `check`, and most of these
#: repositories require their judged llmlint tier as a separate status check that
#: `check` deliberately does not reach. Carrying the migration verbatim is what let a
#: `nick-derobertis-site` branch pass this gate and be refused as PR #77. The
#: migration's own claim — which publication path and approvals each identity keeps —
#: is asserted from that fixture still.
GATE_TRANSCRIPTS: dict[str, tuple[str, ...]] = {
    "github.com/nickderobertis/ai-orchestrator": (
        "gate base=none NX_BASE=unset NX_HEAD=unset NEXTEST=unset",
    ),
    "github.com/nickderobertis/crozier": (
        "check NEXTEST=unset",
        "lint-llm-diff {base} NEXTEST=unset",
    ),
    "github.com/nickderobertis/dero-skills": (
        "check NEXTEST=unset",
        "lint-llm-diff {base} NEXTEST=unset",
    ),
    "github.com/nickderobertis/llmlint": (
        "check NEXTEST=unset",
        "check-version-bump {base} NEXTEST=unset",
    ),
    "github.com/nickderobertis/nick-derobertis-site": (
        "bootstrap NX_BASE={base}",
        "gate base=none NX_BASE={base} NX_HEAD=unset NEXTEST=unset",
        "lint-llm-diff {base} NEXTEST=unset",
    ),
    "github.com/nickderobertis/oneagentgraph": (
        "gate base={base} NX_BASE=unset NX_HEAD=unset NEXTEST=unset",
    ),
    "github.com/nickderobertis/oneharness": (
        "gate base=none NX_BASE=unset NX_HEAD=unset NEXTEST=unset",
    ),
    # The one gate that resolves the base to a commit before handing it on: this
    # repository's `check-affected` refuses anything but a 40-character SHA pair.
    "github.com/nickderobertis/oneharness-ui": (
        "gate base=none NX_BASE={sha} NX_HEAD=HEAD NEXTEST=unset",
        "lint-llm-diff {base} NEXTEST=unset",
    ),
    "github.com/nickderobertis/onejudge": (
        "check NEXTEST=unset",
        "lint-llm-diff {base} NEXTEST=unset",
    ),
    "github.com/nickderobertis/onepipeline": (
        "gate base={base} NX_BASE=unset NX_HEAD=unset NEXTEST=unset",
    ),
    "github.com/nickderobertis/onepipeline-ui": (
        "gate base={base} NX_BASE=unset NX_HEAD=unset NEXTEST=unset",
    ),
    "github.com/nickderobertis/onevcs": (
        "gate base={base} NX_BASE=unset NX_HEAD=unset NEXTEST=unset",
    ),
    "github.com/nickderobertis/screencomp": (
        "gate base=none NX_BASE=unset NX_HEAD=unset NEXTEST=unset",
    ),
    "github.com/petsinc/org-apps": ("check NEXTEST=unset",),
}

#: One repository as `onevcs` names it once its origin URL is normalized:
#: `host/owner/name`. The rules file matches on its three parts, the registry files
#: identities under it, and `onevcs rules check` takes it as its argument — so it is
#: the vocabulary every set and helper below is keyed by, rather than bare text.
RepoIdentity = str

#: The environment `config/onevcs.rules.yml` used to prepend to the gate of every
#: repository that tests with cargo-nextest, and now prepends to none. It silenced
#: cargo-nextest's per-test status lines so a gate could not fill the single stderr
#: pipe an `onevcs` before 0.2.10 read only after draining stdout. That release
#: drains both concurrently, this host is past it, and the retirement is held here:
#: a loud gate is the case the workaround was hiding, so a wrapper reintroduced by
#: hand would hide it again.
RETIRED_CAPTURE_WORKAROUND = ("env", "NEXTEST_STATUS_LEVEL=fail")

#: Every `just <recipe>` a gate command reaches, in both shapes the rules file writes:
#: bare argv (`["just", "check"]`) and a `bash -c` script chaining two of them. Used to
#: reconcile each rule against the recipes its own repository actually defines.
JUST_RECIPE = re.compile(r"\bjust\s+(?!-)(?P<recipe>[A-Za-z0-9_][A-Za-z0-9_-]*)")

#: How every rule in the tracked file names the repository it matches. Reading them
#: back out is what makes a rule added later fail here, rather than at that
#: repository's first publication.
RULE_MATCH = re.compile(
    r"- match: \{host: (?P<host>[^,]+), owner: (?P<owner>[^,]+), name: (?P<name>[^}]+)\}"
)

#: One fixture command surface every gate is run against: a recipe per name any rule
#: reaches, each reporting the arguments and environment it received. Running the
#: gates rather than reading them is what makes the assertions above about a `bash -c`
#: script — which recipes it reaches, in what order, with which base, and whether the
#: nextest capture workaround survived into each one — evidence rather than a reading.
FIXTURE_JUSTFILE = REPO_ROOT / "tests" / "fixtures" / "gate-command-surface.justfile"


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
    #: The gate command template, which may carry a `{base}` placeholder.
    gate: str

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
            gate=record["gate"],
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


def normalized_identity(origin: str) -> RepoIdentity:
    """The identity `onevcs` files an origin URL under: `host/owner/name`.

    Both spellings this host's checkouts carry are handled — `https://host/owner/name`
    with or without `.git`, and git's scp-like `user@host:owner/name` — because which
    one a clone has is an accident of how it was made.
    """
    without_scheme = re.sub(r"^[a-z][a-z0-9+.-]*://", "", origin.strip())
    user, _, remainder = without_scheme.rpartition("@")
    if user:
        # scp-like: the host is separated from the path by a colon, not a slash.
        remainder = remainder.replace(":", "/", 1)
    return remainder.removesuffix(".git").removesuffix("/")


class Recipes(NamedTuple):
    """The recipes a repository defines, and whether they could be read at all."""

    #: False when `just` could not parse the repository's recipes, so `names` is not
    #: evidence of anything and the reconciliation must report rather than assert.
    readable: bool
    names: frozenset[str]


def defined_recipes(root: Path) -> Recipes:
    """Every recipe `root`'s own justfile defines.

    Read through the tool that owns the definitions rather than by scanning text:
    `just --dump` is the recipe runner's own parse of its justfile, so what comes back
    is the set a gate's `just <recipe>` would really resolve against — aliases and
    imports included, comments and documentation already gone.
    """
    dumped = subprocess.run(
        ["just", "--dump", "--dump-format", "json"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if dumped.returncode != 0:
        return Recipes(readable=False, names=frozenset())
    document = json.loads(dumped.stdout)
    return Recipes(
        readable=True,
        names=frozenset(document["recipes"]) | frozenset(document.get("aliases") or {}),
    )


def gate_recipes(gate: str) -> frozenset[str]:
    """Every `just <recipe>` the resolved gate command reaches."""
    return frozenset(match["recipe"] for match in JUST_RECIPE.finditer(gate))


def checkouts_on_this_host() -> dict[RepoIdentity, Path]:
    """Each ruled identity this host actually holds a checkout of.

    The tracked list names where a checkout of each identity lives under either of the
    two layouts this host clones into, and git is asked which identity one really is
    rather than the path being trusted to say. A host holding none of them resolves
    nothing, which is what the reconciliation below reports rather than asserts on.
    """
    ruled = frozenset(ruled_identities())
    found: dict[RepoIdentity, Path] = {}
    for line in TRACKED_CHECKOUTS.read_text(encoding="utf-8").splitlines():
        entry = line.partition("#")[0].strip()
        if not entry:
            continue
        path = Path(entry).expanduser()
        if not (path / ".git").exists():
            continue
        origin = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
        )
        if origin.returncode != 0:
            continue
        identity = normalized_identity(origin.stdout)
        if identity in ruled:
            found.setdefault(identity, path)
    return found


class MergePath(NamedTuple):
    """One identity's merge path, as `config/merge-path-checks.json` records it."""

    #: The branch its required checks are declared on, which is also the base its gate
    #: falls back to when no comparison identity is exported.
    branch: str
    #: Required check → the commands this host's identity gate runs for it.
    gate_runs: dict[str, tuple[str, ...]]
    #: Required check → the reason slug saying why nothing here runs it.
    not_run: dict[str, str]

    @property
    def required(self) -> frozenset[str]:
        """Every check the merge path requires, however it is classified."""
        return frozenset(self.gate_runs) | frozenset(self.not_run)


def merge_path_checks() -> tuple[dict[RepoIdentity, MergePath], dict[str, str]]:
    """The tracked declaration, read once into records with a shape to check."""
    document = json.loads(MERGE_PATH_CHECKS.read_text(encoding="utf-8"))
    declared = {
        key: MergePath(
            branch=record["branch"],
            gate_runs={check: tuple(commands) for check, commands in record["gate_runs"].items()},
            not_run=dict(record["not_run"]),
        )
        for key, record in sorted(document["identities"].items())
    }
    return declared, dict(document["reasons"])


MERGE_PATHS, REASONS = merge_path_checks()


def gate_command(home: Path, key: RepoIdentity) -> str:
    """The gate `onevcs` resolves for an identity, without its `command:` label.

    Read from the tool rather than from the YAML, so what is asserted is the argv a
    publication would really run.
    """
    return reported(onevcs(home, "rules", "check", key).stdout, "gate").removeprefix("command: ")


def runs_command(gate: str, command: str) -> bool:
    """Whether `gate` reaches `command` — as that recipe, not as a longer-named one.

    `just check` must not be satisfied by `just check-version-bump`: they are separate
    required checks on `llmlint`'s merge path, and treating one as the other is how a
    gate reports a tier it never ran.
    """
    return re.search(rf"{re.escape(command)}(?![\w-])", gate) is not None


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


def test_this_repository_still_publishes_locally(applied: Applied) -> None:
    """The one identity that opens no change request at all, and merges in place."""
    checked = onevcs(applied.home, "rules", "check", "github.com/nickderobertis/ai-orchestrator")
    assert reported(checked.stdout, "publication") == "local-direct"
    assert reported(checked.stdout, "approvals") == "none"


class CommandSurface(NamedTuple):
    """A directory a gate can be run in: the fixture recipes, in a real git checkout."""

    root: Path
    #: What every remote-tracking ref in it points at, which is what a gate that
    #: resolves its base to a commit before handing it on must render.
    sha: str


@pytest.fixture(scope="module")
def command_surface(tmp_path_factory: pytest.TempPathFactory) -> CommandSurface:
    """The fixture command surface, with every base any gate resolves already present.

    A real checkout because one gate resolves its base through `git rev-parse` before
    it can hand it to a recipe; every ref points at the one commit, so what that gate
    renders is the same whichever comparison identity it was given.
    """
    root = tmp_path_factory.mktemp("command-surface")
    shutil.copy2(FIXTURE_JUSTFILE, root / "justfile")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()
    exported = "/".join(COMPARISON)
    for ref in (
        "refs/remotes/origin/main",
        "refs/remotes/origin/master",
        f"refs/remotes/{exported}",
    ):
        subprocess.run(["git", "update-ref", ref, sha], cwd=root, check=True)
    return CommandSurface(root=root, sha=sha)


# llmlint: ignore-block[tests_mirror_real_usage] The interface that runs a gate for
# real is `onevcs`'s publication path, which clones a repository, dispatches an agent
# into a worktree, and pushes the result — a test may not drive that, and this suite
# doubles the published CLIs at exactly that boundary by charter. What is left to
# mirror is how the gate is *executed*, and that is what this reproduces argv for argv.
def run_gate(
    gate: str,
    surface: CommandSurface,
    comparison: tuple[str, str] | None,
    failing: str | None = None,
) -> list[str]:
    """Run a resolved gate against the fixture surface and return what it reached.

    The gate is executed as the argv `onevcs` would execute — `env` prefixes included,
    since a `command:` gate runs with no shell and `env` is a binary in argument
    position there rather than a prefix something interprets.

    `failing` names the one fixture recipe that reports itself and then exits non-zero.
    The gate is then required to fail, which is the half a surface of succeeding recipes
    cannot establish: that a tier's failure ends the gate rather than being swallowed.
    """
    prefix, shell, script = gate.partition("bash -c ")
    argv = [*shlex.split(prefix), *(["bash", "-c", script] if shell else [])] or shlex.split(gate)
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("NEXTEST_STATUS_LEVEL", "NX_BASE", "NX_HEAD", "GATE_FIXTURE_FAIL")
    }
    if comparison is not None:
        environment["ONEVCS_COMPARISON_REMOTE"], environment["ONEVCS_COMPARISON_BASE"] = comparison
    if failing is not None:
        environment["GATE_FIXTURE_FAIL"] = failing
    run = subprocess.run(
        argv, cwd=surface.root, env=environment, text=True, capture_output=True, check=False
    )
    if failing is None:
        assert run.returncode == 0, run.stdout + run.stderr
    else:
        assert run.returncode != 0, (
            f"the gate reported success while its {failing!r} tier failed:\n"
            f"{run.stdout}{run.stderr}"
        )
    return run.stdout.splitlines()


# llmlint: ignore-end[tests_mirror_real_usage]


@pytest.mark.parametrize("key", sorted(GATE_TRANSCRIPTS))
def test_each_gate_runs_its_repositorys_whole_bar_against_the_comparison_base(
    ruled: Path, command_surface: CommandSurface, key: RepoIdentity
) -> None:
    """The gate is proven by running it, against a base only the environment could name.

    What a rule promises is a command line, and reading one back tells you only that
    somebody wrote it down. So each gate is executed: the recipes it reaches, their
    order, the base it resolved, and whether the nextest capture workaround survived
    into each one all come out of the fixture surface's own report.
    """
    remote, base = COMPARISON
    transcript = run_gate(gate_command(ruled, key), command_surface, COMPARISON)

    assert transcript == [
        line.format(base=f"{remote}/{base}", sha=command_surface.sha)
        for line in GATE_TRANSCRIPTS[key]
    ]


@pytest.mark.parametrize("key", sorted(GATE_TRANSCRIPTS))
def test_a_gate_run_with_no_comparison_identity_falls_back_to_its_own_base_branch(
    ruled: Path, command_surface: CommandSurface, key: RepoIdentity
) -> None:
    """The fallback has to be the repository's real base branch, and one of them is not `main`.

    `onevcs` exports the comparison identity on every lifecycle path, so the fallback
    only shows up when an operator runs a gate by hand — which is exactly when a wrong
    one is least likely to be noticed. `nick-derobertis-site` publishes to `master`,
    and a gate defaulting to `origin/main` there judges against a ref that repository
    does not have.
    """
    branch = MERGE_PATHS[key].branch
    transcript = run_gate(gate_command(ruled, key), command_surface, None)

    assert transcript == [
        line.format(base=f"origin/{branch}", sha=command_surface.sha)
        for line in GATE_TRANSCRIPTS[key]
    ]


@pytest.mark.parametrize("key", sorted(GATE_TRANSCRIPTS))
def test_a_gate_stops_at_its_first_failing_tier_and_reports_the_failure(
    ruled: Path, command_surface: CommandSurface, key: RepoIdentity
) -> None:
    """The property success cannot show: a tier that fails ends the gate, loudly.

    `onevcs` decides whether a branch may publish from this argv's exit status alone, so
    a gate that ran a failing tier and still exited 0 would publish work nothing verified
    — the same outcome PR #77 had, reached from the opposite direction. Every chained
    gate here is `&&`-joined, and that chain is only load-bearing when the first failure
    ends it: a rule respelled with `;` or `||`, or wrapped in something that swallows a
    status, keeps every transcript above green and fails only here.
    """
    remote, base = COMPARISON
    expected = [
        line.format(base=f"{remote}/{base}", sha=command_surface.sha)
        for line in GATE_TRANSCRIPTS[key]
    ]
    first_tier = expected[0].split()[0]

    transcript = run_gate(gate_command(ruled, key), command_surface, COMPARISON, failing=first_tier)

    assert transcript == expected[:1], (
        f"{key}'s gate reached a tier past the {first_tier!r} that failed, so its "
        "chain does not short-circuit"
    )


@pytest.mark.reads_checkouts
def test_every_gate_names_a_recipe_its_repository_defines(ruled: Path) -> None:
    """The drift gate: each rule's `just <recipe>`, against the justfile that owns it.

    A gate is argv `onevcs` runs in the repository being published, and every rule here
    but one names a `just` recipe. Which recipes a repository has is a fact no file in
    *this* repository decides, so a rule naming one that was renamed away is a merge
    path that fails at publication — after a worker has done the work — for a reason
    this host authored. Reconciling it here is what turns that into a failed gate on
    the change that caused it.

    This is why the tier is uncached. Its inputs are checkouts outside the workspace,
    which no `nx.json` key covers, and a memoized green would be a verdict on whatever
    those repositories looked like when it was recorded.
    """
    present = checkouts_on_this_host()
    if not present:
        pytest.skip(
            "this host holds no checkout of any ruled identity, so there is nothing "
            "to reconcile the rules file against"
        )

    found = {identity: defined_recipes(path) for identity, path in sorted(present.items())}
    unreadable = sorted(identity for identity, result in found.items() if not result.readable)
    assert not unreadable, (
        "`just --dump` could not parse the recipes of these registered checkouts, so "
        f"their gates could not be reconciled at all: {unreadable}"
    )

    gates = {identity: gate_command(ruled, identity) for identity in found}
    missing = {
        identity: sorted(gate_recipes(gates[identity]) - result.names)
        for identity, result in found.items()
        if gate_recipes(gates[identity]) - result.names
    }
    assert not missing, "\n".join(
        f"{identity}: its rule's gate ({gates[identity]}) runs `just "
        f"{'`, `just '.join(recipes)}`, which {present[identity]} does not define — "
        "correct the gate in config/onevcs.rules.yml"
        for identity, recipes in missing.items()
    )


def required_checks(key: RepoIdentity) -> tuple[frozenset[str], str | None]:
    """What GitHub really requires to merge into an identity's base branch.

    Asked of the API rather than of a workflow file, because a workflow job is not a
    required check: `llmlint` and `screencomp` both run one on every pull request and
    require neither, and a gate reproducing those would verify something that cannot
    refuse a merge. Returns the contexts, or the reason they could not be read.
    """
    repository = key.partition("/")[2]
    branch = MERGE_PATHS[key].branch
    probe = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}/branches/{branch}/protection",
            "--jq",
            "[.required_status_checks.contexts // []] | flatten | .[]",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode == 0:
        return frozenset(probe.stdout.split("\n")) - {""}, None
    # An unprotected branch is an answer, not an outage: nothing is required there.
    if "Branch not protected" in probe.stderr:
        return frozenset(), None
    diagnostic = (probe.stderr or probe.stdout).strip()
    return frozenset(), diagnostic.splitlines()[-1] if diagnostic else "gh reported no diagnostic"


@pytest.mark.reads_checkouts
def test_the_declared_required_checks_match_each_repositorys_branch_protection() -> None:
    """The drift gate: the declaration, against the merge paths that own the fact.

    `config/merge-path-checks.json` restates something no file in this repository
    decides — which checks another repository requires to merge — and a check added,
    renamed, or newly required after it was written would otherwise be found the way
    this host found the defect: by a dispatched branch passing its gate, publishing,
    and sitting blocked.

    This is why the tier is uncached, for the same reason the recipe reconciliation
    is: its input is other repositories' branch protection, which no `nx.json` key
    covers, and a memoized green would be a verdict on whatever they required when it
    was recorded. GitHub being unreachable is reported as unknown rather than as a
    pass — the probe skips, and only an answer that disagrees fails.
    """
    if shutil.which("gh") is None:
        pytest.skip("gh is not installed, so this host cannot read any branch protection")

    answered: dict[RepoIdentity, frozenset[str]] = {}
    unread: dict[RepoIdentity, str] = {}
    for key in sorted(MERGE_PATHS):
        contexts, failure = required_checks(key)
        if failure is None:
            answered[key] = contexts
        else:
            unread[key] = failure
    if not answered:
        pytest.skip(f"no branch protection could be read at all: {unread}")

    drifted = {
        key: (contexts ^ MERGE_PATHS[key].required)
        for key, contexts in answered.items()
        if contexts != MERGE_PATHS[key].required
    }
    assert not drifted, "\n".join(
        f"{key}: config/merge-path-checks.json and {MERGE_PATHS[key].branch}'s branch "
        f"protection disagree about {sorted(difference)} — classify each one under "
        "`gate_runs` with the command this host's gate runs for it, or under `not_run` "
        "with the reason nothing here does"
        for key, difference in drifted.items()
    )
    if unread:
        pytest.skip(f"branch protection could not be read for {unread}")


@pytest.mark.parametrize("key", sorted(frozenset(ruled_identities())))
def test_no_gate_carries_the_retired_capture_workaround(ruled: Path, key: RepoIdentity) -> None:
    """Every gate is the argv its repository verifies with, and nothing wraps one.

    Nine rules once ran their argv through `env NEXTEST_STATUS_LEVEL=fail` to silence
    cargo-nextest, because an `onevcs` before 0.2.10 could be wedged by a gate that
    filled its stderr pipe. That release drains both pipes, so the silencing is retired
    and a loud gate is judged by its own exit status — which is the case the workaround
    was hiding. Asserted on the gate `onevcs` resolves, so a wrapper written back into
    the rules file in either position fails here: leading the argv of a `command:` gate,
    which runs with no shell, or inside the `bash -c` script the translated gates run.
    That no recipe a gate reaches *receives* the variable by some other route is what
    the `NEXTEST=unset` in every transcript above shows.
    """
    gate = gate_command(ruled, key)
    assert RETIRED_CAPTURE_WORKAROUND[1] not in gate, gate


def test_every_ruled_identity_declares_what_its_merge_path_requires() -> None:
    """A rule nobody classified is a gate nobody compared against the merge path.

    The two files decide different halves of one question — `config/onevcs.rules.yml`
    what a publication verifies, `config/merge-path-checks.json` what the merge really
    requires — and an identity present in only one of them is the half nobody checked.
    """
    assert set(MERGE_PATHS) == set(ruled_identities())
    assert set(GATE_TRANSCRIPTS) == set(ruled_identities())


@pytest.mark.parametrize("key", sorted(MERGE_PATHS))
def test_every_required_check_is_classified_exactly_once(key: RepoIdentity) -> None:
    """A check in both maps claims to be run and not run at once, and one of them is wrong."""
    declared = MERGE_PATHS[key]
    overlap = set(declared.gate_runs) & set(declared.not_run)
    assert not overlap, f"{key} classifies {sorted(overlap)} as both run and not run"
    unknown = {reason for reason in declared.not_run.values() if reason not in REASONS}
    assert not unknown, f"{key} names reasons no `reasons` entry defines: {sorted(unknown)}"


def test_every_declared_reason_is_one_some_identity_uses() -> None:
    """The vocabulary is small on purpose: an unused reason is one nobody had to justify."""
    used = {reason for declared in MERGE_PATHS.values() for reason in declared.not_run.values()}
    assert set(REASONS) == used, f"unused reasons: {sorted(set(REASONS) - used)}"


@pytest.mark.parametrize("key", sorted(MERGE_PATHS))
def test_every_declared_gate_command_is_in_the_rule_gate(ruled: Path, key: RepoIdentity) -> None:
    """The contract this whole file exists for: the gate runs every tier it claims to.

    A rule edited back to a bare `check` — which is what every one of these rules said
    until a `nick-derobertis-site` branch passed its gate and was refused as PR #77 —
    stops naming the command its merge path's judged tier is declared against, and
    fails here rather than after a full dispatch and a published change request.
    """
    gate = gate_command(ruled, key)
    missing = {
        check: command
        for check, commands in MERGE_PATHS[key].gate_runs.items()
        for command in commands
        if not runs_command(gate, command)
    }
    assert not missing, (
        f"{key}'s gate does not run what config/merge-path-checks.json declares it runs "
        f"for its required checks {sorted(missing)}: {sorted(missing.values())} are absent "
        f"from {gate!r} — add them to the rule in config/onevcs.rules.yml, or correct the "
        "declaration if that check no longer gates the merge"
    )


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


def test_invalid_rules_do_not_replace_the_installed_rules(tmp_path: Path) -> None:
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    installed = home / "rules.yml"
    installed.write_text("version: 2\nrules: []\n", encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("not: [valid", encoding="utf-8")

    result = apply_registry(manifest, home, "--rules", str(invalid))

    assert result.returncode == 1
    assert "is not a valid onevcs rules file" in result.stderr
    assert installed.read_text(encoding="utf-8") == "version: 2\nrules: []\n"


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


def which_checkout(path: str) -> str:
    """Which checkout a path names, whichever host's layout spelled it.

    The hosts this list serves lay the same checkouts out differently — one keeps
    this repository's clones at the top of `$HOME` and the engine repositories under
    `~/projects`, the other keeps everything under `~/projects` and lets the
    dispatcher clone the engines into `~/.ai-orchestrator/repos/<owner>__<name>` —
    so two spellings of one checkout agree on nothing but their last component, with
    that owner prefix removed. `onevcs` reads the same component to derive an alias.
    """
    return Path(path).name.rpartition("__")[2]


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
