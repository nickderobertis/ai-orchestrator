"""Preserved evidence records a credential's name, never its value.

Two implementations apply this rule — `redact_secrets` in
`scripts/preserved-log.sh`, in front of `just check` where no virtualenv is
guaranteed, and `orchestrator.redaction` for the evidence the lifecycle
preserves. The last test here is what keeps them one behavior: the shell filter
is driven for real and compared against the Python function over the same table.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from orchestrator.redaction import redact, secret_values

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "preserved-log.sh"

TOKEN = "sk-ant-oat01-not-a-real-credential"
CASES: list[tuple[str, dict[str, str], str]] = [
    (
        "credential-shaped names lose their value",
        {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN},
        f"authorization: Bearer {TOKEN}\n",
    ),
    (
        "a name that merely contains a keyword is left alone",
        {"TOKENIZER_MODE": "greedy-splitting", "KEYCHAIN_PATH": "/var/keychains"},
        "TOKENIZER_MODE=greedy-splitting KEYCHAIN_PATH=/var/keychains\n",
    ),
    (
        "a value too short to be a credential cannot corrupt the evidence",
        {"CI_SECRET": "on"},
        "coverage on 12 of 12 files\n",
    ),
    (
        "several credentials in one line are each named",
        {"GH_TOKEN": "ghp_first_credential", "ANTHROPIC_API_KEY": "sk-second-credential"},
        "gh=ghp_first_credential anthropic=sk-second-credential\n",
    ),
    (
        "a credential whose value contains a shorter one is replaced whole",
        {"OUTER_TOKEN": "prefix-inner-credential", "INNER_TOKEN": "inner-credential"},
        "outer=prefix-inner-credential inner=inner-credential\n",
    ),
    (
        "ordinary output passes through untouched",
        {"HOME": "/home/agent"},
        "TOTAL 11551 402 96.42%\nSuccess: no issues found\n",
    ),
]


def _shell_redact(text: str, environ: dict[str, str]) -> str:
    """Run the real shell filter the way `just check` pipes output through it."""
    proc = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{HELPER}"; redact_secrets'],
        input=text,
        env={"PATH": "/usr/bin:/bin", **environ},
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout


@pytest.mark.parametrize(
    ("environ", "text"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_python_and_shell_redaction_agree(environ: dict[str, str], text: str) -> None:
    assert _shell_redact(text, environ) == redact(text, secret_values(environ))


def test_a_credential_value_is_replaced_by_its_name() -> None:
    assert (
        redact(f"Bearer {TOKEN}", secret_values({"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}))
        == "Bearer <redacted:CLAUDE_CODE_OAUTH_TOKEN>"
    )


def test_a_multiline_value_is_not_used_as_a_replacement_key() -> None:
    """A pasted key would otherwise match line-by-line and shred the evidence."""
    assert secret_values({"PRIVATE_KEY": "-----BEGIN-----\nabcdefgh\n-----END-----"}) == []


def test_longer_credentials_are_offered_first() -> None:
    values = secret_values({"A_TOKEN": "short-one-value", "B_TOKEN": "a-much-longer-credential"})

    assert [name for name, _ in values] == ["B_TOKEN", "A_TOKEN"]


def test_the_shell_filter_streams_before_its_input_closes(tmp_path: Path) -> None:
    """A filter that buffered to EOF would put the running-run log back out of reach."""
    log = tmp_path / "streamed.log"
    script = (
        f'set -euo pipefail; source "{HELPER}"; '
        f'{{ echo first; while [[ ! -e "{tmp_path / "release"}" ]]; do sleep 0.05; done; '
        f"echo second; }} | redact_secrets > {log}"
    )
    with subprocess.Popen(["bash", "-c", script]) as process:
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if log.exists() and log.read_text() == "first\n":
                    break
                time.sleep(0.05)
            else:  # pragma: no cover - only reached when the filter never streams
                pytest.fail("the filter withheld its first line until its input closed")
            assert process.poll() is None
        finally:
            (tmp_path / "release").touch()

    assert log.read_text() == "first\nsecond\n"
