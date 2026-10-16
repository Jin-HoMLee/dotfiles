#!/usr/bin/env bash
# Static wiring checks for the pinned Pi package: exact 1.1.0 npm release,
# content-addressed tarball, vendored lockfile, and a fixed npmDepsHash so the
# pin cannot silently move.
set -euo pipefail

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CONFIG=$(cat "$ROOT/home.nix")

assert_contains "$CONFIG" 'pname = "pi-coding-agent";' \
  "the package names the npm package"
assert_contains "$CONFIG" 'version = "1.1.0";' \
  "Pi is pinned to the exact known-working release"
assert_contains "$CONFIG" 'registry.npmjs.org/@earendil-works/pi-coding-agent/-/pi-coding-agent-1.1.0.tgz' \
  "the package fetches the pinned npm tarball"
assert_contains "$CONFIG" 'hash = "sha512-SeEi/4hdcHNgA9UWlefZl7ZZpm3dzi2OoxNjDHsBJ9o298LNOtbL4DGKgitlEj6uCTccvtw6f2hlCkTPVJ2RXg==";' \
  "the tarball is content-addressed with its npm integrity hash"
assert_contains "$CONFIG" 'buildNpmPackage' \
  "Pi is built through buildNpmPackage (bundled CLI plus node_modules), not a fetchurl zip"
assert_contains "$CONFIG" 'pi-coding-agent-package-lock.json' \
  "the vendored lockfile pins the dependency tree"
assert_contains "$CONFIG" 'npmDepsHash = "sha256-qC89OCkQ2iJXnsxVAUiDZ10VSVEagpotmekn8iPSX3Y=";' \
  "the dependency set is pinned with a fixed npmDepsHash"
assert_contains "$CONFIG" 'dontNpmBuild = true;' \
  "the prebuilt dist/ is used as shipped"
assert_contains "$CONFIG" 'piLauncher # run the pinned `pi` (1.1.0) with corrected Node 24' \
  "the launcher is what lands on PATH"

assert_not_contains "$CONFIG" 'piLauncher # run the npm-installed' \
  "the floating npm-install launcher description is gone"

pass "Pi is pinned declaratively at 1.1.0 without an auto-update path"
