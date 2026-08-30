#!/usr/bin/env bash
# The self-heal for this checkout's own plan-store CLI, and the third of the three
# `scripts/nx.sh` performs beside `workspace-install.sh` and `python-install.sh`.
#
# Provisioning installs the standalone `onetaskgraph` release archive into
# `<root>/.venv/bin` rather than a directory the whole host shares, so the binary a
# checkout reads is the release *it* pinned. That destination is ignored state, which
# a freshly created worktree and a publication clone arrive without — and unlike the
# shared path it replaced, nothing else on the host puts one there. Session setup runs
# on a `SessionStart` hook and reaches neither of those, so without this the gate a
# publication runs in its own clone resolved no CLI at all and every recipe and test
# that reads the plan store failed with "No such file or directory".
#
# The install itself is not duplicated here: `scripts/session-setup.sh` defines it and
# returns when sourced, so this is the entry point and that file stays the one source
# of what an install does and which release it adopts. It exits immediately once the
# binary already reports this checkout's pin, which is one `--version` and no network.
set -euo pipefail

# Under errexit, a `cd` or `pwd` that fails ends this here rather than leaving the
# resolution below reading a path that is not this script's own.
script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

if [[ $# -gt 0 ]]; then
    echo "onetaskgraph-install: expected no arguments, got '$*'; rerun it with none — the install it performs is already idempotent, so there is no flag to reinstall or refresh with" >&2
    exit 2
fi

# uv's own "the environment is provided, do not touch it" signal, read exactly as
# `scripts/python-install.sh` reads it: this binary lives in that environment, so a
# caller that pointed uv at one it provisioned elsewhere is not installed over.
if [[ -n ${UV_NO_SYNC+set} ]]; then
    case ${UV_NO_SYNC,,} in
        1 | true | yes | on | y) exit 0 ;;
        0 | false | no | off | n) ;;
        *)
            echo "onetaskgraph-install: UV_NO_SYNC='$UV_NO_SYNC' is not a value uv reads; set it to 1/true/yes/on/y to keep the environment you provided, to 0/false/no/off/n to provision this checkout's own, or leave it unset" >&2
            exit 2
            ;;
    esac
fi

# A workspace declaring no adopted release has no plan-store CLI to provision.
# `tests/fixtures/nx-cache` is one: the cache contract is proven by copying this
# wrapper chain into a TypeScript-only tree and running it for real.
if [[ ! -f "$repo_root/config/onetaskgraph.version" ]]; then
    exit 0
fi

# Sourced rather than run: the guard at the foot of that file returns once its
# functions and its adopted versions are defined, without provisioning anything else.
# Validated first, as every other wrapper here validates a helper it loads: a `source`
# of something that is not a readable regular file fails as shell noise naming nothing.
setup="$script_dir/session-setup.sh"
if [ ! -f "$setup" ] || [ ! -r "$setup" ]; then
    echo "onetaskgraph-install: required helper is not a readable regular file: $setup; restore it from the repository or run 'just bootstrap', then retry" >&2
    exit 1
fi

# That sourced guard is also why the two lines below carry a suppression. shellcheck
# inlines a followed file, reads its `return 0` as ending this script, and calls
# everything after the `source` unreachable — which is true of the copy it inlined and
# false of the one bash runs, where `return` only ends the sourcing.
# shellcheck source=scripts/session-setup.sh
# shellcheck disable=SC2317  # reached: that `return` ends the sourcing, not this script.
if ! source "$setup"; then
    echo "onetaskgraph-install: $setup is readable but refused to load; it declines an adopted version file it cannot read or parse and names which one above, so correct that file or run 'just bootstrap', then retry" >&2
    exit 1
fi
# That file turns errexit off for itself — session setup continues past optional
# failures deliberately — and this wrapper does not, so take it back.
# shellcheck disable=SC2317  # reached: that `return` ends the sourcing, not this script.
set -e

# shellcheck disable=SC2317  # reached: session-setup.sh's `return` ends the sourcing, not this script.
if ! install_onetaskgraph; then
    echo "onetaskgraph-install: could not provision onetaskgraph $ADOPTED_ONETASKGRAPH_VERSION into $ONETASKGRAPH_BIN; a download, a checksum or an unpack names itself above, and the steps that report nothing are a staging directory, a checksum tool, the final version check, and the plan root beside that binary — so check that this checkout's .venv/bin and .plans are writable. 'just session-setup' performs the same install" >&2
    exit 1
fi
