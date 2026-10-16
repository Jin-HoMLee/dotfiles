#!/usr/bin/env bash
# Static wiring checks for the pinned OpenCode package: exact 1.x version,
# auto-update gate, PATH visibility, and non-ownership of the user's config.
set -euo pipefail

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CONFIG=$(cat "$ROOT/home.nix")

assert_contains "$CONFIG" 'version = "1.18.32";' \
  "OpenCode is pinned to the exact adapter-verified release"
assert_contains "$CONFIG" 'releases/download/v1.18.32/opencode-darwin-arm64.zip' \
  "the package fetches the official v1.18.32 Darwin arm64 binary"
assert_contains "$CONFIG" '--set OPENCODE_DISABLE_AUTOUPDATE true' \
  "the wrapper disables OpenCode's automatic upgrades"
assert_contains "$CONFIG" 'opencode   # pinned OpenCode 1.18.32 for Firstmate' \
  "OpenCode is declared in the Home Manager package list"
assert_contains "$CONFIG" 'home.sessionPath = [ "${config.home.profileDirectory}/bin" ];' \
  "Home Manager prepends its profile so opencode lands on PATH"

assert_not_contains "$CONFIG" 'home.file.".config/opencode/opencode.json"' \
  "Home Manager does not manage the user's opencode.json (Marginalia MCP preserved)"

pass "OpenCode is pinned declaratively without owning the user's config"
