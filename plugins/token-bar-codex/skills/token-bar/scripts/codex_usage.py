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
PROJECTION_MAX_HOURS = 6
USE_MARKERS = ('"session_meta"', '"turn_context"', '"token_count"')
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


def recent_readings(series, now):
    """The readings from the last hour, or None when there are too few or the latest is old."""
    if len(series) < 2:
        return None
    latest_when = series[-1][0]
    if now - latest_when > timedelta(minutes=FRESH_MINUTES):
        return None
    return [(when, percent) for when, percent in series if when >= latest_when - timedelta(minutes=LOOKBACK_MINUTES)]


def percent_rate(recent):
    """How fast the percent rose over these readings, in percent per minute. None if it didn't rise."""
    (first_when, first_percent), (latest_when, latest_percent) = recent[0], recent[-1]
    gained = latest_percent - first_percent
    span = (latest_when - first_when).total_seconds() / 60
    return gained / span if gained > 0 and span > 0 else None


def pace_in_percent(series, now):
    """Percent per minute from a series of (time, percent) readings, or None when it can't be told."""
    recent = recent_readings(series, now)
    return percent_rate(recent) if recent else None


def projection_text(percent, rate, minutes_to_reset):
    """Whether the current pace reaches the limit before the window resets.

    Only for windows resetting within PROJECTION_MAX_HOURS, because a pace measured over
    an hour says little about a window that lasts days.
    """
    if rate is None or minutes_to_reset is None or percent >= 100:
        return None
    if minutes_to_reset > PROJECTION_MAX_HOURS * 60:
        return None
    minutes_to_full = (100 - percent) / rate
    if minutes_to_full < minutes_to_reset:
        gap = format_remaining((minutes_to_reset - minutes_to_full) * 60)
        return f"at this pace you'd hit 100% in ~{format_remaining(minutes_to_full * 60)}, about {gap} before the reset"
    return f"at this pace you'll reach ~{round(percent + rate * minutes_to_reset)}% by the reset"


def minutes_to_next_percent(series, now):
    """Minutes until the percent next goes up, from how fast it moved in the last hour.

    Returns None when the reading is old or the percent hasn't moved recently.
    """
    recent = recent_readings(series, now)
    rate = percent_rate(recent) if recent else None
    if rate is None:
        return None
    latest_when, latest_percent = recent[-1]
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


def usage_bar(percent, width=BAR_WIDTH):
    filled = max(0, min(width, round(percent / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def estimate_text(minutes):
    if minutes < 1:
        return "under a minute"
    return "~" + format_remaining(minutes * 60)


def render_block(width, label, percent, eta, idle, expired, resets_time, projection, now):
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
    lines = [head, f"{pad}resets in {format_remaining((resets_time - now).total_seconds())}"]
    if projection:
        lines.append(f"{pad}{projection}")
    return "\n".join(lines)


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
        eta, idle, projection = None, False, None
        if not expired and fresh and percent < 100:
            series = window_series(readings, key, resets_at)
            eta = minutes_to_next_percent(series, now)
            idle = eta is None and len(series) >= 2
            minutes_to_reset = (resets_time - now).total_seconds() / 60
            projection = projection_text(percent, pace_in_percent(series, now), minutes_to_reset)
        rows.append((window_label(int(window["window_minutes"])), percent, eta, idle, expired, resets_time, projection))

    width = max(len(row[0]) for row in rows)
    blocks = [render_block(width, *row, now=now) for row in rows]

    footer = f"Source: Codex session log, saved {age} ago"
    plan = latest_limits.get("plan_type")
    if plan:
        footer += f". Plan: {plan}."
    return "\n\n".join(blocks) + "\n\n" + footer


def read_token_use(sessions, cutoff):
    """Tokens used since `cutoff`, one entry per token_count event.

    Codex records a running total for each session, so each entry is the increase since the
    previous event, attributed to the project and model the session was using at the time.
    """
    if not sessions.is_dir():
        return []
    uses = []
    for path in sessions.rglob("rollout-*.jsonl"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                continue
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        cwd, model, previous_total = None, None, 0
        for line in lines:
            if not any(marker in line for marker in USE_MARKERS):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get("payload") or {}
            if event.get("type") == "session_meta":
                cwd = payload.get("cwd") or cwd
            elif event.get("type") == "turn_context":
                cwd = payload.get("cwd") or cwd
                model = payload.get("model") or model
            elif payload.get("type") == "token_count":
                total = ((payload.get("info") or {}).get("total_token_usage") or {}).get("total_tokens")
                when = parse_time(event.get("timestamp"))
                if total is None or when is None:
                    continue
                delta = max(int(total) - previous_total, 0)
                previous_total = int(total)
                if delta and when >= cutoff:
                    uses.append({"when": when, "tokens": delta,
                                 "project": Path(cwd).name if cwd else "unknown", "model": model or "unknown"})
    return uses


def parse_time(stamp):
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def format_tokens(count):
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def grouped_shares(uses, key, top=5):
    """(name, tokens) per group, largest first. Groups past `top` are folded into 'other'."""
    totals = {}
    for use in uses:
        name = key(use)
        totals[name] = totals.get(name, 0) + use["tokens"]
    ordered = sorted(totals.items(), key=lambda item: -item[1])
    shown = ordered[:top]
    rest = sum(tokens for _, tokens in ordered[top:])
    if rest:
        shown.append(("other", rest))
    return shown


def share_lines(title, rows, total):
    name_width = 26
    lines = [title]
    for name, tokens in rows:
        share = round(tokens / total * 100)
        shown = name if len(name) <= name_width - 1 else name[: name_width - 2] + "…"
        lines.append(f"  {shown:<{name_width}}{usage_bar(share, 12)}  {share:>3}%")
    return lines


def breakdown_text(uses, label):
    total = sum(use["tokens"] for use in uses)
    if not total:
        return f"No Codex usage in the current {label} window yet."
    lines = [f"Where your tokens went in the current {label} window · {format_tokens(total)} tokens counted", ""]
    lines += share_lines("By project", grouped_shares(uses, lambda use: use["project"]), total)
    lines += [""] + share_lines("By model", grouped_shares(uses, lambda use: use["model"]), total)
    lines += ["", "These are shares of the tokens Codex recorded. Codex weighs tokens in ways the logs",
              "don't show, so they are not exact shares of your limit."]
    return "\n".join(lines)


def breakdown(home, now):
    sessions = home / ".codex" / "sessions"
    readings = read_readings(sessions)
    if not readings:
        return "No Codex usage found yet. Use Codex once, then run this again."
    limits = readings[-1][1]
    window = limits.get("primary") or limits.get("secondary")
    length = timedelta(minutes=int(window["window_minutes"]))
    resets_time = datetime.fromtimestamp(int(window["resets_at"]), timezone.utc)
    start = resets_time - length if resets_time > now else now - length
    uses = [use for use in read_token_use(sessions, start) if use["when"] <= now]
    return breakdown_text(uses, window_label(int(window["window_minutes"])).lower())


def main(home, now=None, command="status"):
    now = now or datetime.now(timezone.utc)
    if command == "breakdown":
        return breakdown(home, now)
    readings = read_readings(home / ".codex" / "sessions")
    if not readings:
        return "No Codex usage found yet. Use Codex once, then run this again."
    return render(readings, now)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the bars need UTF-8, which some Windows consoles lack
    requested = sys.argv[1] if len(sys.argv) > 1 else "status"
    if requested not in ("status", "breakdown"):
        print("Usage: codex_usage.py [status | breakdown]")
        sys.exit(1)
    print(main(Path.home(), command=requested))
    sys.exit(0)
