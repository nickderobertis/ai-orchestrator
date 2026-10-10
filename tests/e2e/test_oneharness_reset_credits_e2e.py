"""The adopted `oneharness usage` reports a Codex identity's banked reset credits.

An identity at 100% of its weekly window with unspent reset credits is not exhausted,
and a credit nobody notices expires unused, so the pre-flight headroom read this host
runs across its identities has to show them. The release `config/oneharness.version`
pins carries that read (oneharness#1437); this journey holds the CLI this checkout
installs from the pin to it, in both of the report's formats.

Codex is the one thing doubled, at its process boundary: a stand-in `codex app-server
--stdio` that answers `initialize` and `account/rateLimits/read` over JSON-RPC. Its
answer is shaped from oneharness's own `tests/fixtures/codex-rate-limits.schema.json`
and the elided sample its change was written against, never from a real account.
Configuration is disabled, so none of this host's identities is read.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from waits import timeout as e2e_timeout

#: 2026-10-03T04:00:00Z, 2026-10-24T00:00:00Z and 2026-11-06T21:20:00Z as codex's epoch
#: seconds; the soonest is listed second so a renderer reading the first row is caught.
EXPIRIES = (1_792_800_000, 1_791_000_000, 1_794_000_000)
SOONEST_EXPIRY = "2026-10-03T04:00:00Z"
AVAILABLE_COUNT = 3

RATE_LIMITS = {
    "rateLimitsByLimitId": {
        "codex": {
            "limitId": "codex",
            "limitName": None,
            "primary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": 1_792_000_000},
            "secondary": None,
            "planType": "pro",
        }
    },
    "rateLimitResetCredits": {
        "availableCount": AVAILABLE_COUNT,
        "credits": [
            {
                "id": f"RateLimitResetCredit_…{suffix}",
                "resetType": "codexRateLimits",
                "status": "available",
                "grantedAt": 1_790_000_000,
                "expiresAt": expiry,
                "title": "Full reset",
                "description": None,
            }
            for suffix, expiry in zip("abc", EXPIRIES, strict=True)
        ],
    },
}

#: Answers by JSON-RPC id, like `codex app-server`, and refuses any other argv so a
#: probe that stopped speaking app-server cannot read as an answer.
# llmlint: ignore-block[e2e_not_mocked] Codex is the paid provider, the one boundary AGENTS.md's realistic-tests invariant doubles: a real account cannot be made to answer with a fixed credit list on demand, and the subject — the installed `oneharness` CLI's probe, parser and both renderers — runs for real.  # noqa: E501 - a directive is one line
STAND_IN = f"""#!{sys.executable}
import json, sys

RATE_LIMITS = json.loads({json.dumps(json.dumps(RATE_LIMITS))})
if sys.argv[1:] != ["app-server", "--stdio"]:
    sys.exit(f"stand-in codex: unexpected argv {{sys.argv[1:]}}")
for line in sys.stdin:
    request = json.loads(line)
    match request.get("method"):
        case "initialize":
            result = {{"userAgent": "stand-in-codex"}}
        case "account/rateLimits/read":
            result = RATE_LIMITS
        case _:
            continue
    print(json.dumps({{"jsonrpc": "2.0", "id": request["id"], "result": result}}), flush=True)
"""
# llmlint: ignore-end[e2e_not_mocked]


# llmlint: ignore-block[shell_test_tiers_stay_split] A pytest journey, not a shell suite: the one tool it runs is the `oneharness` this workspace's own lockfile installs into `.venv` from `config/oneharness.version`, against a local stand-in, in well under a second, so a project of its own would isolate no cost; tests/e2e/test_oneharness_bin_override_e2e.py drives the same binary in this tier.  # noqa: E501 - a directive is one line
def _usage(oneharness_bin: str, tmp_path: Path, output_format: str) -> str:
    stand_in = tmp_path / "codex"
    stand_in.write_text(STAND_IN, encoding="utf-8")
    stand_in.chmod(0o755)
    path = f"{Path(sys.executable).parent}:/usr/bin:/bin"
    assert shutil.which("codex", path=path) is None, f"a real `codex` is reachable on {path}"
    completed = subprocess.run(
        [
            oneharness_bin,
            "usage",
            "--no-config",
            "--harness",
            "codex",
            "--bin",
            f"codex={stand_in}",
            "--format",
            output_format,
        ],
        cwd=tmp_path,
        # Built from nothing, so no inherited `ONEHARNESS_*` or `CODEX_HOME` reaches it.
        env={"HOME": str(tmp_path), "PATH": path, "ONEHARNESS_NO_CONFIG": "1"},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


# llmlint: ignore-end[shell_test_tiers_stay_split]


def test_usage_reports_a_codex_identitys_reset_credits_as_json(
    oneharness_bin: str, tmp_path: Path
) -> None:
    report = json.loads(_usage(oneharness_bin, tmp_path, "json"))
    (identity,) = report["identities"]
    assert identity["harness"] == "codex"
    assert identity["availability"]["state"] == "available", identity
    credits = identity["reset_credits"]
    assert credits["state"] == "reported", credits
    assert credits["available_count"] == AVAILABLE_COUNT
    assert [credit["expires_at"] for credit in credits["credits"]] == [
        "2026-10-24T00:00:00Z",
        SOONEST_EXPIRY,
        "2026-11-06T21:20:00Z",
    ]
    assert {credit["status"] for credit in credits["credits"]} == {"available"}


def test_usage_text_names_the_count_and_soonest_expiry(oneharness_bin: str, tmp_path: Path) -> None:
    text = _usage(oneharness_bin, tmp_path, "text")
    lines = [line.strip() for line in text.splitlines() if "reset credits:" in line]
    assert lines == [
        f"reset credits: {AVAILABLE_COUNT} available · soonest expires {SOONEST_EXPIRY}"
    ], text
