#!/usr/bin/env bash
# Installs the token-bar CLI and the /token command for Claude Code.
# Run from the root of a clone of this repo: ./install.sh
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
claude_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
skill_source="$repo_dir/plugins/token-bar/skills/token/SKILL.md"
skill_target="$claude_dir/skills/token"

if ! command -v cargo >/dev/null 2>&1; then
  echo "token-bar needs Rust to build. Install it from https://rustup.rs, then run ./install.sh again." >&2
  exit 1
fi

echo "Installing the token-bar CLI..."
cargo install --path "$repo_dir/crates/token-bar-cli" --locked --force

echo "Adding the /token command to $skill_target ..."
mkdir -p "$skill_target"
cp "$skill_source" "$skill_target/SKILL.md"

echo
echo "Done. Next:"
echo "  1. In a terminal, run: token-bar"
echo "     Answer the one question about the percent Claude shows you."
echo "  2. In Claude Code, type: /token"
