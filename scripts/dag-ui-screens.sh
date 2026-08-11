#!/usr/bin/env bash
# Photograph the published DAG Observatory at every viewport in the matrix, into a
# gallery of this invocation's own.
#
# The operator iterates on this UI visually and cannot otherwise see it while a
# change is being made: a polish problem at one width stays invisible until
# somebody starts the app by hand at that width. So this starts the published read
# API and the published bundle on ports of this run's own, drives a real browser
# over them, and prints where the images landed.
#
# What it photographs is bounded by what the published packages ship. `onepipeline-ui`
# publishes the built bundle alone — no fixture server, and no screenshot surface
# with the per-surface waits its own repository's tier uses — so the surfaces here
# are the ones a URL names against a real runs root: the run list, and the three
# views of one run when the runs root has one. A run root with no runs in it is
# photographed as the empty run list, and this says so rather than reporting a
# fuller gallery than it captured.
set -euo pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

runs_root="${ONEPIPELINE_RUNS_DIR:-$repo_root/runs}"
run_id=""
args=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        --runs-root)
            [ "$#" -ge 2 ] || { echo "dag-ui-screens: --runs-root needs a directory" >&2; exit 2; }
            runs_root="$2"
            shift 2
            ;;
        --run)
            [ "$#" -ge 2 ] || { echo "dag-ui-screens: --run needs a run id" >&2; exit 2; }
            run_id="$2"
            shift 2
            ;;
        *)
            args+=("$1")
            shift
            ;;
    esac
done

# Playwright and the published bundle both arrive through the locked install, and a
# freshly created worktree has neither. Same self-heal as `scripts/nx.sh`.
"$script_dir/workspace-install.sh" || exit 1

gallery_root="$repo_root/.screenshots"
mkdir -p "$gallery_root" || {
    echo "dag-ui-screens: cannot create the gallery root '$gallery_root'; repair its parent permissions and retry" >&2
    exit 1
}
gallery="$(mktemp -d "$gallery_root/gallery-XXXXXXXX")" || {
    echo "dag-ui-screens: cannot create a gallery directory beneath '$gallery_root'; free some space and retry" >&2
    exit 1
}

free_port() {
    python3 -c 'import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()'
}

api_port="$(free_port)"
ui_port="$(free_port)"
api_pid=""
ui_pid=""
cleanup() {
    [ -n "$ui_pid" ] && kill "$ui_pid" 2>/dev/null || true
    [ -n "$api_pid" ] && kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT

uv run onepipeline-api serve --runs-root "$runs_root" --bind "127.0.0.1:$api_port" >"$gallery/api.log" 2>&1 &
api_pid=$!
DAG_UI_API_URL="http://127.0.0.1:$api_port" \
    DAG_UI_PORT="$ui_port" \
    bun "$script_dir/dag-ui-server.js" >"$gallery/ui.log" 2>&1 &
ui_pid=$!

base="http://127.0.0.1:$ui_port"
ready=""
for _ in $(seq 1 100); do
    if curl -sf "$base/healthz" >/dev/null 2>&1 && curl -sf "$base/" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.2
done
[ -n "$ready" ] || {
    cat "$gallery/api.log" "$gallery/ui.log" >&2
    echo "dag-ui-screens: the read API or the bundle server did not come up; their output is above and at $gallery" >&2
    exit 1
}

if [ -z "$run_id" ]; then
    # A request that failed and a store with no runs in it are different answers, and
    # only one of them is this recipe's to report: swallowing the first would
    # photograph an empty run list and call it an empty runs root.
    listed="$(curl -sf "$base/api/v2/runs")" || {
        echo "dag-ui-screens: the read API did not answer $base/api/v2/runs; its output is in $gallery" >&2
        exit 1
    }
    run_id="$(printf '%s' "$listed" | python3 -c 'import json,sys
runs = json.load(sys.stdin).get("runs", [])
print(runs[0]["run_id"] if runs else "")')" || {
        echo "dag-ui-screens: the read API answered $base/api/v2/runs with something that is not a run list; its output is in $gallery" >&2
        exit 1
    }
fi

surfaces=("01-run-list:/")
if [ -n "$run_id" ]; then
    encoded="$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$run_id")"
    surfaces+=(
        "02-overall:/?run=$encoded&view=overall"
        "03-graph:/?run=$encoded&view=graph"
        "04-timeline:/?run=$encoded&view=timeline"
    )
else
    echo "dag-ui-screens: '$runs_root' holds no runs, so only the empty run list is photographed" >&2
fi

#: The screen sizes this view is read at: the desktop sizes down to the smallest
#: laptop still in use, plus one phone — the only width where the shell's two
#: columns stop fitting, and therefore where every reflow defect shows up first.
viewports=(1920x1080 1440x900 1280x800 1024x768 390x844)

captured=()
for surface in "${surfaces[@]}"; do
    name="${surface%%:*}"
    path="${surface#*:}"
    for viewport in "${viewports[@]}"; do
        image="$name-at-$viewport.png"
        status=0
        # llmlint: ignore[tool_output_is_signal] Playwright's per-capture progress is what tells the operator which surfaces have been photographed while the tier runs.
        bunx playwright screenshot \
            --viewport-size="${viewport/x/,}" \
            --wait-for-timeout=2500 \
            "${args[@]}" \
            "$base$path" "$gallery/$image" || status=$?
        if [ "$status" -ne 0 ]; then
            echo "dag-ui-screens: playwright exited $status capturing $name at $viewport; what it reported is above (a run that reached no surface at all is usually the read API or the bundle server failing to start, whose output is in $gallery). Fix what it names and rerun 'just dag-ui-screens'; whatever it managed is at $gallery" >&2
            exit 1
        fi
        captured+=("$image")
    done
done

# The caption names a directory and a run id that arrived from a flag, an
# environment variable, or the read API's own JSON, so they are escaped rather than
# interpolated: an unescaped `<` in either would corrupt the one artifact this
# script exists to produce, silently and at the top of the page.
caption="$(python3 -c 'import html,sys; print(html.escape(sys.argv[1]))' \
    "runs root: $runs_root${run_id:+ · run: $run_id}")"
{
    echo "<!doctype html><meta charset=utf-8><title>DAG Observatory gallery</title>"
    echo "<h1>DAG Observatory — published bundle</h1>"
    echo "<p>$caption</p>"
    for image in "${captured[@]}"; do
        echo "<figure><figcaption>$image</figcaption><img src=\"$image\" width=\"900\"></figure>"
    done
} >"$gallery/index.html"

echo "dag-ui-screens: gallery at $gallery/index.html (${#captured[@]} images)"
