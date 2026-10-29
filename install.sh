#!/usr/bin/env bash
# Installs the token-bar CLI, the /token command, and (with your consent) the
# Claude Code status line that reports Claude's own usage numbers.
# Run from the root of a clone of this repo: ./install.sh
#   --statusline   turn on the status line without asking
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
claude_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
skill_source="$repo_dir/plugins/token-bar/skills/token/SKILL.md"
skill_target="$claude_dir/skills/token"
settings_file="$claude_dir/settings.json"
want_statusline=""
[ "${1:-}" = "--statusline" ] && want_statusline="yes"

if ! command -v cargo >/dev/null 2>&1; then
  echo "token-bar needs Rust to build. Install it from https://rustup.rs, then run ./install.sh again." >&2
  exit 1
fi

echo "Installing the token-bar CLI..."
cargo install --path "$repo_dir/crates/token-bar-cli" --locked --force

echo "Adding the /token command to $skill_target ..."
mkdir -p "$skill_target"
cp "$skill_source" "$skill_target/SKILL.md"

# Sets Claude Code's status line to token-bar, unless the user already has a different one.
# Prints one of: set, kept, already, declined.
configure_statusline() {
  local bin
  bin="$(command -v token-bar)"
  mkdir -p "$claude_dir"
  python3 - "$settings_file" "$bin statusline" <<'PY'
import json, os, sys

path, command = sys.argv[1], sys.argv[2]
settings = {}
if os.path.exists(path):
    with open(path) as handle:
        settings = json.load(handle)

current = settings.get("statusLine")
if current and current.get("command") == command:
    print("already")
elif current:
    print("kept")
else:
    settings["statusLine"] = {"type": "command", "command": command}
    with open(path, "w") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    print("set")
PY
}

if [ -z "$want_statusline" ] && [ -t 0 ]; then
  read -r -p "Show Claude's usage numbers in Claude Code's status line? This edits $settings_file. [y/N] " answer
  if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
    want_statusline="yes"
  fi
fi

if [ -n "$want_statusline" ]; then
  case "$(configure_statusline)" in
    set) echo "Status line set. Send one message in Claude Code, then run /token." ;;
    already) echo "Status line was already set to token-bar." ;;
    kept) echo "You already have a status line, so it was left as it is. Add 'token-bar statusline' to it yourself to get live numbers." ;;
  esac
else
  echo "Skipped the status line. /token will use your calibrated limits instead."
fi

echo
echo "Done. Next:"
echo "  1. In a terminal, run: token-bar"
echo "     Answer the questions about the percents Claude shows you, or press Enter to skip."
echo "  2. In Claude Code, type: /token"
