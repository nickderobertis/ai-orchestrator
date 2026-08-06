"""History-label contract tests (oneharness 0.4.0 ONEHARNESS_HISTORY_LABELS)."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from functools import cache

import pytest

from orchestrator import REPO_ROOT
from orchestrator.labels import (
    LABEL_ENV,
    MAX_VALUE_CODEPOINTS,
    LabelError,
    format_labels,
    graph_labels,
    main,
    merge_labels,
    parse_labels,
    validate_key,
    validate_value,
)


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
    # An untracked lifecycle run has no run/node; empty values would break the contract.
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


def test_history_labels_main_prints_merged_labels(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(LABEL_ENV, "outer=keep")

    assert main(["role=agent"]) == 0
    assert capsys.readouterr().out == "outer=keep,role=agent\n"


def test_history_labels_main_reports_invalid_inherited_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(LABEL_ENV, "outer=keep")

    assert main(["role=bad,value"]) == 2
    assert "history label value" in capsys.readouterr().err


#: How `scripts/llmlint-judge.sh` writes the second opinion of this contract that it
#: applies to the rendered value before exporting it. That opinion exists because the
#: renderer is reached through `PATH` and can be replaced — which is what
#: `tests/e2e/test_llmlint_cache_e2e.py::test_the_recipe_refuses_unusable_harness_history_labels`
#: drives — so the recipe cannot simply trust what came back. Lifting the guard out of
#: the script instead of restating it here is what makes the sweeps below a gate on the
#: shell that actually runs rather than on a copy of it.
_RECIPE_GUARD = re.compile(
    r"key='(?P<key>[^']*)'\nvalue='(?P<value>[^']*)'\n(?P<condition>\[\[ .*?\]\])"
)


@cache
def _recipe_guard_program() -> str:
    """The recipe's label guard as a runnable bash program over ``$1``."""
    source = (REPO_ROOT / "scripts" / "llmlint-judge.sh").read_text(encoding="utf-8")
    guard = _RECIPE_GUARD.search(source)
    assert guard is not None, (
        "scripts/llmlint-judge.sh no longer states its label guard as `key=`, `value=` and a "
        "`[[ ... ]]` condition; lift its new form here so the two opinions stay reconciled"
    )
    return f"labels=\"$1\"\nkey='{guard['key']}'\nvalue='{guard['value']}'\n{guard['condition']}\n"


def _guard_accepts(labels: str) -> bool:
    """Whether the recipe would export ``labels`` rather than refuse them."""
    return (
        subprocess.run(
            ["bash", "-c", _recipe_guard_program(), "llmlint-judge-guard", labels], check=False
        ).returncode
        == 0
    )


def _accepts(validator: Callable[[str], str], candidate: str) -> bool:
    try:
        validator(candidate)
    except LabelError:
        return False
    return True


#: The separator, and so the one character neither sweep below can ask an alphabet
#: question about: written into a key it stops being part of that key on both sides of
#: the contract at once. `test_the_separator_belongs_to_no_alphabet` states that
#: instead.
_SEPARATOR = "="


def test_the_recipe_guard_accepts_exactly_this_modules_key_alphabet() -> None:
    """Character by character, in both the leading position and a later one.

    The two positions have different alphabets — a key starts alphanumeric and may
    then also carry a dot, an underscore or a hyphen — so a sweep that only tried one
    of them would miss half of any future change. Sweeping rather than listing
    examples is what makes this a gate: it fails on any change to either side, not
    only on the ones someone thought to write down.
    """
    disagreements = [
        (key, expected)
        for codepoint in range(1, 128)
        if chr(codepoint) != _SEPARATOR
        for key in (chr(codepoint), f"a{chr(codepoint)}")
        if _guard_accepts(f"{key}=v") != (expected := _accepts(validate_key, key))
    ]

    assert disagreements == []


def test_the_separator_belongs_to_no_alphabet() -> None:
    """Both sides read the first `=` as the separator and leave the rest to the value."""
    assert parse_labels("a=b=c") == {"a": "b=c"}
    assert _guard_accepts("a=b=c")


def test_the_recipe_guard_accepts_exactly_this_modules_value_alphabet() -> None:
    """The same sweep for values, where the contract is stated by exclusion.

    A value is anything that is not empty, not a control character and not a comma,
    so the alphabet is the whole of ASCII minus three exclusions, and a sweep is the
    only way to say all three are still drawn in the same places.
    """
    disagreements = [
        (codepoint, expected)
        for codepoint in range(1, 128)
        if _guard_accepts(f"k={chr(codepoint)}")
        != (expected := _accepts(validate_value, chr(codepoint)))
    ]

    assert disagreements == []


def test_the_recipe_guard_passes_every_shape_this_module_renders() -> None:
    """What the recipe actually receives, rather than one pair at a time.

    The sweeps above say nothing about the separator, and a guard that accepted a
    single pair but not a list would fail every dispatch that inherited one. The last
    mapping is the shape that did: an inherited label with a hyphen in its key and a
    space in its value, which this module deliberately passes through.
    """
    rendered = [
        format_labels(labels)
        for labels in (
            {"role": "llmlint"},
            {"run_id": "2809132-a8028463", "round": "1", "node": "watchdog-coverage"},
            {"ticket-id": "ENG 123", "agent.role": "worker", "role": "llmlint"},
        )
    ]

    assert [value for value in rendered if not _guard_accepts(value)] == []


@pytest.mark.parametrize(
    "output", ["", "not labels at all", "=v", "k=", "k=v,", ",k=v", "k=v,,k2=v2", "k v"]
)
def test_the_recipe_guard_still_refuses_output_that_is_not_labels(output: str) -> None:
    """The guard's whole reason to exist: a renderer on `PATH` that printed something else."""
    assert not _guard_accepts(output)


def test_the_recipe_guard_is_deliberately_wider_than_the_value_length_rule() -> None:
    """The one rule the guard does not mirror, recorded here rather than left to be found.

    `validate_value` caps a value at 256 *code points*. Bash's `=~` counts whatever
    its locale calls a character, so an interval quantifier there would reject a short
    non-ASCII value this module accepts — narrower than the contract, which is the
    failure this guard exists not to reproduce. So it bounds no length at all, and
    this module stays the only thing that does.
    """
    too_long = "a" * (MAX_VALUE_CODEPOINTS + 1)

    with pytest.raises(LabelError):
        validate_value(too_long)
    assert _guard_accepts(f"k={too_long}")
