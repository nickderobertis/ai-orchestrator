# shellcheck shell=bash
# The ONE statement of what the two ends of the ask-manager channel must agree on,
# sourced by `scripts/ask-manager.sh` — which asks the question and reads the answer —
# and by `scripts/channel-reply.sh`, which sends it. Three things: what a run or node
# may be before it is passed on, the prefix a question of this wrapper's is recognized
# by, and what a reply must carry to be an answer at all.
#
# Two processes decide the same thing about one envelope, at opposite ends of the
# channel and minutes apart. The asking wrapper decides whether what came back is a
# ruling it may act on; the replying recipe decides whether what is being sent can
# answer the question waiting. A second copy of that rule is what would let the two
# disagree — and disagreement here is silent in the worst direction: a reply the
# recipe waved through and the wrapper then discarded is reported `delivered` to the
# manager and read by nobody, which is exactly the failure the recipe exists to close.
#
# So the rule lives here and each side reads it, rather than each side restating it.

# Strict mode is established here rather than inherited from whichever caller sourced
# this, exactly as scripts/ask-manager-env.sh does: nothing below may fall through to
# leaving a caller with an empty rule, which would judge every envelope usable.
set -euo pipefail

#: The prefix `scripts/ask-manager.sh` mints into every correlation token, and so the
#: mark by which a pending surface is recognized as one of its questions. Declared here
#: rather than in the wrapper because both ends now read it: the wrapper builds the
#: pattern it matches an answer against from it, and the reply recipe looks for it in
#: the surface that is waiting.
# shellcheck disable=SC2034  # read by the sourcing script, not by this file
ASK_MANAGER_TOKEN_PREFIX='ask-manager-token:'

#: What a run id may be before it is passed to `channel serve` as an argv word and
#: resolved as the `runs/<run-id>/` directory, and what a node id may be before it is
#: put into a frame the engine resolves against that run's graph. It refuses only what
#: those uses cannot survive — `scripts/channel-serve.py` holds the same superset, and
#: `tests/test_planner_seam_contracts.py` reconciles the two.
#:
#: Every consumer takes it from a caller: an environment a dispatch was started with, or
#: an argument a manager typed. `scripts/channel-reply.sh` reads a file path built from
#: one, which is the use with no verb behind it to refuse a value this would not.
# shellcheck disable=SC2034  # read by the sourcing script, not by this file
ASK_MANAGER_SAFE_REFERENCE='^[A-Za-z0-9_][A-Za-z0-9_.-]*$'

#: The rule itself, as Python source both callers embed in a program of their own. A
#: function rather than a whole program, because the two callers ask it inside different
#: questions — one is classifying an answer that also has to be matched to a token, the
#: other is judging an envelope before it is sent — and a program would force one of them
#: to spawn a second interpreter to reach the same decision.
#:
#: What it decides is deliberately narrow: whether the envelope is the shape a ruling
#: has to be. `onepipeline channel serve` hands its caller back whatever answered, and
#: `scripts/ask-manager.sh` may act only on a JSON object carrying a boolean
#: `completion` — everything else is discarded there, so everything else is unusable
#: here. It says which field is missing or of which wrong type, because "invalid" sends
#: a manager back to guess at an envelope they have already written once.
# shellcheck disable=SC2034  # read by the sourcing script, not by this file
ASK_MANAGER_RULING_SOURCE='
import json


def ruling_refusal(raw):
    """Why this envelope cannot be a ruling, or None when it can be one."""
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError:
        return "it is not JSON at all, so it carries no completion field"
    if not isinstance(answer, dict):
        return (
            "its top level is a "
            + type(answer).__name__
            + " rather than a JSON object, so it carries no completion field"
        )
    if "completion" not in answer:
        return "it carries no completion field, and that is what makes an answer a ruling"
    if not isinstance(answer["completion"], bool):
        return (
            "its completion field is a "
            + type(answer["completion"]).__name__
            + " where a ruling requires a boolean"
        )
    return None
'

#: The other half of that rule, and the half a well-formed envelope can still fail: an
#: answer is *this* question's only when it echoes the correlation token the question
#: carries. Stated here for the same reason the ruling rule is — two processes decide it
#: minutes apart at opposite ends of the channel, and a second copy is what would let the
#: recipe wave through an envelope the wrapper then discards in silence.
#:
#: `pending_token` reads what a surface asks to be echoed; `answer_echoes` decides
#: whether an envelope carries it. Both are embedded beside `ASK_MANAGER_RULING_SOURCE`
#: rather than folded into it, because they answer a different question about a
#: different pair of inputs and a caller that wants one may not want the other.
# shellcheck disable=SC2034  # read by the sourcing script, not by this file
ASK_MANAGER_TOKEN_SOURCE='
import json


def pending_token(message, prefix):
    """The token a pending surface asks an answer to echo, or None when it carries none.

    A token is the declared prefix together with the value following it, through the end
    of that line — the whole of what a question asks for back, rather than the half that
    varies, so that both ends compare the same bytes. Whatever is on that line is the
    token: a surface carrying the prefix is a question this reader must guard, and
    holding the value to a shape as well would let a surface it did not recognize pass a
    reply through unjudged, which is the silence the guard exists to close.

    The first marked line is the only one asked, which is why scripts/ask-manager.sh
    states its protocol at the head of the surface: below the body, a question quoting an
    older token would decide what an answer has to echo, and the guard would demand the
    wrong one.
    """
    for line in message.splitlines():
        _, marked, rest = line.partition(prefix)
        if marked:
            return prefix + rest.strip()
    return None


def answer_echoes(raw, token):
    """Whether this envelope answers the question that minted the given token.

    Read from the one field that scripts/ask-manager.sh takes the text of an answer
    out of: an envelope echoing the token anywhere else is one that wrapper would
    discard, so echoing it anywhere else is not echoing it.
    """
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(answer, dict):
        return False
    message = answer.get("message")
    return isinstance(message, str) and token in message
'
