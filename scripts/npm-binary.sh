# shellcheck shell=bash
# The ONE place a script here resolves the engine or the read API `bun.lock` installs from
# npmjs.com: the prebuilt binary inside the platform package npm installed for this host,
# `node_modules/<package>-<platform>/bin/<binary>`, which `orchestrator/npm_installs.py`
# resolves the same way for every module.
#
# A workaround, retired by https://github.com/nickderobertis/onepipeline/issues/1238 and
# https://github.com/nickderobertis/onepipeline-ui/issues/527. The engine's npm launcher
# `node_modules/.bin/onepipeline` is a node script that runs the binary as a child process
# rather than replacing itself, so a caller that `exec`s it leaves a process between itself
# and the engine. A launcher re-executed under `onepipeline flow run` then reads the binary
# as its `$PPID`, never the shell that entered the flow, so `scripts/launch-flow.sh` reads
# itself as outside its flow and enters another, without end; and a PID a caller records is
# node's, which a signal reaches while the engine goes on. The read API's launcher did the
# same until 0.58.1, whose `node_modules/.bin/onepipeline-api` execs the binary; its half of
# this resolver retires once the callers here adopt that launcher. Every caller here execs
# the binary itself, as it did the PyPI wheel's, until then.
#
# Strict mode is established here rather than inherited, as the other resolvers do.
set -euo pipefail

# Print the binary `$2` that npm package `$1`'s platform package carries for this host,
# under this checkout's `node_modules`, resolved from this file's own location. A host no
# platform package is published for is refused by name, naming the four that are; whether
# the binary is installed is the caller's to check, naming its own remedy.
npm_binary() {
    local package=${1:?npm_binary: the npm package is required}
    local binary=${2:?npm_binary: the binary is required}
    local checkout platform
    # Both become path components, so each is held to npm's own name alphabet: no
    # separator or traversal can lead the path out of the platform package.
    local name='^[a-z0-9][a-z0-9._-]*$'
    if ! [[ "$package" =~ $name && "$binary" =~ $name ]]; then
        echo "npm-binary: '$package' and '$binary' must each be an npm package or binary name (lower-case letters, digits, '.', '_' and '-'); name the package and binary bun.lock installs" >&2
        return 2
    fi
    if ! checkout=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd); then
        echo "npm-binary: the checkout holding ${BASH_SOURCE[0]} could not be entered; run this from a readable checkout" >&2
        return 2
    fi
    # Node's `<process.platform>-<process.arch>` for each host `uname` names: the suffix of
    # the platform package npm installs there, as `orchestrator/npm_installs.py` maps it.
    case "$(uname -s)-$(uname -m)" in
        Linux-x86_64) platform=linux-x64 ;;
        Linux-aarch64 | Linux-arm64) platform=linux-arm64 ;;
        Darwin-arm64) platform=darwin-arm64 ;;
        *)
            echo "$package publishes no platform package for $(uname -s) $(uname -m); it publishes darwin-arm64, linux-arm64, linux-x64 and win32-x64, so run this on one of those hosts" >&2
            return 2
            ;;
    esac
    printf '%s\n' "$checkout/node_modules/$package-$platform/bin/$binary"
}

# Run as a command rather than sourced — `bash scripts/npm-binary.sh <package> <binary>
# <arguments>…`, which is how the `Stop` hook in `.claude/settings.json` reaches the engine
# on every host from one registration — it replaces itself with that binary, so nothing of
# this repository's stays between the caller and the verb.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    if [ "$#" -lt 2 ]; then
        echo "npm-binary: usage: npm-binary.sh <package> <binary> [argument…]" >&2
        exit 2
    fi
    resolved=$(npm_binary "$1" "$2") || exit 2
    if [ ! -x "$resolved" ]; then
        echo "npm-binary: this checkout has no executable $2 at $resolved; run 'just bootstrap' to install the pinned releases from bun.lock, then retry" >&2
        exit 2
    fi
    shift 2
    # `execfail` brings a binary the kernel refuses — a missing loader, another
    # architecture — back here to be explained rather than ending on the shell's own error.
    # errexit is off for the one line, because bash exits on a failed exec under `set -e`
    # even with `execfail`.
    shopt -s execfail
    set +e
    exec "$resolved" "$@"
    status=$?
    echo "npm-binary: $resolved is executable but could not be run (status $status); reinstall it with 'just bootstrap', then retry" >&2
    exit 126
fi
