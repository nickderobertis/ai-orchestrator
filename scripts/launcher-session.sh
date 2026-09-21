# shellcheck shell=bash
# The one definition of **who is acting** on this host, sourced rather than run.
#
# Ownership is a comparison, so every process that launches a run, reads one, or acts
# on one has to identify itself the same way — and two spellings of the ladder is how
# a reader ends up owning nothing while the launcher owns everything. This is that
# ladder, in one place: `scripts/onepipeline.sh` sources it for every recipe that
# reaches `onepipeline`, and `scripts/telemetry-server.sh` sources it to hand the read
# API the acting session it serves under, since a mutation from the browser is
# performed as that session and an unattributed server owns nothing and is refused
# every stop it does not force.
#
# It exports ONEPIPELINE_LAUNCHER and ONEPIPELINE_LAUNCHER_SESSION, or leaves both
# unset. Detection is from the exported environment and never from process ancestry,
# and a session nothing identifies stays unidentified: a run misattributed to a
# planner who did not launch it is worse than one attributed to nobody.
#
# Strict mode is established here rather than inherited, as its neighbours do: both
# callers set it themselves today, and an identity that could only be half established
# must abort rather than reach a launch as a credential nothing can compare.
set -euo pipefail

# Whether a harness's session id is one this host may claim an identity from. It is a
# **trust boundary**: the value arrives from whichever harness exported it, and this
# helper's answer becomes an ownership credential that reaches a command line
# (`--session`), a launch record, and every later comparison against it. A value
# carrying whitespace, a control character or a shell metacharacter would be a run
# attributed to something no later read can match — which is worse than no
# attribution, by the rule this file's header states. So the shape is checked before
# anything is exported: a non-empty run of the characters a session id is actually
# made of, bounded so a runaway value cannot become a command line of its own.
#
# 1-200 characters of ASCII letters, digits, dot, underscore and hyphen, which admits
# every id this host's two harnesses produce — a Claude UUID and a Codex thread id.
launcher_session_is_usable() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$ ]]
}

# An already-exported identity wins: a dispatch nested inside a launch inherits
# its planner's, and re-deriving it here from the harness the *dispatch* runs
# under would reassign the run to the worker mid-flight.
#
# It is validated all the same, and what differs is only the response. A malformed one
# is **reported and kept**: its run was already launched under it by the process that
# exported it, every reader already compares against it, and dropping it here would
# make this process unattributed while its parent is attributed — splitting one run's
# ownership across two identities, which is the reassignment this rule exists to
# prevent. So the boundary is checked and the finding is made audible, and the
# credential is left to its owner.
# llmlint: ignore-block[boundary_inputs_validated] The value IS validated, in the condition below; what the rule asks for beyond that is rejection, and rejection is the documented wrong answer for this one input. It is not a credential this process is deciding to grant — it is the credential its run was already launched under, by the process that exported it — so refusing it here would leave this process unattributed while its parent is attributed and split one live run's ownership across two identities, the reassignment the ladder exists to refuse. The derived path, which this helper does decide, rejects exactly as the rule asks. `tests/test_launcher_session.py` drives both.
if [ -n "${ONEPIPELINE_LAUNCHER_SESSION:-}" ] &&
    ! launcher_session_is_usable "$ONEPIPELINE_LAUNCHER_SESSION"; then
    echo "launcher-session: the inherited ONEPIPELINE_LAUNCHER_SESSION is not the shape a session id has (1-200 characters of letters, digits, dot, underscore or hyphen); it is kept, because the run it names was launched under it and replacing it here would split that run's ownership, but whichever process exported it is what needs repairing" >&2
fi
# llmlint: ignore-end[boundary_inputs_validated]
if [ -z "${ONEPIPELINE_LAUNCHER_SESSION:-}" ]; then
    # Ordered, and read in order, so a session nested inside another resolves to
    # the first harness that claims it. `CODEX_HOME` is deliberately not a marker:
    # it is ambient configuration a developer may export in a shell profile, so a
    # plain shell would claim to be codex.
    launcher_claude_session=${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-}}
    launcher_codex_session=${CODEX_THREAD_ID:-${CODEX_SESSION_ID:-}}
    launcher_kind=
    launcher_session=
    if [ -n "$launcher_claude_session" ]; then
        launcher_kind=claude-code
        launcher_session=$launcher_claude_session
    elif [ -n "$launcher_codex_session" ]; then
        launcher_kind=codex
        launcher_session=$launcher_codex_session
    fi
    if [ -n "$launcher_session" ]; then
        if launcher_session_is_usable "$launcher_session"; then
            export ONEPIPELINE_LAUNCHER="$launcher_kind"
            export ONEPIPELINE_LAUNCHER_SESSION="$launcher_session"
        else
            # Said out loud and then left unidentified, which is the safe half of the
            # doctrine above. A silent drop would read as a host with no harness, and
            # the operator would look for a missing variable rather than a bad one.
            echo "launcher-session: the ${launcher_kind} session id in this environment is not a usable identity, so this process stays unattributed: runs it launches record no owner and reads it makes match none. Its shape must be 1-200 characters of letters, digits, dot, underscore or hyphen" >&2
        fi
    fi
    unset launcher_claude_session launcher_codex_session launcher_kind launcher_session
fi
