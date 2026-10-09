#!/usr/bin/env bash
# Render one Mermaid source to a PNG a design document references: `just render-diagram
# <source.mmd> <output.png>`.
#
# A thin wrapper over the locked mermaid-cli (`mmdc`) and nothing more: it names the
# browser, the page width and the committed files below, and it writes one PNG. It never
# attaches the image to anything — a document is given it by the plan store's own
# `--asset` — and it never generates a diagram's source.
#
# **Everything a render depends on is a file in this workspace**, so two provisioned hosts
# render one source to the same bytes and a check over a render can be cached on those
# files: the source; `config/mermaid/config.json` and `config/mermaid/render.css`;
# `config/mermaid/fonts.conf`; `bun.lock`, which locks mermaid-cli, the Playwright whose
# `chromium.executablePath()` names the browser (honouring `PLAYWRIGHT_BROWSERS_PATH`) and
# so the Chromium revision, and `@expo-google-fonts/noto-sans`, the font every label is
# drawn in; and this script.
#
# **The font reaches Chromium through fontconfig, not through CSS.** mermaid-cli measures a
# label's text before it applies `--cssFile`, so a web font declared there would be
# measured in a fallback and painted in another. `FONTCONFIG_FILE` names the committed
# `fonts.conf`, whose only font directory is the locked package's, so the locked Noto Sans
# is the one font the browser can see — measured and painted alike — and no host font is
# ever drawn. The package is one shipping TTF files because Chromium draws nothing from a
# WOFF or WOFF2 file fontconfig offers it, leaving every label blank.
#
# **The page is 880 px wide**, the width of a board issue's column, so a PNG is laid out
# at the size a reader sees it. mermaid-cli keeps the page's 8-px body margins, so a graph
# wider than the page is written 864 px wide: a label-size budget divides by that width.
#
# mermaid-cli depends on Puppeteer, whose install would otherwise download a browser of
# its own; `.puppeteerrc.json` at the root turns that off, because the render only ever
# uses the Chromium the locked Playwright names.
#
# Chromium is launched with no extra argument, so its own sandbox stays on.
#
# Refusals, each writing no output file and leaving one already at the output path as it
# was: a wrong argument count, an unreadable source, or an output path not ending in `.png`,
# naming a directory or inside no writable one (exit 2); an absent mermaid-cli or browser, naming its path and the command that
# provisions it (exit 3); and a source mermaid-cli cannot render, with mermaid's own
# diagnostic, or a host that cannot stage or place the PNG (exit 1). A render is staged in
# the output's own directory and renamed over the output only once it has succeeded.
set -euo pipefail

# llmlint: ignore-block[changed_behavior_has_e2e] This guard answers a host that cannot enter
# the checkout holding this script, which no journey can bring about without breaking the
# filesystem the suite itself runs on; every refusal a caller can cause is driven in
# tests/render_diagram/test_render_diagram_e2e.py.
if ! script_dir="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"; then
    echo "render-diagram: the directory holding this script could not be entered; run it from a readable checkout, then retry" >&2
    exit 1
fi
repo_root="$(dirname -- "$script_dir")"
# llmlint: ignore-end[changed_behavior_has_e2e]

#: The width of the page mermaid-cli lays a diagram out in, in CSS pixels.
PAGE_WIDTH=880

if [ "$#" -ne 2 ]; then
    echo "render-diagram: expected <source.mmd> <output.png>, got $# argument(s); run 'just render-diagram <source.mmd> <output.png>'" >&2
    exit 2
fi
source_file="$1"
output="$2"
case "$output" in
    *.png) ;;
    *)
        echo "render-diagram: '$output' does not end in .png; name a .png output path, since a design document references its diagrams as PNG images" >&2
        exit 2
        ;;
esac
if [ ! -f "$source_file" ] || [ ! -r "$source_file" ]; then
    echo "render-diagram: the Mermaid source '$source_file' is not a readable file; name an existing .mmd file this user can read, then retry" >&2
    exit 2
fi
if [ -d "$output" ]; then
    echo "render-diagram: '$output' is a directory; name the PNG file to write, then retry" >&2
    exit 2
fi
destination="$(dirname -- "$output")"
if [ ! -d "$destination" ] || [ ! -w "$destination" ]; then
    echo "render-diagram: the output's directory '$destination' is not a writable directory; create it or name an output in one this user can write, then retry" >&2
    exit 2
fi

mmdc="$repo_root/node_modules/.bin/mmdc"
if [ ! -x "$mmdc" ]; then
    echo "render-diagram: the locked mermaid-cli is not installed at $mmdc; install the locked workspace with 'just bootstrap', then retry" >&2
    exit 3
fi
if ! browser="$(cd "$repo_root" && node -e 'process.stdout.write(require("playwright").chromium.executablePath())')"; then
    echo "render-diagram: the locked Playwright in $repo_root/node_modules could not name its Chromium, for the reason above; install the locked workspace with 'just bootstrap', then retry" >&2
    exit 3
fi
if [ ! -x "$browser" ]; then
    echo "render-diagram: no Chromium at $browser, the executable the locked Playwright names; provision it with 'just bootstrap', which runs the locked Playwright's 'install chromium', then retry" >&2
    exit 3
fi

# llmlint: ignore-block[changed_behavior_has_e2e] As the block above: a temporary directory
# that cannot be created, written or removed is a host failure no journey can cause safely.
if ! work="$(mktemp -d)"; then
    echo "render-diagram: no temporary directory could be created; free space in ${TMPDIR:-/tmp} or point TMPDIR at a writable directory, then retry" >&2
    exit 1
fi
# Cleanup is best-effort: a directory left behind is mktemp's own, under the temporary root.
trap 'rm -rf -- "$work" || echo "render-diagram: could not remove $work; delete it by hand" >&2' EXIT
if ! node -e 'process.stdout.write(JSON.stringify({executablePath: process.argv[1]}))' \
    "$browser" >"$work/puppeteer.json"; then
    echo "render-diagram: the browser configuration could not be written to $work; free space in ${TMPDIR:-/tmp}, then retry" >&2
    exit 1
fi
# llmlint: ignore-end[changed_behavior_has_e2e]
if ! FONTCONFIG_FILE="$repo_root/config/mermaid/fonts.conf" "$mmdc" --quiet \
        --input "$source_file" --output "$work/diagram.png" \
        --width "$PAGE_WIDTH" --backgroundColor white \
        --configFile "$repo_root/config/mermaid/config.json" \
        --cssFile "$repo_root/config/mermaid/render.css" \
        --puppeteerConfigFile "$work/puppeteer.json"; then
    echo "render-diagram: mermaid-cli could not render '$source_file', so no PNG was written; where its diagnostic above points into the source, correct the source, and where it names the browser or a config/mermaid/ file, re-provision with 'just bootstrap' or restore that file from this checkout's history; then retry" >&2
    exit 1
fi
# Staged beside the output, so the rename that places it is one step on one filesystem.
# llmlint: ignore-block[changed_behavior_has_e2e] As the blocks above: staging or renaming a
# file in a directory already checked writable fails only on a host failure, which no journey
# can cause safely; a successful replacement is driven by the journeys.
if ! staged="$(mktemp "$destination/.render-diagram.XXXXXX.png")" ||
    ! cp -- "$work/diagram.png" "$staged" ||
    ! mv -f -- "$staged" "$output"; then
    if [ -n "${staged:-}" ] && ! rm -f -- "$staged"; then
        echo "render-diagram: the staged file $staged could not be removed; delete it by hand" >&2
    fi
    echo "render-diagram: the rendered PNG could not be placed at '$output'; free space in '$destination' or name another output, then retry" >&2
    exit 1
fi
# llmlint: ignore-end[changed_behavior_has_e2e]
