#!/usr/bin/env bash
# Regression tests for the Pi launcher: it execs the pinned store-path bundle
# under the corrected Node, and Home Manager keeps its profile ahead of
# Homebrew so the launcher wins on a fresh macOS login.
set -euo pipefail

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CONFIG=$(cat "$ROOT/home.nix")
LAUNCHER=$(cat "$ROOT/home/pi-launcher.sh")

assert_contains "$CONFIG" 'home.sessionPath = [ "${config.home.profileDirectory}/bin" ];' \
  "Home Manager prepends its profile in session variables"
assert_contains "$CONFIG" 'path=("${config.home.profileDirectory}/bin" $path)' \
  "zsh reasserts the profile after Homebrew shellenv"
assert_contains "$CONFIG" 'builtins.readFile ./home/pi-launcher.sh' \
  "the packaged launcher uses the tested launcher source"
assert_contains "$CONFIG" '"@PI_CLI@"' \
  "the packaged launcher injects the pinned Pi bundle path"
assert_contains "$CONFIG" 'dist/bundle/cli.js' \
  "the packaged launcher targets the bundled CLI"

assert_contains "$LAUNCHER" 'worker-thread file-descriptor regression' \
  "the launcher preserves the worker-thread regression rationale"
assert_contains "$LAUNCHER" 'exec env PATH="@UNSTABLE_NODE_BIN@:$PATH" "@UNSTABLE_NODE_BIN@/node" "@PI_CLI@" "$@"' \
  "the launcher execs the corrected Node on the pinned bundle"
assert_not_contains "$LAUNCHER" 'for directory in $PATH' \
  "the launcher no longer searches PATH for a floating pi"
assert_not_contains "$LAUNCHER" 'not found on PATH' \
  "the launcher has no floating-install fallback"

fixture=$(dotfiles_test_tmproot pi-launcher)
brew_bin="$fixture/homebrew/bin"
profile_bin="$fixture/nix-profile/bin"
node_bin="$fixture/corrected-node/bin"
mkdir -p "$brew_bin" "$profile_bin" "$node_bin"

# Stand in for the corrected Node: report its identity, then run the given
# "script" (the fixture's fake bundle) under bash so the test can observe the
# environment the bundle would see.
cat > "$node_bin/node" <<'EOF'
#!/usr/bin/env bash
printf 'launcher-node=corrected\n'
script=$1
shift
exec bash "$script" "$@"
EOF
chmod +x "$node_bin/node"

# The profile's ordinary Node remains the default runtime for unrelated commands.
cat > "$profile_bin/node" <<'EOF'
#!/usr/bin/env bash
printf 'default-node\n'
EOF
chmod +x "$profile_bin/node"

# The pinned Pi bundle, standing in for the store-path cli.js.
cat > "$profile_bin/pinned-cli.js" <<'EOF'
#!/usr/bin/env bash
printf 'pinned-pi node=%s\n' "$(command -v node)"
EOF
chmod +x "$profile_bin/pinned-cli.js"

# Recreate the generated package launcher with the corrected Node and pinned
# bundle paths substituted.
launcher=${LAUNCHER//@UNSTABLE_NODE_BIN@/$node_bin}
launcher=${launcher//@PI_CLI@/$profile_bin/pinned-cli.js}
printf '%s\n' "$launcher" > "$profile_bin/pi"
chmod +x "$profile_bin/pi"

# This is the failure mode from a fresh macOS login: /etc/zshrc's brew shellenv
# puts Homebrew before the per-user Nix profile.
plain_path="$brew_bin:$profile_bin:$node_bin:/usr/bin:/bin"
[ "$(PATH="$plain_path" command -v pi)" = "$profile_bin/pi" ] \
  || fail "fixture does not represent Homebrew-before-Nix Pi resolution"

# This is the generated zsh fix, exercised in a clean shell with the same PATH.
path_fix='path=("$PROFILE_BIN" $path)'
out=$(
  PATH="$plain_path" PROFILE_BIN="$profile_bin" PATH_FIX="$path_fix" \
    zsh -dfc '
      typeset -U path
      eval "$PATH_FIX"
      printf "resolved=%s\n" "$(command -v pi)"
      printf "ordinary-node=%s\n" "$(node)"
      pi
    '
)

assert_contains "$out" "resolved=$profile_bin/pi" \
  "Homebrew-before-Nix PATH reaches the corrected launcher"
assert_contains "$out" 'ordinary-node=default-node' \
  "unrelated commands retain the default Node runtime"
assert_contains "$out" 'launcher-node=corrected' \
  "the launcher execs the corrected Node binary"
assert_contains "$out" "pinned-pi node=$node_bin/node" \
  "the launcher runs the pinned bundle with the corrected Node on PATH"

pass "Pi launcher execs the pinned bundle and wins over Homebrew without changing unrelated Node selection"
