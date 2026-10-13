"""Count the installed engine's JSON host reading through the real host recipe."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from probe_run_root import run_name, run_root

from orchestrator.root import REPO_ROOT


@pytest.mark.parametrize("stop", [None, "signalled", "partly-signalled"])
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This is a cheap,
# local-only reading over one generated run and a sleep process: no live API,
# container, browser, database server or paid model. The existing budget-gate
# family owns this condition; no slow or external-service tier is added here.
def test_dispatch_count_reads_proof(tmp_path: Path, stop: str | None) -> None:
    runs = tmp_path / "runs"
    with subprocess.Popen(["sleep", "60"]) as process:
        try:
            # llmlint: ignore-block[tests_mirror_real_usage] The task requires historical
            # pre-registry run records. The installed engine always launches with a
            # registry, so its current launch interface cannot create that old layout.
            # Only the input records are built; the real count script, host recipe and
            # installed engine read them, and a real process holds the live dispatch.
            directory = run_root(
                runs, run_name(), dispatch_pid=process.pid if stop is None else None
            )
            if stop is not None:
                assert not (directory / "dispatches").exists()
                with (directory / "events.jsonl").open("a", encoding="utf-8") as journal:
                    journal.write(
                        json.dumps(
                            {
                                "v": 2,
                                "ts": "2026-01-01T00:00:02.000Z",
                                "stream": "supervision-readings-e2e",
                                "seq": 2,
                                "source": "pipeline",
                                "kind": "run-stopped",
                                "labels": {"run_id": directory.name},
                                "payload": {"teardown": stop},
                            }
                        )
                        + "\n"
                    )
            # llmlint: ignore-end[tests_mirror_real_usage]
            environment = {**os.environ, "ONEPIPELINE_RUNS_DIR": str(runs)}
            reading = json.loads(
                subprocess.check_output(
                    ["just", "host", "--json"],
                    cwd=REPO_ROOT,
                    env=environment,
                    text=True,
                    timeout=60,
                )
            )
            assert reading["root_error"] is None
            assert reading["skipped"] == [], "a malformed fixture must not pass as zero"
            if stop == "signalled":
                assert reading["dispatches"] == []
                assert [(row["run"], row["node"]) for row in reading["gone"]] == [
                    (directory.name, "probe-node")
                ]
            else:
                assert reading["dispatches"][0]["proof"] == ("held" if stop is None else "unproven")
            ran = subprocess.run(
                [str(REPO_ROOT / "scripts/budget-dispatches.sh")],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if stop == "partly-signalled":
                assert ran.returncode == 1, ran.stdout + ran.stderr
                assert ran.stdout == ""
                assert "could not be proven" in ran.stderr
                assert "Run just host --json" in ran.stderr
            else:
                assert ran.returncode == 0, ran.stderr
                assert ran.stdout == ("1\n" if stop is None else "0\n")
        finally:
            process.terminate()
            process.wait()


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


@pytest.mark.parametrize(
    ("reading", "diagnostic"),
    [
        ("not json", "invalid JSON"),
        ("[]", "must be an object"),
        ('{"schema_version": 2}', "schema_version"),
        ('{"schema_version": true}', "schema_version"),
        ('{"schema_version": 1}', "root_error"),
        ('{"schema_version": 1, "root_error": 0}', "root_error"),
        ('{"schema_version": 1, "root_error": null}', "dispatches"),
        ('{"schema_version": 1, "root_error": null, "dispatches": {}}', "dispatches"),
        ('{"schema_version": 1, "root_error": null, "dispatches": [{}]}', "proof"),
        ('{"schema_version": 1, "root_error": null, "dispatches": [0]}', "proof"),
        ('{"schema_version": 1, "root_error": null, "dispatches": [{"proof": 0}]}', "proof"),
        ('{"schema_version": 1, "root_error": "denied", "dispatches": []}', "denied"),
        (
            '{"schema_version": 1, "root_error": null, "dispatches": [{"proof": "unknown"}]}',
            "proof",
        ),
        (None, "failed"),
        ('{"schema_version": 1, "root_error": null, "dispatches": []}', "interpreter failed"),
    ],
)
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] These are cheap,
# local-only subprocess reads. UV_NO_SYNC disables dependency provisioning; the
# environment links the installed libraries and only the delegated engine is
# doubled. No live API, container, browser, database server or paid model is used.
def test_consumer_refuses_incompatible_or_failed_producer(
    tmp_path: Path, reading: str | None, diagnostic: str
) -> None:
    """A protocol producer double supplies readings the real engine cannot emit.

    The script, host recipe, wrappers and uv are real. Only the published
    engine CLI is doubled; the journey above also uses the installed engine.
    """
    import shutil
    import sys

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    command = scripts / "budget-dispatches.sh"
    for name in (
        "budget-dispatches.sh",
        "onepipeline.sh",
        "launcher-session.sh",
        "template-env.sh",
    ):
        shutil.copy2(REPO_ROOT / "scripts" / name, scripts / name)
    for name in ("justfile", "pyproject.toml", "uv.lock"):
        shutil.copy2(REPO_ROOT / name, tmp_path / name)
    templates = tmp_path / "templates"
    templates.mkdir()
    shutil.copy2(REPO_ROOT / "templates/templates.yaml", templates / "templates.yaml")
    binaries = tmp_path / ".venv" / "bin"
    binaries.mkdir(parents=True)
    (binaries / "python").symlink_to(sys.executable)
    (tmp_path / "reading").write_text(reading or "", encoding="utf-8")
    (binaries.parent / "pyvenv.cfg").symlink_to(Path(sys.prefix) / "pyvenv.cfg")
    (binaries.parent / "lib").symlink_to(Path(sys.prefix) / "lib", target_is_directory=True)
    engine = binaries / "onepipeline"
    # llmlint: ignore-block[e2e_not_mocked,tests_mirror_real_usage] The typed engine
    # cannot emit missing/retyped JSON fields or an unknown schema/proof word. Only
    # the published CLI below the consumer boundary is doubled for those protocol
    # faults, as tests/AGENTS.md permits; the script, host recipe, wrappers and uv
    # remain real. The separate run-root journey also uses the installed engine.
    engine.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        '[ "$*" = "host --json" ] || exit 2\n'
        + ('cat "$FAKE_HOST_READING"\n' if reading is not None else "exit 1\n"),
        encoding="utf-8",
    )
    engine.chmod(0o755)
    if diagnostic == "interpreter failed":
        # Break the environment after uv starts the producer, so the real count
        # consumer reaches interpreter execution rather than its existence check.
        engine.write_text(
            engine.read_text("utf-8")
            + 'rm "$BROKEN_COUNT_INTERPRETER"\n'
            + 'printf "#!/missing/count-python\\n" > "$BROKEN_COUNT_INTERPRETER"\n'
            + 'chmod +x "$BROKEN_COUNT_INTERPRETER"\n',
            encoding="utf-8",
        )
    # llmlint: ignore-end[e2e_not_mocked,tests_mirror_real_usage]
    ran = subprocess.run(
        [str(command)],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "FAKE_HOST_READING": str(tmp_path / "reading"),
            "BROKEN_COUNT_INTERPRETER": str(binaries / "python"),
        },
    )
    assert ran.returncode == 1
    assert ran.stdout == ""
    assert diagnostic in ran.stderr
    assert "repair" in ran.stderr
    if diagnostic == "interpreter failed":
        assert "run just bootstrap" in ran.stderr


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def test_a_missing_locked_interpreter_names_the_repair(tmp_path: Path) -> None:
    """An incomplete checkout cannot decode the host reading."""
    import shutil

    command = tmp_path / "scripts" / "budget-dispatches.sh"
    command.parent.mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "budget-dispatches.sh", command)
    ran = subprocess.run([str(command)], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 1
    assert ran.stdout == ""
    assert "locked Python interpreter" in ran.stderr
    assert "run just bootstrap" in ran.stderr
