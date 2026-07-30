"""Preserved evidence records a credential's name, never its value.

Two implementations apply this rule — `redact_secrets` in
`scripts/preserved-log.sh`, in front of `just check` where no virtualenv is
guaranteed, and `orchestrator.redaction` for the evidence the lifecycle
preserves. `orchestrator.redaction.SECRET_NAME_PATTERN` is the one source of the
credential-name grammar; the drift gate below fails if the shell copy differs
from it by a single byte, and the equivalence test drives every name the grammar
can produce through both implementations rather than a hand-picked few.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from orchestrator.redaction import SECRET_NAME, SECRET_NAME_PATTERN, redact, secret_values

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


def _shell_pattern() -> str:
    """The credential-name grammar as the shell copy actually spells it."""
    for line in HELPER.read_text(encoding="utf-8").splitlines():
        if line.startswith("_PRESERVED_LOG_SECRET_NAME="):
            return line.split("=", 1)[1].strip().strip("'")
    raise AssertionError("scripts/preserved-log.sh no longer names its credential grammar")


def test_the_shell_copy_of_the_credential_grammar_has_not_drifted() -> None:
    """One source, and a gate that fails on the first byte of divergence."""
    assert _shell_pattern() == SECRET_NAME_PATTERN


#: Every shape the grammar can take: each keyword, at the start and after an
#: underscore, singular and plural, plus the near-misses it must not claim.
_KEYWORDS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "API_KEY",
    "APIKEY",
    "ACCESS_KEY",
    "ACCESSKEY",
    "PRIVATE_KEY",
    "PRIVATEKEY",
    "SESSION_KEY",
    "SESSIONKEY",
    "AUTH",
)
_DECOYS = ("TOKENIZER", "KEYCHAIN", "SECRETARY", "AUTHOR", "PASSWORDLESS", "KEY", "CREDENTIALSS")


def _generated_names() -> list[str]:
    names: list[str] = []
    for keyword in _KEYWORDS:
        for plural in ("", "S"):
            names.extend(
                (
                    f"{keyword}{plural}",
                    f"CLAUDE_{keyword}{plural}",
                    f"{keyword}{plural}_PATH",
                    f"X{keyword}{plural}",
                )
            )
    names.extend(_DECOYS)
    names.extend(f"PREFIX_{decoy}" for decoy in _DECOYS)
    return names


def test_both_implementations_classify_every_generated_name_alike() -> None:
    """An example table cannot prove two regexes equal; the whole grammar can."""
    names = _generated_names()
    environ = {name: f"value-of-{index:04d}" for index, name in enumerate(names)}

    text = " ".join(f"{name}={environ[name]}" for name in names) + "\n"
    shell = _shell_redact(text, environ)
    python = redact(text, secret_values(environ))

    assert shell == python
    # And the classification itself is the thing that must agree, not just the
    # rendering: a grammar that matched nothing would also make the two equal.
    claimed = {name for name in names if f"<redacted:{name}>" in python}
    assert claimed == {name for name in names if SECRET_NAME.search(name)}
    assert "TOKEN" in claimed and "CLAUDE_TOKENS" in claimed
    assert claimed.isdisjoint(_DECOYS)
