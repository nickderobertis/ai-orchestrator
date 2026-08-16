# The command surface every rule's gate is run against by
# `tests/e2e/test_repo_registry_apply_e2e.py`.
#
# One recipe per name any gate in `config/onevcs.rules.yml` reaches, each reporting
# the arguments and the environment it was handed. Running a gate against this is what
# turns a `bash -c` script into evidence: which recipes it reaches, in what order,
# which comparison base it resolved, and whether the nextest capture workaround
# survived into each one.
#
# Every recipe reports itself and then fails when `GATE_FIXTURE_FAIL` names it. That is
# the half a surface of always-succeeding recipes cannot show: these gates are `&&`
# chains, so what a merge path depends on is that the first failing tier ends the run,
# no later tier is reached, and the non-zero status reaches `onevcs` rather than being
# swallowed. Reporting *before* failing is deliberate — it is what makes the transcript
# name the tier that stopped the gate rather than simply ending one line early.

bootstrap:
    @echo "bootstrap NX_BASE=${NX_BASE:-unset}"
    @[ "${GATE_FIXTURE_FAIL:-}" != "bootstrap" ] || exit 1

check:
    @echo "check NEXTEST=${NEXTEST_STATUS_LEVEL:-unset}"
    @[ "${GATE_FIXTURE_FAIL:-}" != "check" ] || exit 1

check-version-bump base:
    @echo "check-version-bump {{base}} NEXTEST=${NEXTEST_STATUS_LEVEL:-unset}"
    @[ "${GATE_FIXTURE_FAIL:-}" != "check-version-bump" ] || exit 1

gate base="none":
    @nx_base=${NX_BASE:-unset}; nx_head=${NX_HEAD:-unset}; nextest=${NEXTEST_STATUS_LEVEL:-unset}; echo "gate base={{base}} NX_BASE=$nx_base NX_HEAD=$nx_head NEXTEST=$nextest"
    @[ "${GATE_FIXTURE_FAIL:-}" != "gate" ] || exit 1

lint-llm-diff base:
    @echo "lint-llm-diff {{base}} NEXTEST=${NEXTEST_STATUS_LEVEL:-unset}"
    @[ "${GATE_FIXTURE_FAIL:-}" != "lint-llm-diff" ] || exit 1
