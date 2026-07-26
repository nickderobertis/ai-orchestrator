"""History-label contract tests (oneharness 0.4.0 ONEHARNESS_HISTORY_LABELS)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from orchestrator.labels import (
    LABEL_ENV,
    AgentRole,
    LabelError,
    dispatched_agent_role,
    format_labels,
    graph_labels,
    main,
    merge_labels,
    parse_labels,
    validate_key,
    validate_value,
)


def test_dispatched_personas_have_deterministic_semantic_roles() -> None:
    assert dispatched_agent_role("engineer") == "worker"
    assert dispatched_agent_role("check-in") == "check-in"
    assert dispatched_agent_role("pr-author") == "pr-author"


def test_operator_docs_agent_role_taxonomy_matches_code() -> None:
    docs = " ".join(Path("docs/orchestration.md").read_text(encoding="utf-8").split())
    rendered = ", ".join(f"`{role.value}`" for role in AgentRole)
    head, tail = rendered.rsplit(", ", 1)
    assert f"The first-class agent roles are {head}, and {tail};" in docs


def test_label_env_name() -> None:
    assert LABEL_ENV == "ONEHARNESS_HISTORY_LABELS"


@pytest.mark.parametrize("key", ["a", "A9", "run_id", "round", "a.b-c_d", "z" * 64])
def test_validate_key_accepts_contract_keys(key: str) -> None:
    assert validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",  # empty
        "_leading",  # must start alphanumeric
        ".leading",
        "-leading",
        "has space",
        "has/slash",
        "café",  # non-ASCII
        "z" * 65,  # over 64
    ],
)
def test_validate_key_rejects_off_contract_keys(key: str) -> None:
    with pytest.raises(LabelError, match="history label key"):
        validate_key(key)


@pytest.mark.parametrize("value", ["x", "a b", "café", "=", "z" * 256, "1"])
def test_validate_value_accepts_contract_values(value: str) -> None:
    assert validate_value(value) == value


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("", "non-empty"),
        ("z" * 257, "exceeds 256 code points"),
        ("a\nb", "control character"),
        ("a\tb", "control character"),
        ("a\x00b", "control character"),
        ("a,b", "comma"),
    ],
)
def test_validate_value_rejects_off_contract_values(value: str, match: str) -> None:
    with pytest.raises(LabelError, match=match):
        validate_value(value)


def test_value_length_is_code_points_not_bytes() -> None:
    # 256 astral code points is 1024 UTF-8 bytes; the contract counts code points.
    assert validate_value("𝄞" * 256)
    with pytest.raises(LabelError, match="exceeds 256"):
        validate_value("𝄞" * 257)


def test_format_labels_round_trips_through_parse() -> None:
    labels = {"run_id": "run-7", "round": "2", "node": "api", "role": "agent"}
    rendered = format_labels(labels)
    assert rendered == "run_id=run-7,round=2,node=api,role=agent"
    assert parse_labels(rendered) == labels


def test_format_labels_rejects_a_bad_label_rather_than_emitting_it() -> None:
    with pytest.raises(LabelError):
        format_labels({"run_id": "a,b"})


def test_parse_labels_is_lenient_about_inherited_junk() -> None:
    # Inherited from whatever invoked us: drop what violates the contract, keep the rest.
    parsed = parse_labels("good=1,,novalue,bad key=2,_bad=3,keep=yes")
    assert parsed == {"good": "1", "keep": "yes"}


def test_parse_labels_keeps_equals_in_a_value() -> None:
    assert parse_labels("expr=a=b") == {"expr": "a=b"}


def test_merge_labels_preserves_inherited_and_lets_ours_win() -> None:
    merged = merge_labels("outer=keep,node=old", {"node": "new", "run_id": "r"})
    assert parse_labels(merged) == {"outer": "keep", "node": "new", "run_id": "r"}


def test_merge_labels_with_no_inherited_value() -> None:
    assert merge_labels(None, {"run_id": "r"}) == "run_id=r"
    assert merge_labels("", {"run_id": "r"}) == "run_id=r"


def test_graph_labels_omits_absent_components() -> None:
    # A bare `just dispatch` has no run/node; empty values would break the contract.
    assert graph_labels() == {}
    assert graph_labels(run_id="r", round_number=0) == {"run_id": "r", "round": "0"}
    assert graph_labels(run_id="r", round_number=2, node="api", step="impl") == {
        "run_id": "r",
        "round": "2",
        "node": "api",
        "step": "impl",
    }


def test_graph_labels_feed_format_labels_cleanly() -> None:
    labels = graph_labels(run_id="run-1", round_number=1, node="api", step="impl")
    assert format_labels(labels) == "run_id=run-1,round=1,node=api,step=impl"


def test_history_labels_cli_layers_labels_at_the_public_boundary() -> None:
    env = os.environ.copy()
    env[LABEL_ENV] = "outer=keep,node=old"

    result = subprocess.run(
        ["uv", "run", "orchestrator-history-labels", "node=new", "role=llmlint"],
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "outer=keep,node=new,role=llmlint\n"


def test_history_labels_main_reports_invalid_cli_input(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["not-a-pair"]) == 2
    assert "expected KEY=VALUE" in capsys.readouterr().err


def test_history_labels_main_reports_invalid_inherited_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(LABEL_ENV, "outer=keep")

    assert main(["role=bad,value"]) == 2
    assert "history label value" in capsys.readouterr().err
