#!/usr/bin/env python3
"""Shows your Codex usage limits, read from Codex's own session logs.

Standard library only. Codex records your rate limits in every session it writes
under ~/.codex/sessions/, so nothing needs to be set up or calibrated.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_FILES = 25
WINDOW_LABELS = {300: "5-hour", 10080: "Weekly", 43200: "Monthly"}


def window_label(minutes):
    if minutes in WINDOW_LABELS:
        return WINDOW_LABELS[minutes]
    if minutes < 24 * 60:
        return f"{minutes // 60}-hour"
    return f"{minutes // (24 * 60)}-day"


def parse_event(line):
    """(time, rate_limits) for a token_count line that carries rate limits, else None."""
    if '"token_count"' not in line or '"rate_limits"' not in line:
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return None
    payload = event.get("payload") or {}
    limits = payload.get("rate_limits")
    stamp = event.get("timestamp")
    if payload.get("type") != "token_count" or not limits or not stamp:
        return None
    # Codex also writes entries for other limit buckets that carry no windows. Skip those.
    if not (limits.get("primary") or limits.get("secondary")):
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    return when, limits


def latest_rate_limits(sessions):
    """The newest rate-limit reading in the most recently written session files."""
    if not sessions.is_dir():
        return None
    files = []
    for path in sessions.rglob("rollout-*.jsonl"):
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _, path in sorted(files, reverse=True)[:MAX_FILES]:
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            found = parse_event(line)
            if found:
                return found
    return None


def format_remaining(seconds):
    minutes = max(int(seconds // 60), 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins} min"


def render(when, limits, now):
    age = format_remaining((now - when).total_seconds())
    blocks = []
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if not window:
            continue
        lines = [f"{window_label(int(window['window_minutes']))} usage   {math.floor(float(window['used_percent']))}%"]
        resets_at = datetime.fromtimestamp(int(window["resets_at"]), timezone.utc)
        if resets_at > now:
            lines.append(f"  Resets       in {format_remaining((resets_at - now).total_seconds())}")
        else:
            lines.append("  Reset since this reading, so your real usage is probably lower now.")
        blocks.append("\n".join(lines))

    footer = f"Source: Codex session log, saved {age} ago"
    plan = limits.get("plan_type")
    if plan:
        footer += f". Plan: {plan}."
    return "\n\n".join(blocks) + "\n\n" + footer


def main(home, now=None):
    now = now or datetime.now(timezone.utc)
    found = latest_rate_limits(home / ".codex" / "sessions")
    if not found:
        return "No Codex usage found yet. Use Codex once, then run this again."
    when, limits = found
    return render(when, limits, now)


if __name__ == "__main__":
    print(main(Path.home()))
    sys.exit(0)
