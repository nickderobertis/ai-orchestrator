# shellcheck shell=bash
# The ONE definition of which resolvers establish a dispatch's environment — the `.env`
# credentials, every Claude identity's config directory, the alternate Codex home —
# sourced by scripts/onepipeline.sh at driver start and by scripts/dispatch-env-hook.sh
# before every node-scope dispatch (ai-orchestrator#1109). One table serves both so the
# two lists cannot drift; `tests/test_dispatch_env_hook.py` holds both callers to it.
#
# Strict mode is established here rather than inherited, as the resolvers below do: a
# launch whose environment could only be half established must abort.
set -euo pipefail

# The resolvers, in the order they run, as `<helper>:<function>`. Each helper is this
# file's neighbour and each function takes the calling launcher's name as its one
# argument. The order is the order scripts/onepipeline.sh has always run them in, and it
# is load-bearing in one place: the credentials come first because the Claude identities
# file is parsed by the same `read_env_file` the credentials helper defines.
DISPATCH_ENVIRONMENT_RESOLVERS=(
    "credentials-env.sh:export_host_credentials"
    "claude-alt-config-dir.sh:resolve_claude_alt_config_dir"
    "codex-alt-home.sh:ensure_codex_alt_home"
)

# Every variable the resolvers established on the last call, in resolver order, so a
# caller that has to hand them on — the hook — reads which rather than guessing. Empty
# until `export_dispatch_environment` has run to the end.
dispatch_environment_names=()

# Run every resolver above, in order, each attributing its diagnostics to $1, the name
# of the calling launcher. A resolver's own refusal is returned as it stands; a helper
# that cannot be sourced is a broken checkout, which the shell itself says on the line
# it fails the source at.
export_dispatch_environment() {
    # Named the way scripts/ask-manager-env.sh names its own required input: a caller
    # that forgot the label would otherwise abort on `1: unbound variable`, which says
    # nothing about which helper was called wrong.
    local caller=${1:?export_dispatch_environment: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/onepipeline.sh passes onepipeline, then retry}
    local here resolver helper function
    dispatch_environment_names=()
    # From this file's own location, because the helpers being loaded are this
    # checkout's — never from `$PWD`, which a launcher may be invoked from anywhere.
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd); then
        echo "$caller: this checkout's dispatch environment could not be resolved; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    for resolver in "${DISPATCH_ENVIRONMENT_RESOLVERS[@]}"; do
        helper=${resolver%%:*}
        function=${resolver#*:}
        # shellcheck disable=SC1090  # the helper is named by the table above
        # llmlint: ignore[boundary_inputs_validated, robust_shell, tool_output_is_signal] A tracked sibling is a checkout invariant, not an input at a trust boundary: one that is missing or will not load is a broken checkout, and the shell says so on the line it fails the source at.
        . "$here/$helper"
        "$function" "$caller" || return $?
        # What each resolver established, read off the resolver's own record of it: the
        # credentials helper's parser leaves the names its file defines in
        # `env_file_names`, the identities helper declares its four indirections, and
        # the Codex helper exports exactly one.
        case $function in
            export_host_credentials)
                # shellcheck disable=SC2154  # left by read_env_file in credentials-env.sh
                dispatch_environment_names+=(${env_file_names[@]+"${env_file_names[@]}"})
                ;;
            resolve_claude_alt_config_dir)
                dispatch_environment_names+=("${CLAUDE_IDENTITY_CONFIG_VARIABLES[@]}")
                ;;
            ensure_codex_alt_home)
                dispatch_environment_names+=(ORCHESTRATOR_CODEX_ALT_HOME)
                ;;
        esac
    done
}

# The whitelist: what a driver's environment is *built from*, rather than what a
# launching shell happened to be carrying, so a planner session's `VIRTUAL_ENV` never
# reaches a worker's `uv pip install` (ai-orchestrator#1162).
#
# The engine's dispatch-env hook can only add or overlay variables on the environment
# the driver started with (onepipeline's `docs/contract.md`, **Dispatch-env hook**), so
# the removal happens where that environment is built: `scripts/onepipeline.sh`'s
# `start` and `adopt` arms call `construct_dispatch_environment` below, and a read-only
# view constructs nothing.
#
# The three tables are the ONE place the list is stated, and every entry carries the
# reason it is there, so a variable a dispatch turns out to need reads as a diagnosable
# omission. When in doubt a family is preferred to a name: a name too narrow breaks a
# tool inside a dispatch in a way nobody foresees.

#: Refused outright, whatever a family below would admit — this is the class
#: ai-orchestrator#1162 closes, stated in one place. A name here is dropped even when it
#: is one a resolver established or a kept prefix matches.
declare -A DISPATCH_ENVIRONMENT_REFUSED_NAMES=(
    [VIRTUAL_ENV]="the launching session's activated Python environment, which on this host is the canonical checkout's .venv; inherited, a worker's bare 'uv pip install' in its own worktree writes into the environment every concurrent node and the manager share"
    [VIRTUAL_ENV_PROMPT]="the same activation's label; it survives only to describe a VIRTUAL_ENV that is no longer there"
    [UV_PROJECT_ENVIRONMENT]="names the environment 'uv sync' and 'uv run' resolve for a project, so an inherited one points every uv in every worktree at the launching checkout's; the one name the UV_ family below does not admit"
    [PYTHONHOME]="repoints the interpreter's own prefix, so an inherited one makes a worktree's python assemble itself out of the launching checkout's standard library"
    [PYTHONPATH]="puts the launching checkout's importable tree in front of every dispatch's own, which is the same capture tests/test_dispatch_cwd_does_not_shadow.py holds the cwd to"
    [CONDA_PREFIX]="the active conda environment, which 'conda install' and a bare 'pip install' write into for the same reason VIRTUAL_ENV does"
    [CONDA_DEFAULT_ENV]="its name, which conda reactivates the prefix above from"
    [PYENV_VERSION]="pins which interpreter a pyenv shim resolves, so an inherited one overrides a worktree's own .python-version"
    [POETRY_ACTIVE]="tells poetry it is already inside its project's environment, so a dispatch installs into the launching session's instead of its own"
)

#: Kept by exact name: the process's own identity and terminal, the few platform names
#: with no family, and this repository's own stand-in seams.
declare -A DISPATCH_ENVIRONMENT_KEPT_NAMES=(
    [HOME]="every tool resolves its configuration, credentials and caches under it, and the Claude and Codex identity helpers derive their directories from it"
    [PATH]="which binaries a dispatch can run at all; rewritten below rather than merely kept"
    [USER]="the name git, gh and ssh stamp and authenticate under"
    [LOGNAME]="the same name, which the tools that do not read USER read instead"
    [SHELL]="the interpreter 'just' and every agent harness spawn a recipe in"
    [TERM]="whether a tool renders control sequences at all; several refuse a terminal they cannot name"
    [TMPDIR]="where every tool writes its scratch, and this host does not put it at /tmp everywhere"
    [TZ]="the zone a worker's timestamps are written and read in"
    [LANG]="the locale a tool decodes its own output in; absent, several fall back to ASCII and mangle a repository's prose"
    [PWD]="bash's own record of where this process is; unsetting it makes the launching shell lie about its directory to every child"
    [OLDPWD]="bash's companion to PWD, for the same reason"
    [SHLVL]="bash's own nesting depth, which a shell increments rather than invents"
    [BASH_ENV]="the file a non-interactive bash sources at startup, which is how this host initialises a dispatch's shell"
    [DISPLAY]="the X display a browser-driven check attaches to where a repository has one"
    [NO_COLOR]="an operator's declared preference that no tool emit colour, which every tool here honours"
    [FORCE_COLOR]="the same preference stated the other way, which the node toolchain reads"
    [COLORTERM]="what colour depth the terminal above supports"
    [CI]="many tools branch on it, and a host that declares itself CI means it for its dispatches too"
    [EDITOR]="the editor a verb that opens one would use; a dispatch never does, but a tool that cannot resolve one sometimes refuses rather than skipping"
    [VISUAL]="the same, for the tools that read this name instead"
    [PAGER]="what a verb pipes a long report through; an unset one makes some tools spawn 'less' against a pipe"
    [GPG_TTY]="what gpg signs a commit against, where a repository requires signed commits"
    [GNUPGHOME]="where those keys live, for a host that does not keep them under HOME"
    [SSL_CERT_FILE]="the TLS trust store every request out of a dispatch is verified against"
    [SSL_CERT_DIR]="the same trust store in its directory form, which is how several TLS stacks are pointed at a host's own certificate authority"
    [REQUESTS_CA_BUNDLE]="the same trust, under the name the Python HTTP stack reads"
    [CURL_CA_BUNDLE]="the same trust, under the name curl and its bindings read"
    [HTTP_PROXY]="how this host reaches the network at all where it is behind one"
    [HTTPS_PROXY]="the same, for TLS, which is every request a dispatch makes to a forge or a package registry"
    [ALL_PROXY]="the same, for the tools that read only this name"
    [NO_PROXY]="which hosts bypass the three above; inherited without it a dispatch proxies its own loopback"
    [http_proxy]="the lowercase spelling, which curl and several node tools read in preference to the uppercase one"
    [https_proxy]="the lowercase spelling of HTTPS_PROXY, for the same reason"
    [all_proxy]="the lowercase spelling of ALL_PROXY, for the same reason"
    [no_proxy]="the lowercase spelling of NO_PROXY, for the same reason"
    [UV]="the path of the uv that launched this process, which uv sets so a nested invocation reaches the same binary rather than whichever one a PATH offers"
    [IS_SANDBOX]="what Claude Code reads to know it is already inside one, which the oneharness configs set per identity and a dispatch inherits where they do not"
    [AI_AGENT]="the marker a tool branches on to know a non-human is driving it"
    [REAL_ONEHARNESS_BIN]="the real binary this repository's own stand-in model delegates to at the oneharness seam; a launch journey that could not hand it on would drive no real oneharness at all"
    [TRACE_FILE]="where this repository's own stand-in binaries record the command lines they were given; the recipe-delegation journeys drive the real wrappers and read that file, and a launch that could not hand it on would trace nothing at all"
    [IDENTITY_RECORDINGS]="where this repository's Claude-identity journey has the stand-ins at every entry point's hand-off record the identity environment they were given; a launch that could not hand it on would record nothing, and the journey could not tell a dropped indirection from a hand-off never reached"
    [REAL_PLAN_STORE]="the real plan-store CLI this repository's older-plan-store stand-in delegates to from inside a dispatched turn; the follow-up recipe journeys put that stand-in ahead of the checkout's, and without this name it refuses every command the turn runs"
    [OBSERVER_ENVIRONMENT_PATH]="where this repository's observer journeys have the dag-scope graph record the environment it was given; the observer graph never runs the dispatch-env hook, so the driver's environment is the only way it arrives"
)

#: Kept by family. Each is a namespace whose whole contents a dispatch legitimately
#: reads, listed as a prefix because a name added to one of them later is a name a
#: dispatch will need for exactly the reason the family is here.
declare -A DISPATCH_ENVIRONMENT_KEPT_PREFIXES=(
    [LC_]="every locale category, which decide how a tool decodes and sorts a repository's own content"
    [XDG_]="the platform directory roots this host points at its own caches, state and runtime directories, XDG_RUNTIME_DIR among them"
    [ORCHESTRATOR_]="this host's own seam names: the four Claude identity indirections, the alternate Codex home, the ask shim, the follow-up drafting command and the comparison identity a gate replays"
    [AI_ORCHESTRATOR_]="this host's own roots, which scripts/sweep.sh and its siblings read"
    [ONEPIPELINE_]="the engine's own names, the launcher identity a nested launch inherits and the per-dispatch scratch directory among them"
    [ONEVCS_]="the lifecycle's own names: the state root, the session a dispatch publishes through, the comparison identity and the lock bound ai-orchestrator#1164 derives"
    [ONEAGENTGRAPH_]="the agent-graph runner's own names, including where it keeps a run's scratch"
    [ONEJUDGE_]="the two-party conversation's own names, including where it writes a turn's artifacts"
    [ONEHARNESS_]="harness and identity routing: which chain, which model, which binary stands in for a provider"
    [ONEMESSAGEBUS_]="the channel's own names, including the transport directory a run's rendezvous is bound to"
    [ONETASKGRAPH_]="the plan store's own configuration layer, which is how a launch points a source at a root and how the board is nominated"
    [LLMLINT_]="the judged lint tier's own names, which a worker iterating with 'just lint-llm-diff' reads"
    [CLAUDE]="every Claude name, CLAUDECODE and the CLAUDE_CODE_ session identity among them; the oneharness configs mask the ones an identity must not see, and a chain's bare candidate resolves no mask, so the family travels and the config decides"
    [CODEX_]="the same for codex, CODEX_HOME included: ambient configuration a developer may export, which the first codex variant deliberately honours"
    [ANTHROPIC_]="the provider's own credential and endpoint names, which the oneharness configs mask per identity rather than here"
    [OPENAI_]="the same credential and endpoint names for the other provider, masked per identity by the oneharness configs rather than here"
    [GH_]="the forge CLI's own names, the board credential among them; which roles keep it is the oneharness configs' to decide, not this table's"
    [GITHUB_]="the forge's own names, which a worker's repository reads when it is running under one"
    [GIT_]="git's own configuration and the editor, author and ssh command a commit is made under"
    [SSH_]="the agent socket and askpass a push or a clone authenticates through"
    [CARGO_]="the rust toolchain's own names, including where a crate's build artifacts and registry live"
    [RUSTUP_]="which rust toolchain a dispatch resolves"
    [RUST_]="rust's own runtime names, RUST_LOG and RUST_BACKTRACE among them, which is how a refusal from a linked engine is made legible"
    [NX_]="the Nx workspace's own names, which scripts/nx.sh sets and a dispatch's own recipes read"
    [BUN_]="the bun toolchain's own names, including its install cache"
    [NODE_]="node's own names, NODE_OPTIONS and NODE_EXTRA_CA_CERTS among them"
    [NPM_]="npm's own uppercase names, which decide which registry a dispatch installs from and how it authenticates to it"
    [npm_]="npm's own lowercase configuration names, which is how every npm_config_ setting travels"
    [COREPACK_]="corepack's own names, which decide whether it pins a package manager under a dispatch"
    [UV_]="uv's own names — the index, the keyring provider, the recursion guard and UV_NO_SYNC — except UV_PROJECT_ENVIRONMENT, which the refusals above name"
    [PIP_]="pip's own configuration: which index a host mirrors from, and how it is trusted. These name an index, never an environment"
    [PYTHON]="python's own behaviour flags, PYTHONUNBUFFERED and PYTHONDONTWRITEBYTECODE among them, except PYTHONHOME and PYTHONPATH, which the refusals above name"
    [PYTEST_]="pytest's own names, which a repository's own test tier reads"
    [ASDF_]="the version manager's own names, without which its shims on PATH resolve nothing"
    [DOCKER_]="which daemon a containerised check reaches, and how it authenticates to it"
    [PLAYWRIGHT_]="where the browsers a visual check drives are installed"
    [AWS_]="the cloud identity a worker's own repository deploys or reads under"
    [JUST_]="just's own names, including where it writes a shebang recipe's body"
    [WSL]="the interop names this host's kernel exports, which decide whether a dispatch can reach the Windows side at all"
    [FAKE_]="this repository's own stand-ins — the model at the oneharness seam, the provider below it, the forge and the engine — which a launch journey has to be able to hand a dispatch or it drives nothing real"
    [MOCK_]="the oneharness mock-harness protocol the same stand-ins answer under"
    [STUB_]="the plan-store stand-in this repository's own journeys put in front of a board"
    [OLDER_PLAN_STORE_]="the older-plan-store stand-in the follow-up recipe journeys put first on a dispatched agent's own search path — its directory, the release it claims and where it logs what it served — read inside the turn, because that is where the agent resolves the store command its task names"
    [HELD_]="the plan-store stand-in that holds a call open, which the writeback-budget journeys measure a copy with"
)

#: Put at the front of a dispatch's PATH, deliberately **relative**. The engine hands a
#: dispatch-env hook the run id and the node id and no worktree path, and a launch does
#: not know where a node will be placed at all, so an absolute answer does not exist at
#: the moment this is built. A relative entry is the honest one: a dispatch's working
#: directory *is* its worktree, so this resolves to that worktree's own environment where
#: the repository has one — which `scripts/nx.sh`'s healing of `.venv` from the locked
#: installs is what keeps true — and matches nothing where it does not. It is what
#: replaces the inherited VIRTUAL_ENV above: a worker's python, and the tools its own
#: repository pins, come from the checkout it is working in rather than from whichever
#: one the launching shell had activated.
#:
#: Its one known cost, so that nobody has to rediscover it: a *bare* `python3` resolved
#: through a relative entry is executed under that relative path, and CPython then reports
#: a relative `sys.prefix`, which `site` reports as two `RuntimeWarning`s on stderr naming
#: the venv. Nothing else is affected — every console script in a `.venv/bin` carries an
#: absolute shebang — no exit status changes, and the alternative is a dispatch with no
#: worktree environment on its PATH at all, which is the defect this closes.
DISPATCH_ENVIRONMENT_WORKTREE_BIN=".venv/bin"

#: And behind it, this checkout's own — the absolute entry `uv run` used to contribute
#: by activating the project environment. It is what puts `oneharness` on a dispatch's
#: PATH at all: onejudge spawns whichever one it finds there, and a worktree of another
#: repository has none of its own. Behind the relative entry, so a repository that does
#: pin its own tools is answered by its own first. Resolved from this file's location
#: rather than from `$PWD`, because the environment being built is this checkout's.
DISPATCH_ENVIRONMENT_CHECKOUT_BIN=".venv/bin"

# Whether $1 is a name a dispatch keeps. Reads the three tables above and the record of
# what the resolvers established, in that order of authority.
dispatch_environment_keeps() {
    local name=$1 prefix
    [ -n "${DISPATCH_ENVIRONMENT_REFUSED_NAMES[$name]+set}" ] && return 1
    [ -n "${DISPATCH_ENVIRONMENT_KEPT_NAMES[$name]+set}" ] && return 0
    for prefix in "${!DISPATCH_ENVIRONMENT_KEPT_PREFIXES[@]}"; do
        case $name in
            "$prefix"*) return 0 ;;
        esac
    done
    # What the resolvers established this launch — the checkout's own `.env` names among
    # them, which are an operator's to write and so cannot be listed above.
    case $dispatch_environment_resolved in
        *" $name "*) return 0 ;;
    esac
    return 1
}

# Build this launch's environment from the tables above, in place, and put the worktree
# environment at the front of PATH. Called by scripts/onepipeline.sh's launch arms after
# `export_dispatch_environment`, so that what the resolvers established is kept and
# everything the launching shell merely happened to carry is not.
#
# $1 is the name of the calling launcher, for the diagnostics below.
construct_dispatch_environment() {
    local caller=${1:?construct_dispatch_environment: the name of the calling launcher is required, so its diagnostics stay attributable; pass it as the first argument, the way scripts/onepipeline.sh passes onepipeline, then retry}
    local name
    dispatch_environment_resolved=" ${dispatch_environment_names[*]-} "
    # The list is taken whole before anything is dropped: `compgen -e` reports the
    # exported names as they are now, and unsetting while reading it would skip.
    local -a exported=()
    mapfile -t exported < <(compgen -e) || exported=()
    for name in "${exported[@]}"; do
        dispatch_environment_keeps "$name" && continue
        # A name bash holds readonly cannot be dropped, and a launch that quietly passed
        # one on would be the leak this exists to close, reported as nothing at all.
        if ! unset -v "$name" 2>/dev/null; then
            echo "$caller: $name is on no dispatch-environment table and could not be dropped, so a dispatch would inherit it; unset it before the launch, or add it to a table in scripts/dispatch-env.sh with the reason it belongs there, then retry" >&2
            return 2
        fi
    done
    local here
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when this helper's own directory stops being enterable between a caller sourcing it and calling this; no journey can produce that without racing the filesystem the test itself runs on.
    if ! here=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd); then
        echo "$caller: this checkout could not be resolved, so a dispatch's PATH cannot be built; run the launch from a readable checkout, then retry" >&2
        return 2
    fi
    export PATH="$DISPATCH_ENVIRONMENT_WORKTREE_BIN:$here/$DISPATCH_ENVIRONMENT_CHECKOUT_BIN${PATH:+:$PATH}"
}
