#!/usr/bin/env python3
"""Runs the newest installed token-bar status line.

The status line setting points at this file, which is copied to a stable place.
Plugin updates change the folder the script lives in, so this finds the newest
one each time and the setting never goes stale.
"""

import glob
import os
import re
import sys

PATTERN = "~/.claude/plugins/cache/token-bar/token-bar/*/scripts/token_bar.py"


def version_key(path):
    """Sorts install paths by the version folder, so 0.1.10 comes after 0.1.9."""
    version = os.path.normpath(path).split(os.sep)[-3]
    return [int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", version)]


def newest(paths):
    return max(paths, key=version_key) if paths else None


def main():
    script = newest(glob.glob(os.path.expanduser(PATTERN)))
    if not script:
        print("token-bar: plugin not found")
        return 0
    os.execv(sys.executable, [sys.executable, script, "statusline"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
