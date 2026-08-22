"""The bar a supervisor is really handed, against the repository it will judge work in.

`personas/crozier/crozier-corpus.yaml` demanded `just gate` be fully green before a
fixture change was done, and crozier has no `gate` recipe: its deterministic tier is
`check` and its judged tier is a separate `lint-llm-diff`, which is the pair
crozier's merge path requires of it — the two required checks
`config/merge-path-checks.json` inventories for that identity. That is the failure this
persona's proof clause was corrected for, and it is not a failure of the file — it is a
failure of what a *supervisor* is told. A worker never sees that bar and cannot argue
with it, so finished, gate-green work is failed against a recipe nobody can run.

So the corrected clause is proven where it takes effect. Each journey below runs a real
`oneagentgraph run` of a two-party `kind: onejudge` member built from the real
`config/onejudge.base.yaml` and the persona, reads the prompt the **supervisor side**
was actually handed out of it, and reconciles the recipes that prompt demands against
`just --dump` in crozier's own registered checkout. Only the paid model is substituted,
at the seam `tests/e2e/persona_probe.py` documents.

Both paths are driven: the tracked persona, whose delivered bar must name only
verification crozier has, and a deliberately drifted copy of it that asks for `just
gate` again, whose delivered bar must be reported — naming the persona, the recipe, and
the checkout searched. The drifted copy is what keeps the passing case honest: without
it, a reconciliation that silently found no demands at all would read the same green.

Both reconciliations a repo-specific persona is held to run against that delivered bar,
because both failures reach a supervisor the same way: the recipes it demands
(`tests/persona_recipes.py`) and the identifiers it names of the repository it reviews
(`tests/persona_identifiers.py`). They are separate checks because they are separate
failures — a recipe that disappears is a command nobody can run, while an identifier that
changes meaning is a command that still runs and now asks for the opposite of the work —
so the drifted copy below carries one drift of each kind and the report for each is read
on its own.

`tests/test_persona_recipe_drift.py` and `tests/test_persona_identifier_drift.py` ask this
of the tracked files for every repo-specific persona, which is the cheap gate; this asks it
of the prose a supervisor was handed, which is the thing that actually fails work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import JUDGE_CONFIG_NAME, PROMPT_LOG_ENV
from persona_identifiers import (
    NamedIdentifiers,
    identifiers_of,
    undefined_identifiers,
)
from persona_probe import (
    ProviderTurn,
    probe_environment,
    recorded_turns,
    run_graph,
    settlement,
    started_member,
)
from persona_recipes import RepoPersona, checkout_of, named_recipes, persona_at, undefined_recipes

from orchestrator.root import REPO_ROOT

#: Who the indirection helpers attribute their diagnostics to when one of them refuses.
INDIRECTION_CALLER = "tests/e2e/test_persona_review_bar_e2e.py"

#: The persona under test, the repository it reviews, and the member name its probe
#: graph gives it. The repository is the directory the file sits in, which is how a
#: repo-specific persona states it; asserted below rather than assumed.
CROZIER_PERSONA = REPO_ROOT / "personas" / "crozier" / "crozier-corpus.yaml"
MEMBER = "corpus"

#: The task each probe runs with. Deliberately names no `just` recipe: what the
#: supervisor demands has to come from the persona, or this reconciliation would be
#: reading its own fixture back.
PROBE_TASK = "Probe that this persona's review bar reached its supervisor."

#: The recipe the uncorrected clause demanded, which crozier does not define. Restored
#: into a copy of the persona to drive the failure path.
DRIFTED_RECIPE = "gate"

#: The file crozier registers every corpus in, named by the tracked bar, and the rename
#: put in its place to drive the identifier failure path. crozier tracks no such path.
CORPUS_REGISTRY = "tests/e2e.rs"
DRIFTED_REGISTRY = "tests/corpus-e2e.rs"


def _probe_graph(destination: Path, persona: Path) -> Path:
    """A one-member graph in the shape a repo-specific persona is dispatched in.

    Two-party `kind: onejudge`, because that is what a plan node naming this persona by
    path settles into: the file's `system_prompt` becomes the worker's role and its
    `user.persona` becomes the supervisor's bar, both merged over the real base config.
    """
    graph = destination / "review-bar-probe.yaml"
    graph.write_text(
        "version: 4\n"
        "name: review-bar-probe\n"
        "members:\n"
        f"  {MEMBER}:\n"
        "    kind: onejudge\n"
        f"    base_config: {REPO_ROOT / 'config/onejudge.base.yaml'}\n"
        f"    persona: {persona}\n"
        "    agent:\n"
        f"      oneharness_config: {REPO_ROOT / 'oneharness.toml'}\n"
        "    judge:\n"
        f"      oneharness_config: {REPO_ROOT / JUDGE_CONFIG_NAME}\n"
        "    mode: bypass\n",
        encoding="utf-8",
    )
    return graph


class DeliveredBar(NamedTuple):
    """One supervisor's delivered bar, read into what each reconciliation needs of it.

    Both views are of the same prompt: what that bar demands the repository *run*, and
    what it names *of* the repository. They travel together because one graph run
    produces both, and they stay named apart because they are reconciled apart.
    """

    demands: RepoPersona
    names: NamedIdentifiers


def _supervisor_turns(turns: list[ProviderTurn]) -> list[ProviderTurn]:
    """The turns the supervisor side took, by the one property that separates the two.

    A two-party member's judge side is the turn pinned to the judge config; every other
    turn is the agent's. Which side a demand reached matters here more than anywhere
    else, because the whole defect is that a bar reaches the supervisor and not the
    worker.
    """
    return [turn for turn in turns if Path(turn["config"] or "").name == JUDGE_CONFIG_NAME]


def _delivered_bar(tmp_path: Path, oneharness_bin: str, persona: Path) -> DeliveredBar:
    """Run `persona` as a real member and return what its supervisor was demanded of.

    Both views come out of the prompt the supervisor was handed rather than out of the
    file, which is the point: a merge that dropped the bar, or a release that stopped
    delivering it, would leave the file correct and the supervisor told something else.
    """
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = probe_environment(tmp_path, oneharness_bin, INDIRECTION_CALLER)
    prompt_log = tmp_path / f"{persona.stem}-prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)

    envelopes = run_graph(_probe_graph(tmp_path, persona), environment, PROBE_TASK)
    started = started_member(envelopes, MEMBER)
    assert started["labels"].get("persona"), (
        f"the member started under no persona label, so {persona} was not resolved:\n"
        f"{json.dumps(started, indent=2)}"
    )
    assert settlement(envelopes)["members"] == {MEMBER: "settled"}, (
        f"the probe graph did not settle its one member:\n{json.dumps(envelopes, indent=2)}"
    )

    # llmlint: ignore[tests_mirror_real_usage] No planner-facing view carries a supervisor prompt.
    supervising = _supervisor_turns(recorded_turns(prompt_log))
    assert supervising, (
        "the supervisor side took no turn, so nothing here reads the bar it was given"
    )
    delivered = "\n".join(turn["prompt"] for turn in supervising)
    return DeliveredBar(
        demands=RepoPersona(
            named=str(persona),
            repository=persona.parent.name,
            recipes=named_recipes(delivered),
        ),
        names=identifiers_of(named=str(persona), repository=persona.parent.name, prose=delivered),
    )


@pytest.fixture(scope="module")
def crozier_checkout() -> Path:
    """crozier's own registered checkout, which owns the fact these journeys read."""
    found = checkout_of(CROZIER_PERSONA.parent.name)
    if found is None:
        pytest.skip(
            f"this host holds no registered checkout of {CROZIER_PERSONA.parent.name}, so "
            "the bar its supervisor is handed cannot be reconciled against it here"
        )
    return found


@pytest.mark.reads_checkouts
@pytest.mark.xdist_group("persona-review-bar")
def test_the_delivered_crozier_bar_demands_and_names_only_what_that_repository_has(
    tmp_path: Path, oneharness_bin: str, crozier_checkout: Path
) -> None:
    """The corrected clauses, where they take effect: the supervisor's own prompt."""
    bar = _delivered_bar(tmp_path, oneharness_bin, CROZIER_PERSONA)
    delivered, named = bar.demands, bar.names

    # A subset, not the whole file: `system_prompt` is the worker's role and reaches the
    # agent side, so the recipes only it names — the fixture-repair commands — are
    # deliberately absent from what a supervisor is told. What must not happen is the
    # reverse: a demand the supervisor makes that this file never wrote.
    tracked = persona_at(CROZIER_PERSONA)
    assert delivered.recipes <= tracked.recipes, (
        "the supervisor was handed recipes this persona does not name, so the bar it is "
        f"applying is not this file's: {sorted(delivered.recipes - tracked.recipes)}"
    )
    assert {"check", "lint-llm-diff", "test-corpus-match"} <= delivered.recipes, (
        "the delivered bar no longer demands crozier's complete gate — `just check` for "
        "the deterministic tier and `just lint-llm-diff` for the judged one, which is the "
        "pair config/onevcs.rules.yml resolves for its merge path — beside the corpus "
        f"proof itself: {sorted(delivered.recipes)}"
    )

    report = undefined_recipes(delivered, crozier_checkout)
    assert report is None, report

    # The other half of the same bar: what it names *of* crozier rather than what it asks
    # crozier to run. A supervisor pointed at a file that repository does not have holds a
    # worker to prose only this host wrote, exactly as a dead recipe does.
    assert CORPUS_REGISTRY in named.paths, (
        "the delivered bar no longer points its supervisor at the file crozier registers "
        f"every corpus in, so this reconciliation reads nothing: {sorted(named.paths)}"
    )
    assert undefined_identifiers(named, crozier_checkout) is None, undefined_identifiers(
        named, crozier_checkout
    )


@pytest.mark.reads_checkouts
@pytest.mark.xdist_group("persona-review-bar")
def test_a_delivered_bar_demanding_or_naming_what_crozier_lacks_is_reported(
    tmp_path: Path, oneharness_bin: str, crozier_checkout: Path
) -> None:
    """The failure path, driven the same way: one drift of each kind in one dispatched copy.

    A copy of the tracked persona is dispatched for real with `just gate` put back into
    its proof clause and the file crozier registers every corpus in renamed to one that
    repository does not track, and what its supervisor is handed is reconciled against
    the same checkout by both reconciliations. This is the regression each correction was
    made for, so both are driven rather than described — and each report is read for all
    three facts a reader needs, because one saying only that something drifted sends them
    back through the whole investigation.
    """
    catalog = tmp_path / "personas" / CROZIER_PERSONA.parent.name
    catalog.mkdir(parents=True)
    drifted = catalog / CROZIER_PERSONA.name
    corrected = CROZIER_PERSONA.read_text(encoding="utf-8")
    assert "`just check`" in corrected, (
        "the tracked persona no longer names `just check`, so this journey cannot drift "
        "it back to the clause it was corrected from"
    )
    assert f"`{CORPUS_REGISTRY}`" in corrected, (
        f"the tracked persona no longer names `{CORPUS_REGISTRY}`, so this journey cannot "
        "drift that path to one crozier does not track"
    )
    drifted.write_text(
        corrected.replace("`just check`", f"`just {DRIFTED_RECIPE}`", 1).replace(
            CORPUS_REGISTRY, DRIFTED_REGISTRY
        ),
        encoding="utf-8",
    )

    bar = _delivered_bar(tmp_path, oneharness_bin, drifted)
    delivered, named = bar.demands, bar.names
    assert DRIFTED_RECIPE in delivered.recipes, (
        "the drifted demand did not reach the supervisor, so this journey proves nothing "
        f"about the failure path: {sorted(delivered.recipes)}"
    )

    report = undefined_recipes(delivered, crozier_checkout)
    assert report is not None, (
        f"a bar demanding `just {DRIFTED_RECIPE}` was accepted against {crozier_checkout}, "
        "which defines no such recipe"
    )
    assert str(drifted) in report
    assert f"`just {DRIFTED_RECIPE}`" in report
    assert str(crozier_checkout) in report

    # The same journey drives the identifier drift, because a bar carries both kinds of
    # name and one report must not stand in for the other.
    assert DRIFTED_REGISTRY in named.paths, (
        "the drifted path did not reach the supervisor, so the identifier half of this "
        f"journey proves nothing: {sorted(named.paths)}"
    )
    named_report = undefined_identifiers(named, crozier_checkout)
    assert named_report is not None, (
        f"a bar pointing its supervisor at `{DRIFTED_REGISTRY}` was accepted against "
        f"{crozier_checkout}, which tracks no such path"
    )
    assert str(drifted) in named_report
    assert f"`{DRIFTED_REGISTRY}`" in named_report
    assert str(crozier_checkout) in named_report
