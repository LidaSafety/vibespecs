#!/usr/bin/env bash
# Install this as a Claude Code PreToolUse hook by placing it in
# ~/.claude/hooks/pretooluse.sh (or wherever your Claude Code version
# expects hooks) and making it executable.
#
# It reads the hook JSON from stdin and delegates to safe-scaffold's
# `hook` subcommand. Exit code 0 = allow, 2 = deny. Anything else
# (treated as an error by Claude Code) also denies by convention.
#
# Set SAFE_SCAFFOLD_POLICY to point at the policy you want enforced
# in this session. Set SAFE_SCAFFOLD_JOURNAL to redirect the audit log.

set -euo pipefail

# Default policy location: per-project under .safe-scaffold/policy.json
# if it exists, else $HOME/.safe_scaffold/policy.json.
if [[ -z "${SAFE_SCAFFOLD_POLICY:-}" ]]; then
  if [[ -f "$(pwd)/.safe-scaffold/policy.json" ]]; then
    export SAFE_SCAFFOLD_POLICY="$(pwd)/.safe-scaffold/policy.json"
  else
    export SAFE_SCAFFOLD_POLICY="$HOME/.safe_scaffold/policy.json"
  fi
fi

exec python -m safe_scaffold.cli hook
