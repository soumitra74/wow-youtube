#!/usr/bin/env bash
set -eu
root="$(git rev-parse --show-toplevel)"
hook_dir="$root/.git/hooks"
mkdir -p "$hook_dir"
cp "$root/scripts/githooks/pre-commit" "$hook_dir/pre-commit"
cp "$root/scripts/githooks/pre-push" "$hook_dir/pre-push"
chmod +x \
  "$root/scripts/githooks/pre-commit" \
  "$root/scripts/githooks/pre-push" \
  "$hook_dir/pre-commit" \
  "$hook_dir/pre-push"
echo "Installed git hooks:"
echo "  pre-commit -> unit tests (pytest, skip e2e)"
echo "  pre-push    -> e2e tests (pytest tests/e2e)"
