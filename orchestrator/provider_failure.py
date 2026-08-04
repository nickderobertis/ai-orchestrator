"""The one model of a provider refusal: its closed vocabularies and its record.

Declared apart from `dispatch`, which classifies, and from `runs`/`telemetry`,
which record and serve, because all three need the same shape and `dispatch`
already imports `runs` — so the contract cannot live in either without a cycle.
`scripts/check-dag-state-contract.py` reconciles the two vocabularies here against
their `dag-model` and `docs/dag-ui/design.md` copies.
"""

from __future__ import annotations

import re
from typing import Any, Literal, NotRequired, TypedDict, cast

#: Every identity the role chains configure, and so the only names a refusal may be
#: attributed to. `tests/test_harness_routing.py` reconciles this against
#: `oneharness*.toml`, which is what makes it safe for the classifier below to
#: resolve against: one roster, gated once, rather than a second copy in a regex.
IDENTITIES = (
    "claude-code:alternate",
    "claude-code:alternate2",
    "codex",
    "codex:alternate",
    "claude-code:primary",
)
#: What an operator calls the unsuffixed account in a chain. Diagnostics and people
#: both write "codex:primary" for the identity the configs spell `codex`, so it is
#: accepted as a spelling of that identity rather than resolved to a sixth one that
#: no config declares — otherwise a rollup would name `codex:primary` while the
#: provider-health block beside it named `codex`, for the same account.
UNSUFFIXED_VARIANT = "primary"


def _spellings() -> list[tuple[str, str]]:
    """Every accepted spelling paired with the configured identity it means."""
    pairs = [(identity, identity) for identity in IDENTITIES]
    pairs += [
        (f"{identity}:{UNSUFFIXED_VARIANT}", identity)
        for identity in IDENTITIES
        if ":" not in identity
    ]
    # Longest first, so `codex:alternate` is never read as a bare `codex`.
    return sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)


def resolve_identity(text: str) -> tuple[str, str, str]:
    """Which configured identity a diagnostic names: harness, variant, identity.

    Resolved against the gated roster instead of a second hard-coded one, so a
    renamed or added identity cannot be attributed to a name nothing configures.
    An unrecognised diagnostic stays `unknown` rather than being guessed at.
    """
    for spelling, identity in _spellings():
        if re.search(rf"(?<![\w:-]){re.escape(spelling)}(?![\w-])", text, re.I):
            harness, _, variant = identity.partition(":")
            return harness, variant or UNSUFFIXED_VARIANT, identity
    return "unknown", "unknown", "unknown"


#: Which of onejudge's two conversation sides — plus the lint tier that runs beside
#: them — the refusal came from. A planner reading "quota" needs this first: the
#: judge chain and the agent chain prefer different identities, so a fix aimed at
#: the wrong side changes nothing.
ConversationSide = Literal["agent", "judge", "llmlint"]
#: Why the provider refused, closed so a client can switch on it exhaustively.
#: `quota_at_launch` fell through to the next identity in the chain and cost only
#: time; `quota_mid_conversation` could not, because the conversation was already
#: bound to the identity that refused it. `harness_exit` is the unclassified
#: remainder and carries the harness's own structured error payload where it
#: reported one.
ProviderFailureCause = Literal[
    "quota_at_launch",
    "quota_mid_conversation",
    "stale_session_resume",
    "rate_limit",
    "harness_exit",
]


class ProviderFailure(TypedDict, total=False):
    """What a provider refusal is, everywhere this repository carries one.

    Modelled rather than passed as a bare mapping because the shape is this
    repository's own closed contract — `packages/dag-model/src/index.ts` spells out
    the same fields for the client that parses it — and because every consumer
    reads it by key: the rollups, the planner views, and the served failure record.

    The first five are always classified. The rest are evidence the harness may or
    may not have given, so they are optional: a refusal that stated no reset time
    must still be recorded, with that silence visible rather than invented.
    """

    side: ConversationSide
    harness: str
    variant: str
    identity: str
    cause: ProviderFailureCause
    #: Bounded tail of what the harness actually printed, kept because a classified
    #: cause is a summary and the operator still needs the words underneath it.
    raw_tail: str
    reset_time: NotRequired[str]
    #: The conversation id a resume asked for and the harness no longer had.
    missing_session_id: NotRequired[str]
    #: How long a watchdog-killed retry loop waited before it was ended.
    wait_seconds: NotRequired[float]
    failure_kind: NotRequired[str]
    #: The harness's own structured error payload, bounded — claude-code's `subtype`
    #: and `errors[]`, for an exit nothing else classified.
    structured_error: NotRequired[dict[str, Any]]
    #: Set when the agent side was recorded in history and the judge side was not.
    #: Derived from the recorded sessions, never written by the classifier.
    judge_unrecorded: NotRequired[bool]


def journalled(attribution: ProviderFailure | None) -> dict[str, Any]:
    """The `failure_attribution` fragment a journal detail carries, or nothing.

    The journal's own value type is a recursive JSON union, and a `TypedDict` is
    invariant in its values, so a checker cannot see that this closed shape is a
    subtype of it. Every value here is a string, a float, or a bounded JSON object,
    which is exactly what that union admits — so the cast is the narrow, one-place
    statement of a fact the type system cannot derive, rather than a loosening
    repeated at each of the four journal sites that record a refusal.
    """
    if not attribution:
        return {}
    return {"failure_attribution": cast("dict[str, Any]", attribution)}
