#!/usr/bin/env bash
# Regression tests for Homebrew activation declarations.
set -euo pipefail

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CONFIG=$(cat "$ROOT/configuration.nix")
README=$(cat "$ROOT/README.md")

assert_contains "$CONFIG" 'onActivation.cleanup = "zap"' \
  "Homebrew cleanup policy remains explicit"
assert_contains "$CONFIG" '"handy"' \
  "Handy is declared as a reproducible cask"
assert_not_contains "$CONFIG" '"opensuperwhisper"' \
  "the superseded transcription cask is no longer declared"
assert_contains "$README" 'official Homebrew `handy` cask' \
  "documentation explains how Handy survives activation cleanup"

pass "Homebrew activation preserves the declarative Handy cask"
