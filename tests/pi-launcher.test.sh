#!/usr/bin/env bash
# Regression tests for the Pi launcher and Homebrew-before-Nix shell ordering.
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
assert_contains "$LAUNCHER" 'worker-thread file-descriptor regression' \
  "the launcher preserves the worker-thread regression rationale"

fixture=$(dotfiles_test_tmproot pi-launcher)
brew_bin="$fixture/homebrew/bin"
profile_bin="$fixture/nix-profile/bin"
node_bin="$fixture/corrected-node/bin"
mkdir -p "$brew_bin" "$profile_bin" "$node_bin"

cat > "$brew_bin/pi" <<'EOF'
#!/usr/bin/env bash
printf 'npm-installed-pi node=%s\n' "$(node)"
EOF
chmod +x "$brew_bin/pi"

cat > "$node_bin/node" <<'EOF'
#!/usr/bin/env bash
printf 'corrected-node\n'
EOF
chmod +x "$node_bin/node"

# The profile's ordinary Node remains the default runtime for unrelated commands.
cat > "$profile_bin/node" <<'EOF'
#!/usr/bin/env bash
printf 'default-node\n'
EOF
chmod +x "$profile_bin/node"

# Recreate the generated package launcher with the pinned Node path substituted.
launcher=${LAUNCHER//@UNSTABLE_NODE_BIN@/$node_bin}
printf '%s\n' "$launcher" > "$profile_bin/pi"
chmod +x "$profile_bin/pi"

# This is the failure mode from a fresh macOS login: /etc/zshrc's brew shellenv
# puts Homebrew before the per-user Nix profile.
plain_path="$brew_bin:$profile_bin:$node_bin:/usr/bin:/bin"
[ "$(PATH="$plain_path" command -v pi)" = "$brew_bin/pi" ] \
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
assert_contains "$out" 'npm-installed-pi node=corrected-node' \
  "the launcher still discovers npm Pi and injects corrected Node"

pass "Pi launcher wins over Homebrew without changing unrelated Node selection"
