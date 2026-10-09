#!/usr/bin/env bash
# Run the npm-installed Pi with the pinned Node runtime that avoids the Darwin
# worker-thread file-descriptor regression in the primary nixpkgs Node.
set -eu

pi_path=
old_ifs=$IFS
IFS=:
for directory in $PATH; do
  candidate="$directory/pi"
  if [ -x "$candidate" ] && [ "$candidate" != "$0" ]; then
    pi_path="$candidate"
    break
  fi
done
IFS=$old_ifs

if [ -z "$pi_path" ]; then
  echo "pi: the npm-installed Pi executable was not found on PATH" >&2
  exit 127
fi

exec env PATH="@UNSTABLE_NODE_BIN@:$PATH" "$pi_path" "$@"
