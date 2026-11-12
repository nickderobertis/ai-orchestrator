"""`orchestrator/instruction_tokens.py`: the two instruction-token budgets' measurement.

Driven the way the pre-push hook drives it: the installed `onebudgetspec check --id` runs the
command the root `budgets.yaml` registers, from a temporary checkout holding this module and
stand-ins for the root `AGENTS.md` and `personas/planner.yaml` whose byte counts are known, so
each reported figure can be held to its definition — the text's UTF-8 bytes over the ratio
the detail reports, rounded up. The failures a budget's command can meet are driven the same
way, and the module's every line is also run in process, because the coverage floor over
`orchestrator/` is read from this tier and a subprocess records nothing into it.

`tests/e2e/budget_gate/test_instruction_tokens_budget_e2e.py` drives the same entries through
the hook's own budget step.
"""

from __future__ import annotations

import io
import json
import math
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from orchestrator import instruction_tokens
from orchestrator.root import REPO_ROOT

ROOT_BUDGETS = REPO_ROOT / "budgets.yaml"
CHECKER = Path(sys.executable).parent / "onebudgetspec"
AGENTS = "agents-md-instruction-tokens"
PLANNER = "planner-prompt-instruction-tokens"
#: What the module needs beside itself to run from another checkout.
MODULES = ("instruction_tokens.py", "root.py")
#: The detail a result carries: bytes, words and the ratio.
DETAIL = re.compile(r"(?P<bytes>\d+) B, (?P<words>\d+) words, (?P<ratio>[\d.]+) B/tok\Z")

#: A stand-in root file with multi-byte characters, so a character count would be wrong.
AGENTS_TEXT = "# AGENTS.md\n\nRules a manager acts on — kept short, naïvely.\n" * 40
#: A stand-in planner prompt, wrapped in a document whose comments and other keys are
#: not part of the prompt and must not be counted.
PROMPT = "You are the planner. Decompose the work — précisément.\n" * 30


def _tokens(size: int, ratio: str | float = instruction_tokens.BYTES_PER_TOKEN) -> int:
    """``size`` bytes over ``ratio``, rounded up, divided exactly as the ratio is written."""
    return math.ceil(Fraction(size) / Fraction(str(ratio)))


def _planner_document(prompt: str | None) -> str:
    document: dict[str, object] = {"user": {"persona": "x" * 5000}}
    if prompt is not None:
        document["system_prompt"] = prompt
    return "# a comment the prompt does not carry\n" + yaml.safe_dump(document, allow_unicode=True)


def _tracked() -> list[dict[str, object]]:
    document = yaml.safe_load(ROOT_BUDGETS.read_text(encoding="utf-8"))
    return [budget for budget in document["budgets"] if budget["id"] in (AGENTS, PLANNER)]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A checkout holding the module, the two tracked entries and both stand-ins."""
    root = tmp_path / "checkout"
    (root / "orchestrator").mkdir(parents=True)
    for name in MODULES:
        shutil.copy(REPO_ROOT / "orchestrator" / name, root / "orchestrator" / name)
    (root / ".venv").symlink_to(REPO_ROOT / ".venv")
    (root / "personas").mkdir()
    (root / "AGENTS.md").write_text(AGENTS_TEXT, encoding="utf-8")
    (root / "personas" / "planner.yaml").write_text(_planner_document(PROMPT), encoding="utf-8")
    (root / "budgets.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "budgets": _tracked()}), encoding="utf-8"
    )
    return root


def _check(checkout: Path, budget: str) -> tuple[int, dict[str, object]]:
    ran = subprocess.run(
        [str(CHECKER), "check", "--id", budget, "--output", "json", "budgets.yaml"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert ran.stdout, ran.stderr
    (result,) = json.loads(ran.stdout[ran.stdout.index("{") :])["results"]
    return ran.returncode, result


@pytest.mark.parametrize(
    ("budget", "measured"),
    [(AGENTS, AGENTS_TEXT), (PLANNER, PROMPT)],
)
def test_each_budget_reports_its_texts_bytes_over_the_ratio_rounded_up(
    checkout: Path, budget: str, measured: str
) -> None:
    returncode, result = _check(checkout, budget)

    assert returncode == 0, result
    assert result["verdict"] == "within", result
    detail = DETAIL.match(str(result["detail"]))
    assert detail is not None, result["detail"]
    size = len(measured.encode("utf-8"))
    assert int(detail["bytes"]) == size, (detail["bytes"], size)
    assert int(detail["words"]) == len(measured.split())
    assert float(detail["ratio"]) == instruction_tokens.BYTES_PER_TOKEN
    assert result["actual"] == _tokens(size, detail["ratio"]), result


def test_a_root_file_past_the_threshold_is_over(checkout: Path) -> None:
    (budget,) = [one for one in _tracked() if one["id"] == AGENTS]
    threshold = float(str(budget["threshold"]))
    size = math.ceil((threshold + 1) * instruction_tokens.BYTES_PER_TOKEN)
    (checkout / "AGENTS.md").write_text("x" * size, encoding="utf-8")

    returncode, result = _check(checkout, AGENTS)

    assert returncode == 1, result
    assert result["verdict"] == "over", result
    assert result["actual"] == _tokens(size), result


def test_a_missing_file_is_an_error_naming_it(checkout: Path) -> None:
    (checkout / "AGENTS.md").unlink()

    returncode, result = _check(checkout, AGENTS)

    assert returncode > 1, result
    assert result["verdict"] != "within", result
    assert "AGENTS.md does not exist" in str(result["error"]), result


def test_a_missing_key_is_an_error_naming_it(checkout: Path) -> None:
    (checkout / "personas" / "planner.yaml").write_text(_planner_document(None), encoding="utf-8")

    returncode, result = _check(checkout, PLANNER)

    assert returncode > 1, result
    assert "no top-level string under the key 'system_prompt'" in str(result["error"]), result


def test_outside_a_check_it_refuses_naming_the_result_file(checkout: Path) -> None:
    """Run as the registered command but not by onebudgetspec: no result file is named."""
    ran = subprocess.run(
        [".venv/bin/python", "-m", "orchestrator.instruction_tokens", "AGENTS.md"],
        cwd=checkout,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert ran.returncode == 1, ran.stdout + ran.stderr
    assert "ONEBUDGETSPEC_RESULT names no result file" in ran.stderr, ran.stderr


# In process, for the coverage floor: each arm the journeys above reach by subprocess.


@pytest.fixture
def in_process(checkout: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(instruction_tokens, "REPO_ROOT", checkout)
    result = tmp_path / "result.json"
    monkeypatch.setenv(instruction_tokens.RESULT_VARIABLE, str(result))
    return result


def test_main_reports_the_file_and_the_key(in_process: Path) -> None:
    assert instruction_tokens.main(["AGENTS.md"], io.StringIO()) == 0
    size = len(AGENTS_TEXT.encode("utf-8"))
    assert json.loads(in_process.read_text(encoding="utf-8")) == {
        "value": _tokens(size),
        "detail": f"{size} B, {len(AGENTS_TEXT.split())} words, 3.288 B/tok",
    }
    assert instruction_tokens.main(["personas/planner.yaml", "--yaml-key", "system_prompt"]) == 0
    assert json.loads(in_process.read_text(encoding="utf-8"))["value"] == _tokens(
        len(PROMPT.encode("utf-8"))
    )


@pytest.mark.parametrize(
    ("content", "argv", "said"),
    [
        (None, ["missing.md"], "missing.md does not exist"),
        (b"\xff\xfe", ["bad.md"], "could not be read as UTF-8 text"),
        (b"a: [unclosed", ["bad.yaml", "--yaml-key", "a"], "is not a YAML document"),
        (b"- a list\n", ["bad.yaml", "--yaml-key", "a"], "no top-level string under the key"),
        (b"a: 3\n", ["bad.yaml", "--yaml-key", "a"], "no top-level string under the key 'a'"),
    ],
)
def test_main_refuses_what_it_cannot_measure_and_reports_nothing(
    in_process: Path, checkout: Path, content: bytes | None, argv: list[str], said: str
) -> None:
    if content is not None:
        (checkout / argv[0]).write_bytes(content)
    err = io.StringIO()

    assert instruction_tokens.main(argv, err) == 1
    assert said in err.getvalue(), err.getvalue()
    assert not in_process.exists()


def test_main_without_a_result_file_writes_nothing(
    in_process: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(instruction_tokens.RESULT_VARIABLE)

    assert instruction_tokens.main(["AGENTS.md"]) == 1
    assert "ONEBUDGETSPEC_RESULT names no result file" in capsys.readouterr().err
    assert not in_process.exists()


def test_a_text_of_whole_tokens_is_not_rounded_up() -> None:
    assert instruction_tokens.tokens("") == 0
    assert instruction_tokens.tokens("x" * 3288) == 1000
    assert instruction_tokens.tokens("x" * 3289) == 1001
