from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import git

from orchestrator import REPO_ROOT
from orchestrator.registry import Registry, RegistryEntry
from orchestrator.verify import NOOP_GATE


def _cli(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).parent / name
    return subprocess.run([str(executable), *args], text=True, capture_output=True, check=check)


@contextmanager
def _github_api_server(tmp_path: Path) -> Iterator[str]:
    """Serve GitHub REST shapes through the real gh client's TLS boundary."""
    cert = tmp_path / "github.crt"
    key = tmp_path / "github.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    class GitHubHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.removeprefix("/api/v3/repos/acme/")
            match path.split("/"):
                case ["inaccessible"]:
                    self._json({"message": "forbidden"}, status=403)
                case ["empty-default"]:
                    self._json({"default_branch": ""})
                case [repo]:
                    branches = {"required": "master", "slash": "release/next"}
                    self._json({"default_branch": branches.get(repo, "main")})
                case ["malformed", "branches", "main"]:
                    self._json({})
                case [repo, "branches", branch]:
                    checks = {
                        ("required", "master"): ["complete-gate"],
                        ("slash", "release%2Fnext"): ["slash-gate"],
                    }
                    contexts = checks.get((repo, branch))
                    if contexts is None:
                        self._json({"protected": False})
                    else:
                        self._json(
                            {
                                "protected": True,
                                "protection": {"required_status_checks": {"contexts": contexts}},
                            }
                        )
                case _:
                    self._json({"message": f"unexpected path {self.path}"}, status=404)

        def _json(self, payload: object, *, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), GitHubHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_registry_register_discover_and_refresh_journey(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home / ".ai-orchestrator"))
    origin = bare_origin()
    checkout = tmp_path / "dev" / "widget"
    git("clone", str(origin), str(checkout))

    registered = _cli("orchestrator-register-repo", str(checkout), "--repo-type", "single-owner")
    assert str(checkout.resolve()) in registered.stdout

    registry_path = home / ".ai-orchestrator" / "repos.json"
    registry_path.unlink()
    remote_url = "https://github.com/acme/widget.git"
    git("remote", "set-url", "origin", remote_url, cwd=checkout)
    registry = Registry()
    assert (
        registry.resolve("acme/widget", search_roots=[checkout.parent], repo_type="single-owner")
        == checkout.resolve()
    )
    persisted = Registry()
    assert persisted.entries["acme/widget"].path == str(checkout.resolve())
    assert persisted.resolve("acme/widget", search_roots=[]) == checkout.resolve()

    # Route the discovered checkout back to the local bare remote for the
    # offline refresh leg, and persist the corresponding origin identity.
    git("remote", "set-url", "origin", str(origin), cwd=checkout)
    registry.entries["acme/widget"] = RegistryEntry(str(checkout.resolve()), str(origin), "remote")
    registry.save()

    writer = tmp_path / "writer"
    git("clone", str(origin), str(writer))
    (writer / "new.txt").write_text("new\n", encoding="utf-8")
    git("add", "new.txt", cwd=writer)
    git("commit", "-m", "upstream", cwd=writer)
    git("push", "origin", "main", cwd=writer)

    listed = _cli("orchestrator-repos", "--refresh", "--format", "json")
    output = listed.stdout
    assert '"refreshed": true' in output
    assert (checkout / "new.txt").read_text(encoding="utf-8") == "new\n"


def test_register_recipe_clones_missing_checkout_to_managed_path(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
) -> None:
    state = tmp_path / "state"
    origin = bare_origin()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    assert real_git is not None
    git_wrapper = bin_dir / "git"
    git_wrapper.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = clone ]; then exec "{real_git}" -c '
        f'url."{origin}".insteadOf=https://github.com/acme/widget.git "$@"; fi\n'
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    git_wrapper.chmod(0o755)
    env = {
        **os.environ,
        "AI_ORCHESTRATOR_HOME": str(state),
        "AI_ORCHESTRATOR_SEARCH_ROOTS": str(tmp_path / "empty"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }

    registered = subprocess.run(
        ["just", "register-repo", "acme/widget", "--repo-type", "single-owner"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert registered.returncode == 0, registered.stderr
    checkout = state / "repos" / "acme__widget"
    assert f"checkout={checkout}" in registered.stdout
    assert git("remote", "get-url", "origin", cwd=checkout).strip() == (
        "https://github.com/acme/widget.git"
    )
    stored = json.loads((state / "repos.json").read_text(encoding="utf-8"))
    assert stored["checkouts"]["acme/widget"]["path"] == str(checkout)


def test_merge_gate_coverage_onboarding_and_registry_audit_journeys(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state))

    def checkout(name: str) -> Path:
        path = tmp_path / name
        git("clone", str(bare_origin()), str(path))
        git("remote", "set-url", "origin", f"https://github.com/acme/{name}.git", cwd=path)
        return path

    with _github_api_server(tmp_path) as github_host:
        monkeypatch.setenv("GH_HOST", github_host)
        monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "test-token")
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "github.crt"))
        monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "gh-config"))

        hooked = checkout("hooked")
        hook = hooked / ".git" / "hooks" / "pre-push"
        hook.write_text("#!/bin/sh\nexec make check\n", encoding="utf-8")
        hook.chmod(0o755)
        hook_result = _cli("orchestrator-register-repo", str(hooked), "--repo-type", "single-owner")
        assert "status=covered" in hook_result.stdout
        assert "coverage=executable pre-push hook" in hook_result.stdout

        required = checkout("required")
        required_result = _cli(
            "orchestrator-register-repo", str(required), "--repo-type", "single-owner"
        )
        assert "required PR status checks on master (complete-gate)" in required_result.stdout

        slash = checkout("slash")
        slash_result = _cli("orchestrator-register-repo", str(slash), "--repo-type", "single-owner")
        assert "required PR status checks on release/next (slash-gate)" in slash_result.stdout

        empty_husky = checkout("empty-husky")
        (empty_husky / ".husky").mkdir()
        git("config", "core.hooksPath", ".husky", cwd=empty_husky)
        empty_result = _cli(
            "orchestrator-register-repo", str(empty_husky), "--repo-type", "single-owner"
        )
        assert "status=not-covered" in empty_result.stdout
        assert "executable_pre_push_hook=missing" in empty_result.stdout

        neither = checkout("neither")
        neither_result = _cli(
            "orchestrator-register-repo", str(neither), "--repo-type", "single-owner"
        )
        assert "identity https://github.com/acme/neither has no executable pre-push hook" in (
            neither_result.stderr
        )
        assert "no required PR status checks exist" in neither_result.stderr
        assert Registry().entries["local/neither"].path == str(neither.resolve())

        for unavailable in ("inaccessible", "empty-default", "malformed"):
            result = _cli(
                "orchestrator-register-repo",
                str(checkout(unavailable)),
                "--repo-type",
                "single-owner",
            )
            assert "required_pr_status_checks=unknown" in result.stdout
            assert "not known to run a gate" in result.stderr

        local_only = tmp_path / "local-only"
        git("clone", str(bare_origin()), str(local_only))
        local_result = _cli(
            "orchestrator-register-repo",
            str(local_only),
            "--workflow",
            "local",
            "--repo-type",
            "single-owner",
        )
        assert "required_pr_status_checks=not-applicable (no GitHub origin)" in local_result.stdout

        audit = _cli("orchestrator-repos", "--audit-gate-coverage")
        assert audit.stdout.count("merge_gate_coverage identity=") == 9
        assert "identity=https://github.com/acme/required status=covered" in audit.stdout


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("neither", "no required PR status checks exist"),
        ("inaccessible", "required PR status checks are unknown"),
    ],
)
def test_remote_identity_without_required_checks_refuses_recovery(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    expected: str,
) -> None:
    """Recovery stops against a remote identity no required check will verify.

    The repository's merge path is the only gate now, so an identity whose branch
    protection is absent or unreadable has to be refused before recovery attests
    and pushes preserved work that nothing would ever verify.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state))
    checkout = tmp_path / name
    git("clone", str(bare_origin()), str(checkout))
    git("remote", "set-url", "origin", f"https://github.com/acme/{name}.git", cwd=checkout)

    with _github_api_server(tmp_path) as github_host:
        monkeypatch.setenv("GH_HOST", github_host)
        monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "test-token")
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "github.crt"))
        monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "gh-config"))
        _cli("orchestrator-register-repo", str(checkout), "--repo-type", "single-owner")

        recovered = _cli(
            "orchestrator-repo-recover",
            "claude/preserved",
            "--repo",
            str(checkout),
            "--gate",
            "true",
            check=False,
        )

    assert recovered.returncode == 2
    assert "recovery refused for identity" in recovered.stderr
    assert expected in recovered.stderr
    assert "just repos --audit-gate-coverage" in recovered.stderr


@pytest.mark.parametrize(
    ("command", "positionals"),
    [
        ("orchestrator-repo-task", ("o/r", "engineer", "never dispatched")),
        ("orchestrator-run-plan", ("plan.json",)),
    ],
)
def test_lifecycle_clis_reject_the_retired_skip_verify_flag(
    command: str, positionals: tuple[str, ...]
) -> None:
    """The flag is gone from every surface that used to accept it.

    It never skipped merge-path verification, and now cannot skip anything, so a
    script still passing it must fail loudly rather than appear to take effect.
    """
    rejected = _cli(command, *positionals, "--skip-verify", check=False)

    assert rejected.returncode == 2
    assert "unrecognized arguments: --skip-verify" in rejected.stderr


def test_lifecycle_clis_reject_unknown_local_aliases_before_dispatch(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every serialized lifecycle CLI rejects reserved aliases at its boundary."""
    state = tmp_path / "state"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(state))
    checkout = tmp_path / "checkout"
    git("clone", str(bare_origin()), str(checkout))
    Registry().register(str(checkout), workflow="local")
    assert Registry().resolve("local/checkout") == checkout.resolve()

    unknown_repo = _cli(
        "orchestrator-repo-task",
        "local/does-not-exist",
        "engineer",
        "must not dispatch",
        check=False,
    )
    assert unknown_repo.returncode == 2
    assert "unknown local checkout alias 'local/does-not-exist'" in unknown_repo.stderr
    assert "just repos" in unknown_repo.stderr

    unknown_execution = _cli(
        "orchestrator-repo-task",
        "local/checkout",
        "engineer",
        "must not dispatch",
        "--execution-checkout",
        "local/missing-execution",
        check=False,
    )
    assert unknown_execution.returncode == 2
    assert "unknown local checkout alias 'local/missing-execution'" in unknown_execution.stderr
    assert "just repos" in unknown_execution.stderr

    plan = tmp_path / "unknown-execution-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tasks": [
                    {
                        "id": "unknown-execution",
                        "repo": "local/checkout",
                        "persona": "engineer",
                        "task": "must not dispatch",
                        "execution_checkout": "local/missing-execution",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    unknown_plan = _cli(
        "orchestrator-repo-plan",
        str(plan),
        "--runs-dir",
        str(runs),
        check=False,
    )
    assert unknown_plan.returncode == 2
    assert "unknown local checkout alias 'local/missing-execution'" in unknown_plan.stderr
    assert "just repos" in unknown_plan.stderr
    assert not runs.exists()
    assert not (state / "worktrees").exists()


def test_conflicting_legacy_aliases_require_and_support_cli_identity_migration(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    registry_path = home / ".ai-orchestrator" / "repos.json"
    registry_path.parent.mkdir(parents=True)
    origin = bare_origin()
    canonical = tmp_path / "canonical"
    safety = tmp_path / "safety"
    git("clone", str(origin), str(canonical))
    git("clone", str(origin), str(safety))
    registry_path.write_text(
        json.dumps(
            {
                "local/widget": {
                    "path": str(canonical.resolve()),
                    "origin": str(origin),
                    "workflow": "local",
                },
                "acme/widget": {
                    "path": str(safety.resolve()),
                    "origin": str(origin),
                    "workflow": "remote",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(registry_path.parent))

    failed = _cli("orchestrator-repos", check=False)
    assert failed.returncode == 2
    assert "local/widget: workflow=local" in failed.stderr
    assert "acme/widget: workflow=remote" in failed.stderr
    assert "just migrate-repo-workflow acme/widget --workflow <local|remote>" in failed.stderr

    migrated = _cli("orchestrator-migrate-repo-workflow", "local/widget", "--workflow", "local")
    assert "publication_workflow=local" in migrated.stdout
    assert "acme/widget,local/widget" in migrated.stdout
    registry = Registry()
    assert len(registry.identities) == 1
    assert {entry.workflow for entry in registry.entries.values()} == {"local"}
    assert {Path(entry.path) for entry in registry.entries.values()} == {canonical, safety}


def test_repository_type_migration_installed_cli_journey(
    tmp_path: Path,
    bare_origin: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed type CLI migrates v3 metadata and enforces the team invariant."""
    home = tmp_path / "home" / ".ai-orchestrator"
    home.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    git("clone", str(bare_origin()), str(checkout))
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home))

    registered = _cli(
        "orchestrator-register-repo",
        str(checkout),
        "--workflow",
        "local",
        "--repo-type",
        "single-owner",
    )
    assert "repository_type=single-owner" in registered.stdout
    alias = f"local/{checkout.name}"

    typed = _cli("orchestrator-migrate-repo-type", alias, "--repo-type", "team")
    assert "repository_type=team" in typed.stdout
    stored = next(iter(Registry().identities.values()))
    assert stored.repo_type == "team" and stored.workflow == "remote"

    rejected = _cli(
        "orchestrator-migrate-repo-workflow",
        alias,
        "--workflow",
        "local",
        check=False,
    )
    assert rejected.returncode == 2
    assert "migrate the repository type to single-owner first" in rejected.stderr


def test_register_ranks_affected_gate_and_persists_explicit_gate(
    tmp_path: Path, bare_origin: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home))
    checkout = tmp_path / "monorepo"
    git(
        "clone",
        str(
            bare_origin(
                {
                    "nx.json": "{}",
                    "turbo.json": "{}",
                    "WORKSPACE.bazel": "",
                    "pnpm-workspace.yaml": "packages: []\n",
                    "lerna.json": "{}",
                    "justfile": "check:\n\ttrue\n",
                }
            )
        ),
        str(checkout),
    )

    result = _cli(
        "orchestrator-register-repo",
        str(checkout),
        "--workflow",
        "local",
        "--repo-type",
        "single-owner",
    )

    assert "1. npx nx affected --target=check --base={base}" in result.stdout
    for expected in ("turbo run", "bazel-diff", "pnpm --filter", "lerna run"):
        assert expected in result.stdout
    assert result.stdout.index("npx nx affected") < result.stdout.index("6. just check")
    assert (
        next(iter(Registry().identities.values())).gate
        == "npx nx affected --target=check --base={base}"
    )

    conflicting = _cli(
        "orchestrator-register-repo", str(checkout), "--gate", "just check", check=False
    )
    assert conflicting.returncode == 2
    assert "conflicts with registered identity" in conflicting.stderr

    plain = tmp_path / "plain"
    git("clone", str(bare_origin({"Makefile": "check:\n\ttrue\n"})), str(plain))
    _cli(
        "orchestrator-register-repo",
        str(plain),
        "--workflow",
        "local",
        "--repo-type",
        "single-owner",
    )
    assert Registry().entries[f"local/{plain.name}"].gate == "make check"

    explicit = tmp_path / "explicit"
    git("clone", str(bare_origin()), str(explicit))
    _cli(
        "orchestrator-register-repo",
        str(explicit),
        "--workflow",
        "local",
        "--repo-type",
        "single-owner",
        "--gate",
        "custom verify {base}",
    )
    assert Registry().entries[f"local/{explicit.name}"].gate == "custom verify {base}"


def test_gateless_registration_warns_and_gate_migration_updates_all_aliases(
    tmp_path: Path, bare_origin: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home))
    origin = bare_origin()
    first, second = tmp_path / "first", tmp_path / "second"
    git("clone", str(origin), str(first))
    git("clone", str(origin), str(second))
    registered = _cli(
        "orchestrator-register-repo",
        str(first),
        "--workflow",
        "local",
        "--repo-type",
        "single-owner",
    )
    _cli("orchestrator-register-repo", str(second))

    assert "registered unproven" in registered.stderr
    assert next(iter(Registry().identities.values())).gate == NOOP_GATE
    migrated = subprocess.run(
        ["just", "migrate-repo-gate", f"local/{first.name}", "--gate", "make check"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "gate=make check" in migrated.stdout
    assert {entry.gate for entry in Registry().entries.values()} == {"make check"}


def test_repos_cli_migrates_v3_and_backfills_detected_gate(
    tmp_path: Path, bare_origin: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home))
    checkout = tmp_path / "checkout"
    origin = bare_origin({"justfile": "check:\n\ttrue\n"})
    git("clone", str(origin), str(checkout))
    identity = str(origin).removesuffix(".git")
    (home / "repos.json").write_text(
        json.dumps(
            {
                "version": 3,
                "identities": {
                    identity: {
                        "origin": str(origin),
                        "workflow": "local",
                        "repo_type": "single-owner",
                    }
                },
                "checkouts": {
                    f"local/{checkout.name}": {
                        "path": str(checkout.resolve()),
                        "identity": identity,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    _cli("orchestrator-repos")

    migrated = json.loads((home / "repos.json").read_text(encoding="utf-8"))
    assert migrated["version"] == 4
    assert migrated["identities"][identity]["gate"] == "just check"

    gateless = tmp_path / "gateless"
    gateless_origin = bare_origin()
    git("clone", str(gateless_origin), str(gateless))
    gateless_identity = str(gateless_origin).removesuffix(".git")
    payload = {
        "version": 3,
        "identities": {
            gateless_identity: {
                "origin": str(gateless_origin),
                "workflow": "local",
                "repo_type": "single-owner",
            }
        },
        "checkouts": {
            f"local/{gateless.name}": {
                "path": str(gateless.resolve()),
                "identity": gateless_identity,
            }
        },
    }
    (home / "repos.json").write_text(json.dumps(payload), encoding="utf-8")
    _cli("orchestrator-repos")
    migrated = json.loads((home / "repos.json").read_text(encoding="utf-8"))
    assert migrated["identities"][gateless_identity]["gate"] == NOOP_GATE

    malformed = _cli(
        "orchestrator-register-repo", str(gateless), "--gate", "bad {target}", check=False
    )
    assert malformed.returncode == 2
    assert "only the {base} placeholder" in malformed.stderr

    invalid_migration = _cli(
        "orchestrator-migrate-repo-gate", f"local/{gateless.name}", "--gate", "", check=False
    )
    assert invalid_migration.returncode == 2
    assert "non-empty" in invalid_migration.stderr
    unknown = _cli("orchestrator-migrate-repo-gate", "unknown/repo", "--gate", "true", check=False)
    assert unknown.returncode == 2
    assert "not registered" in unknown.stderr

    unsafe_shell = _cli(
        "orchestrator-register-repo",
        str(gateless),
        "--gate",
        "sh -c 'test {base} = origin/main'",
        check=False,
    )
    assert unsafe_shell.returncode == 2
    assert "must be an argv value, not command source" in unsafe_shell.stderr

    malformed_v4 = {
        "version": 4,
        "identities": {
            gateless_identity: {
                "origin": str(gateless_origin),
                "workflow": "local",
                "repo_type": "single-owner",
                "gate": "bad\0gate",
            }
        },
        "checkouts": payload["checkouts"],
    }
    (home / "repos.json").write_text(json.dumps(malformed_v4), encoding="utf-8")
    nul_gate = _cli("orchestrator-repos", check=False)
    assert nul_gate.returncode == 2
    assert "single-line command template" in nul_gate.stderr


@pytest.mark.parametrize(
    "payload, error",
    [
        (
            "not json",
            "could not load registry {path}: Expecting value: line 1 column 1 (char 0)",
        ),
        ("[]", "registry {path} must contain a JSON object"),
        (
            json.dumps({"not-a-slug": {"path": "/tmp", "origin": "url", "workflow": "remote"}}),
            "registry key 'not-a-slug' must be a normalized owner/name slug",
        ),
        (
            json.dumps({"x/y": {"path": "/tmp"}}),
            "registry entry 'x/y' must contain path, origin, and workflow",
        ),
        (
            json.dumps({"x/y": {"path": 3, "origin": "url", "workflow": "remote"}}),
            "registry entry 'x/y' path must be an absolute string",
        ),
        (
            json.dumps({"x/y": {"path": "/tmp", "origin": "", "workflow": "remote"}}),
            "registry entry 'x/y' origin must be a non-empty string",
        ),
        (
            json.dumps({"x/y": {"path": "/tmp", "origin": "url", "workflow": "other"}}),
            "registry entry 'x/y' workflow must be 'local' or 'remote'",
        ),
    ],
)
def test_repos_cli_reports_invalid_registry_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    error: str,
) -> None:
    home = tmp_path / "home"
    registry_path = home / ".ai-orchestrator" / "repos.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(payload, encoding="utf-8")
    monkeypatch.setenv("AI_ORCHESTRATOR_HOME", str(home / ".ai-orchestrator"))

    result = _cli("orchestrator-repos", check=False)

    expected = (
        "usage: orchestrator-repos [-h] [--refresh] [--audit-gate-coverage]\n"
        "                          [--format {text,json}]\n"
        f"orchestrator-repos: error: {error.format(path=registry_path)}\n"
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == expected
    assert "Traceback" not in result.stderr
