#!/usr/bin/env bash
# Run the pinned Pi (built through buildNpmPackage in home.nix) under the
# corrected Node 24 that avoids the Darwin worker-thread file-descriptor regression
# in the primary nixpkgs Node.
set -eu

# Keep the corrected Node first on PATH so any child Node processes Pi spawns
# also get the corrected runtime, then exec the pinned store-path bundle
# directly. There is deliberately no fallback that searches PATH for a floating
# `pi` install: the pin is the only source.
exec env PATH="@UNSTABLE_NODE_BIN@:$PATH" "@UNSTABLE_NODE_BIN@/node" "@PI_CLI@" "$@"
