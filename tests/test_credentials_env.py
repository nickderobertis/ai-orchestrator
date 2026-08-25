from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from credential_dialect import DIALECT, Rule

from orchestrator.root import REPO_ROOT

HELPER = REPO_ROOT / "scripts" / "credentials-env.sh"
ONEPIPELINE = REPO_ROOT / "scripts" / "onepipeline.sh"
PRESERVED_LOG = REPO_ROOT / "scripts" / "preserved-log.sh"


def _load(root: Path, contents: str | None, environment: dict[str, str] | None = None):
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / HELPER.name
    helper.write_text(HELPER.read_text(encoding="utf-8"), encoding="utf-8")
    if contents is not None:
        (root / ".env").write_text(contents, encoding="utf-8")
    return subprocess.run(
        ["bash", "-c", f'source "{helper}"; export_host_credentials test; env -0'],
        env={"PATH": "/usr/bin:/bin", **(environment or {})},
        capture_output=True,
        check=False,
    )


def test_credentials_file_exports_values_without_overriding_the_environment(tmp_path: Path) -> None:
    loaded = _load(
        tmp_path,
        "# host credentials\nGH_PROJECTS_TOKEN=file-value\nEMPTY_VALUE=\n",
        {"GH_PROJECTS_TOKEN": "process-value"},
    )
    assert loaded.returncode == 0, loaded.stderr.decode()
    environment = dict(
        entry.split("=", 1) for entry in loaded.stdout.decode().rstrip("\0").split("\0")
    )
    assert environment["GH_PROJECTS_TOKEN"] == "process-value"
    assert environment["EMPTY_VALUE"] == ""


def test_absent_and_empty_credentials_files_are_ordinary(tmp_path: Path) -> None:
    assert _load(tmp_path / "absent", None).returncode == 0
    assert _load(tmp_path / "empty", "").returncode == 0


def test_malformed_line_is_refused_without_printing_a_credential_value(tmp_path: Path) -> None:
    secret = "value-that-must-not-appear"
    refused = _load(tmp_path, f"GH_PROJECTS_TOKEN={secret}\nnot-an-assignment\n")
    diagnostic = refused.stderr.decode()
    assert refused.returncode == 2
    assert "line 2" in diagnostic
    assert "KEY=VALUE" in diagnostic
    assert secret not in diagnostic


def test_read_only_onepipeline_view_does_not_load_a_malformed_file(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / ONEPIPELINE.name).write_text(
        ONEPIPELINE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (scripts / HELPER.name).write_text(HELPER.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / ".env").write_text("not-an-assignment\n", encoding="utf-8")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    uv.chmod(0o755)

    viewed = subprocess.run(
        ["bash", str(scripts / ONEPIPELINE.name), "status", "a-run"],
        env={"PATH": f"{binaries}:/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert viewed.returncode == 0
    assert viewed.stdout == "run onepipeline status a-run\n"
    assert viewed.stderr == ""


@pytest.mark.parametrize("rule", DIALECT, ids=lambda rule: rule.name)
def test_the_credentials_file_is_read_in_the_dialect_this_repository_declares(
    rule: Rule, tmp_path: Path
) -> None:
    """The real loader, driven over every rule of the dialect.

    The rows come from `tests/credential_dialect.py` rather than being restated here,
    so this and `tests/test_credential_dialect_drift.py` cannot disagree about what the
    dialect is — two hand-maintained copies drift together and stay green, which is the
    failure a copied shape has.
    """
    loaded = _load(tmp_path, rule.written)
    assert loaded.returncode == 0, loaded.stderr.decode()
    environment = dict(
        entry.split("=", 1) for entry in loaded.stdout.decode().rstrip("\0").split("\0")
    )
    assert environment["GH_PROJECTS_TOKEN"] == rule.expected


def test_a_value_from_the_file_is_redacted_out_of_preserved_output(tmp_path: Path) -> None:
    """The existing redaction rule covers the new source, because the source is the environment.

    `scripts/preserved-log.sh` and `orchestrator/redaction.py` both hide
    credential-shaped values read out of the process environment, and this loader is
    what puts the file's names there — so nothing had to be taught about `.env` for a
    gate log or a captured transcript to stop carrying its values. Composed here
    against both real scripts, because "by construction" is the kind of claim that
    stops being true the moment either one starts reading a narrower source.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for source in (HELPER, PRESERVED_LOG):
        (scripts / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    secret = "ghp_value_that_must_not_be_preserved"
    (tmp_path / ".env").write_text(f"GH_PROJECTS_TOKEN={secret}\n", encoding="utf-8")

    preserved = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{scripts / HELPER.name}"; export_host_credentials test; '
            f'source "{scripts / PRESERVED_LOG.name}"; '
            f'printf "a log line carrying %s\\n" "$GH_PROJECTS_TOKEN" | redact_secrets',
        ],
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert preserved.returncode == 0, preserved.stderr
    assert secret not in preserved.stdout
    assert preserved.stdout == "a log line carrying <redacted:GH_PROJECTS_TOKEN>\n"


def test_a_call_without_the_calling_launcher_is_refused(tmp_path: Path) -> None:
    """The label is required, so a diagnostic always names the launch it came from.

    Both launchers put their own name in, and a refusal that arrived unattributed would
    leave an operator reading a credentials complaint with no idea which of them made
    it — the same reason `scripts/ask-manager-env.sh` requires one.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    helper = scripts / HELPER.name
    helper.write_text(HELPER.read_text(encoding="utf-8"), encoding="utf-8")

    refused = subprocess.run(
        ["bash", "-c", f'source "{helper}"; export_host_credentials'],
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode != 0
    assert "the name of the calling launcher is required" in refused.stderr
    assert "pass it as the first argument" in refused.stderr, (
        "the refusal says what is missing but not how to supply it"
    )


@pytest.mark.parametrize("shape", ["directory", "unreadable"])
def test_a_credential_file_that_cannot_be_read_is_refused_rather_than_skipped(
    shape: str, tmp_path: Path
) -> None:
    """A file that is there and unusable is the opposite of an absent one.

    An absent `.env` means every name is already exported or this host has none to give,
    so it refuses nothing. A `.env` that exists and cannot be read means somebody meant
    to supply these names — treating that as ordinary would launch a run whose dispatches
    silently lack the credentials the operator placed there.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    helper = scripts / HELPER.name
    helper.write_text(HELPER.read_text(encoding="utf-8"), encoding="utf-8")
    credentials = tmp_path / ".env"
    if shape == "directory":
        credentials.mkdir()
    else:
        credentials.write_text("GH_PROJECTS_TOKEN=unreachable\n", encoding="utf-8")
        credentials.chmod(0o000)

    refused = subprocess.run(
        ["bash", "-c", f'source "{helper}"; export_host_credentials test'],
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode == 2, refused.stderr
    assert "is not a readable regular file" in refused.stderr
    assert "fix its type or permissions" in refused.stderr


def test_a_name_no_shell_could_export_is_refused_without_printing_a_value(
    tmp_path: Path,
) -> None:
    """A key that is not a variable name is refused where it is written, naming the line.

    Exporting it is impossible and skipping it would report the credential as
    mysteriously absent, somewhere far away from the file that defines it. The valid
    line above it is what proves the refusal is not merely the loader giving up early:
    its value is in the file, and still absent from the diagnostic.
    """
    secret = "ghp_value_that_must_not_appear"
    refused = _load(tmp_path, f"GH_PROJECTS_TOKEN={secret}\n9NOT_A_NAME=x\n")

    assert refused.returncode == 2
    diagnostic = refused.stderr.decode()
    assert "line 2" in diagnostic
    assert "shell environment name" in diagnostic
    assert secret not in diagnostic


def test_a_helper_that_cannot_be_loaded_refuses_the_launch_attributably(tmp_path: Path) -> None:
    """A helper that passes the readability check and then fails to load still names itself.

    The check before it answers "is the file there and readable", which a corrupt or
    half-written one passes. Left to `set -e`, that becomes a bare shell syntax error
    naming a file the operator never asked about and no action to take — so the launch
    handles it and says which helper, and how to restore it. The read-only view is driven
    in the same state to show it is unaffected: it sources nothing, so there is nothing
    for a broken helper to break.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / ONEPIPELINE.name).write_text(
        ONEPIPELINE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (scripts / HELPER.name).write_text("this is ( not valid bash\n", encoding="utf-8")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\n", encoding="utf-8")
    uv.chmod(0o755)
    environment = {"PATH": f"{binaries}:/usr/bin:/bin"}

    refused = subprocess.run(
        ["bash", str(scripts / ONEPIPELINE.name), "start", "a-plan.json"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode == 2, refused.stdout
    assert "could not be loaded" in refused.stderr, refused.stderr
    assert HELPER.name in refused.stderr, refused.stderr
    assert "just bootstrap" in refused.stderr, refused.stderr

    viewed = subprocess.run(
        ["bash", str(scripts / ONEPIPELINE.name), "status", "a-run"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert viewed.returncode == 0, viewed.stderr
    assert viewed.stdout == "run onepipeline status a-run\n"
    assert viewed.stderr == ""
