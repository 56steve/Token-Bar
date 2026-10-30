#!/usr/bin/env python3
"""Shows your Codex usage limits, read from Codex's own session logs.

Standard library only. Codex records your rate limits in every session it writes
under ~/.codex/sessions/, so nothing needs to be set up or calibrated.
"""

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_FILES = 25
BAR_WIDTH = 20
LOOKBACK_MINUTES = 60
FRESH_MINUTES = 30
WINDOWS = ("primary", "secondary")
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
    if not any(limits.get(key) for key in WINDOWS):
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    return when, limits


def read_readings(sessions):
    """Every rate-limit reading in the most recently written session files, oldest first."""
    if not sessions.is_dir():
        return []
    files = []
    for path in sessions.rglob("rollout-*.jsonl"):
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    readings = []
    for _, path in sorted(files, reverse=True)[:MAX_FILES]:
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        readings.extend(found for found in map(parse_event, lines) if found)
    return sorted(readings, key=lambda reading: reading[0])


def window_series(readings, key, resets_at):
    """(time, percent) for one window, limited to the current reset period."""
    series = []
    for when, limits in readings:
        window = limits.get(key)
        if window and int(window["resets_at"]) == resets_at:
            series.append((when, float(window["used_percent"])))
    return series


def minutes_to_next_percent(series, now):
    """Minutes until the percent next goes up, from how fast it moved in the last hour.

    Returns None when the reading is old or the percent hasn't moved recently.
    """
    latest_when, latest_percent = series[-1]
    if now - latest_when > timedelta(minutes=FRESH_MINUTES):
        return None
    recent = [(when, percent) for when, percent in series if when >= latest_when - timedelta(minutes=LOOKBACK_MINUTES)]
    first_when, first_percent = recent[0]
    gained = latest_percent - first_percent
    span = (latest_when - first_when).total_seconds() / 60
    if gained <= 0 or span <= 0:
        return None

    rate = gained / span  # percent per minute
    if latest_percent == math.floor(latest_percent):
        # Whole-number readings: the next point arrives one step after the last increase.
        last_increase = next(when for when, percent in recent if percent == latest_percent)
        since = (now - last_increase).total_seconds() / 60
        return max(1 / rate - since, 0)
    since_reading = (now - latest_when).total_seconds() / 60
    return max((math.floor(latest_percent) + 1 - latest_percent) / rate - since_reading, 0)


def format_remaining(seconds):
    minutes = max(int(seconds // 60), 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins} min"


def usage_bar(percent):
    filled = max(0, min(BAR_WIDTH, round(percent / 100 * BAR_WIDTH)))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def estimate_text(minutes):
    if minutes < 1:
        return "under a minute"
    return "~" + format_remaining(minutes * 60)


def render_block(width, label, percent, eta, idle, expired, resets_time, now):
    """One window as a bar, with the next point on the same line and the reset time under it."""
    head = f"{label:<{width + 2}}{usage_bar(percent)}  {math.floor(percent)}%"
    if percent >= 100:
        head += "  limit reached"
    elif eta is not None:
        head += f" → {math.floor(percent) + 1}% in {estimate_text(eta)}"
    elif idle:
        head += " · no recent activity"
    pad = " " * (width + 2)
    if expired:
        return head + f"\n{pad}reset since this reading, so your real usage is probably lower now"
    return head + f"\n{pad}resets in {format_remaining((resets_time - now).total_seconds())}"


def render(readings, now):
    latest_when, latest_limits = readings[-1]
    age = format_remaining((now - latest_when).total_seconds())
    fresh = now - latest_when <= timedelta(minutes=FRESH_MINUTES)

    rows = []
    for key in WINDOWS:
        window = latest_limits.get(key)
        if not window:
            continue
        percent = float(window["used_percent"])
        resets_at = int(window["resets_at"])
        resets_time = datetime.fromtimestamp(resets_at, timezone.utc)
        expired = resets_time <= now
        eta, idle = None, False
        if not expired and fresh and percent < 100:
            series = window_series(readings, key, resets_at)
            eta = minutes_to_next_percent(series, now)
            idle = eta is None and len(series) >= 2
        rows.append((window_label(int(window["window_minutes"])), percent, eta, idle, expired, resets_time))

    width = max(len(row[0]) for row in rows)
    blocks = [render_block(width, *row, now) for row in rows]

    footer = f"Source: Codex session log, saved {age} ago"
    plan = latest_limits.get("plan_type")
    if plan:
        footer += f". Plan: {plan}."
    return "\n\n".join(blocks) + "\n\n" + footer


def main(home, now=None):
    now = now or datetime.now(timezone.utc)
    readings = read_readings(home / ".codex" / "sessions")
    if not readings:
        return "No Codex usage found yet. Use Codex once, then run this again."
    return render(readings, now)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the bars need UTF-8, which some Windows consoles lack
    print(main(Path.home()))
    sys.exit(0)
