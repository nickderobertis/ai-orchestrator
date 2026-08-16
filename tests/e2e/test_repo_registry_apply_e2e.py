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

#: The base a gate template's `{base}` was substituted with before the adoption:
#: the comparison remote and base, which `onevcs` now exports as environment.
COMPARISON = ("origin", "main")

#: The three pre-adoption gates that took a base ref, and the command lines their
#: translated form must run. A `command:` gate is argv run without a shell and with
#: no substitution, so these are the one shape that could not be carried across
#: verbatim — see `docs/host-setup.md`. `dero-skills` is also the one whose old
#: template could never have worked: `shlex.split` made its `&&` a literal argument
#: to `just`, so the translation runs the two commands it evidently meant.
TRANSLATED_GATES = {
    "github.com/nickderobertis/dero-skills": (
        "just check",
        "just lint-llm-diff origin/main",
    ),
    "github.com/nickderobertis/nick-derobertis-site": (
        "NX_BASE=origin/main just bootstrap",
        "NX_BASE=origin/main just gate",
    ),
    "github.com/nickderobertis/oneharness-ui": (
        "npx nx affected --target=check --base=origin/main",
    ),
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
#: back out is what makes a rule added later for another cargo-nextest repository
#: fail here, rather than at that repository's first publication.
RULE_MATCH = re.compile(
    r"- match: \{host: (?P<host>[^,]+), owner: (?P<owner>[^,]+), name: (?P<name>[^}]+)\}"
)

#: Fixture recipes expose the arguments and environment received through the real
#: recipe runner and shell. They avoid running project checks unrelated to this
#: registry migration while still proving that translated gates are executable.
FIXTURE_JUSTFILE = """
check:
    @echo "just check"

lint-llm-diff base:
    @echo "just lint-llm-diff {{base}}"

bootstrap:
    @echo "NX_BASE=$NX_BASE just bootstrap"

gate:
    @echo "NX_BASE=$NX_BASE just gate"
"""

#: The same idea for the retired workaround: recipes that report the status level they
#: were handed, so a gate is *run* rather than only read back. Reading the argv alone
#: would miss a wrapper reintroduced through the recipe runner's own environment. Kept
#: apart from the recipes above because `check` and `gate` are the names both need.
CAPTURE_FIXTURE_JUSTFILE = """
check:
    @echo "check ${NEXTEST_STATUS_LEVEL:-unset}"

gate:
    @echo "gate ${NEXTEST_STATUS_LEVEL:-unset}"
"""


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


def expected_gate(identity: Identity) -> str:
    """The command line an identity's gate must run.

    That is exactly what it ran before the adoption. Nothing wraps a gate any more:
    the nextest capture workaround nine of them carried is retired, and
    `test_no_gate_carries_the_retired_capture_workaround` holds that.
    """
    return identity.gate


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


@pytest.mark.parametrize("identity", IDENTITIES, ids=lambda identity: identity.key)
def test_each_gate_renders_the_same_command_or_runs_the_translated_template(
    applied: Applied, tmp_path: Path, identity: Identity
) -> None:
    gate = reported(onevcs(applied.home, "rules", "check", identity.key).stdout, "gate")

    if "{base}" not in identity.gate:
        assert gate == f"command: {expected_gate(identity)}"
        return

    # A translated gate is proven by running it: the commands it reaches, and the
    # base it hands them, are what the pre-adoption template resolved to.
    script = gate.removeprefix("command: bash -c ")
    assert script != gate, f"{identity.key} kept a {{base}} template instead of a shell: {gate}"
    remote, base = COMPARISON
    if identity.key == "github.com/nickderobertis/oneharness-ui":
        # Executing real Nx would require a complete fixture workspace and its npm
        # dependencies. The exact argv is the useful boundary proof for this gate;
        # the two recipe-based translations below are additionally executed.
        assert script == (
            "npx nx affected --target=check "
            '--base="${ONEVCS_COMPARISON_REMOTE:-origin}/'
            '${ONEVCS_COMPARISON_BASE:-main}"'
        )
    else:
        (tmp_path / "justfile").write_text(FIXTURE_JUSTFILE, encoding="utf-8")
        run = subprocess.run(
            ["bash", "-c", script],
            cwd=tmp_path,
            env={
                **os.environ,
                "ONEVCS_COMPARISON_REMOTE": remote,
                "ONEVCS_COMPARISON_BASE": base,
            },
            text=True,
            capture_output=True,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert tuple(run.stdout.split("\n")[:-1]) == TRANSLATED_GATES[identity.key]
    # Nothing of the template was dropped on the way into the shell.
    for token in shlex.split(identity.gate):
        if token not in ("{base}", "&&", "env", "bash", "-c"):
            assert token.replace("{base}", "") in script


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

    gates = {
        identity: reported(onevcs(ruled, "rules", "check", identity).stdout, "gate")
        for identity in found
    }
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


@pytest.mark.parametrize("key", sorted(frozenset(ruled_identities())))
def test_no_gate_carries_the_retired_capture_workaround(
    ruled: Path, tmp_path: Path, key: RepoIdentity
) -> None:
    """Asserted on the gate `onevcs` resolves, and then by running that argv.

    Reading the resolved argv catches the wrapper written back into the rules file;
    running it catches the same suppression arriving any other way, since what the
    fixture recipe reports is the value the gate's own child actually saw. `unset` is
    the whole point — a loud gate is the case the workaround was hiding, and `onevcs`
    0.2.10 onward drains both of its pipes rather than needing it quiet.
    """
    gate = reported(onevcs(ruled, "rules", "check", key).stdout, "gate")
    assert RETIRED_CAPTURE_WORKAROUND[1] not in gate, gate

    argv = shlex.split(gate.removeprefix("command: "))
    if argv[0] != "just":
        # The three translated gates run through a shell against tooling no fixture
        # here provides; the resolved argv above is their proof.
        return
    (tmp_path / "justfile").write_text(CAPTURE_FIXTURE_JUSTFILE, encoding="utf-8")
    run = subprocess.run(
        argv,
        cwd=tmp_path,
        env={name: value for name, value in os.environ.items() if name != "NEXTEST_STATUS_LEVEL"},
        text=True,
        capture_output=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.split() == [argv[-1], "unset"], run.stdout


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
