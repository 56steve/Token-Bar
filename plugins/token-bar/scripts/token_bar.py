#!/usr/bin/env python3
"""token-bar: see how close you are to your Claude usage limits.

Standard library only. Used by the /token skill and by the Claude Code status line.
Data lives in ~/.claude/token-bar/, next to Claude's own session logs.
"""

import argparse
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PACE_MINUTES = 30
PROJECTS_DAYS = 7
ROUGH_BELOW_PERCENT = 10
BAR_WIDTH = 20
STATUSLINE_BAR_WIDTH = 10
ANSI_RESET = "\033[0m"
ANSI_DIM = "\033[2m"
# (from this percent up, color code), checked from the top: red, yellow, green.
LEVEL_COLORS = ((90, "\033[31m"), (70, "\033[33m"), (0, "\033[32m"))
FRESH_MINUTES = 30
LOOKBACK_MINUTES = 60
PROJECTION_MAX_HOURS = 6
# A weekly pace needs this much of the week behind it before it says anything useful.
AVERAGE_PACE_MIN_HOURS = 12
# An estimate anchored to a sync older than this says it may have drifted.
STALE_SYNC_HOURS = 12
CHART_DAYS = 7
CHART_LABEL_WIDTH = 7
CHART_BAR_WIDTH = 14
# Finishing the window below this percent counts as on track, at or above it as tight.
ON_TRACK_BELOW_PERCENT = 85
WARN_LEVELS = (80, 90)
SHORTCUT_MARKER = "<!-- installed by token-bar -->"
# Bumped when what the scan cache stores changes, so an older cache is read again instead of trusted.
SCAN_CACHE_VERSION = 2
# The four kinds of tokens a request records, which are priced separately.
TOKEN_KINDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
HISTORY_KEEP_SECONDS = 3 * 3600
HISTORY_MAX_ENTRIES = 400

# key -> label, window hours, key of Claude's own reading in rate_limits
WINDOWS = {
    "five_hour": {"label": "5-hour", "hours": 5, "live": "five_hour"},
    "weekly": {"label": "Weekly", "hours": 7 * 24, "live": "seven_day"},
}
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def data_dir(home):
    return home / ".claude" / "token-bar"


def config_path(home):
    return data_dir(home) / "config.json"


def live_path(home):
    return data_dir(home) / "rate_limits.json"


def scan_cache_path(home):
    return data_dir(home) / "scan_cache.json"


def history_path(home):
    return data_dir(home) / "rate_history.json"


def other_live_paths(home):
    """Where the token-bar command-line tool saves the same readings."""
    return [
        home / "Library" / "Application Support" / "token-bar" / "rate_limits.json",
        home / ".config" / "token-bar" / "rate_limits.json",
    ]


def read_live(home):
    """Claude's saved readings, from the plugin's file or the command-line tool's file."""
    for path in [live_path(home), *other_live_paths(home)]:
        saved = read_json(path)
        if saved:
            return saved
    return {}


def append_history(home, rate_limits, now):
    """Keeps a short history of Claude's percents, so the pace can be worked out from them."""
    entry = {"t": int(now.timestamp())}
    for key in ("five_hour", "seven_day"):
        window = rate_limits.get(key) if isinstance(rate_limits, dict) else None
        if isinstance(window, dict) and "used_percentage" in window and "resets_at" in window:
            entry[key] = [float(window["used_percentage"]), int(window["resets_at"])]
    if len(entry) == 1:
        return

    saved = read_json(history_path(home))
    history = [e for e in saved if isinstance(e, dict)] if isinstance(saved, list) else []
    history = [e for e in history if e.get("t", 0) >= entry["t"] - HISTORY_KEEP_SECONDS]
    last = history[-1] if history else None
    unchanged = last is not None and all(last.get(k) == entry.get(k) for k in ("five_hour", "seven_day"))
    if unchanged and entry["t"] - last["t"] < 60:
        return
    history.append(entry)
    write_json(history_path(home), history[-HISTORY_MAX_ENTRIES:])


def window_series(home, key, resets_at):
    """(time, percent) readings for one window, limited to the current reset period."""
    saved = read_json(history_path(home))
    series = []
    for entry in saved if isinstance(saved, list) else []:
        reading = entry.get(key) if isinstance(entry, dict) else None
        if reading and int(reading[1]) == int(resets_at):
            series.append((datetime.fromtimestamp(int(entry["t"]), timezone.utc), float(reading[0])))
    return sorted(series)


def read_json(path):
    """Returns the parsed file, or None when it is missing or unreadable."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_reset(text, now):
    """The next local occurrence of a reset such as "2:20 PM" or "Sun 5:30 AM", in UTC."""
    words = text.strip().split()
    weekday = None
    if words and words[0][:3].lower() in WEEKDAYS:
        weekday = WEEKDAYS[words.pop(0)[:3].lower()]
    clock = " ".join(words).upper()

    moment = None
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            moment = datetime.strptime(clock, fmt).time()
            break
        except ValueError:
            continue
    if moment is None:
        raise ValueError(f"'{text}' is not a reset time. Try something like \"2:20 PM\" or \"Sun 5:30 AM\".")

    today = now.astimezone().date()
    for offset in range(8):
        day = today + timedelta(days=offset)
        if weekday is not None and day.weekday() != weekday:
            continue
        occurrence = datetime.combine(day, moment).astimezone().astimezone(timezone.utc)
        if occurrence > now:
            return occurrence
    raise ValueError(f"could not find the next time for '{text}'")


def window_start(anchor, period, now):
    """Start of the window that contains `now`, counting from the fixed reset `anchor`."""
    return anchor + period * ((now - anchor) // period)


def tokens_of(usage):
    return sum(int(usage.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens"))


def project_name(entry, path):
    """The project a request belongs to: the folder Claude Code was working in."""
    cwd = entry.get("cwd")
    if cwd:
        return Path(cwd).name or str(cwd)
    return path.parent.name


def request_from_line(line, path, cutoff):
    """(request id, [epoch seconds, tokens, project, model, subagent]) for a usage line, else None."""
    try:
        entry = json.loads(line)
    except UnicodeDecodeError:
        # A stray invalid byte in the text. Drop it and read the rest, as reading the file as text would.
        try:
            entry = json.loads(line.decode("utf-8", errors="ignore"))
        except ValueError:
            return None
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None
    usage = (entry.get("message") or {}).get("usage")
    request_id = entry.get("requestId")
    stamp = entry.get("timestamp")
    if not usage or not request_id or not stamp:
        return None
    try:
        when = parse_time(stamp)
    except ValueError:
        return None
    if when < cutoff:
        return None
    model = (entry.get("message") or {}).get("model")
    # The effort the request ran at, when the logs record one. Whatever value appears is kept as it is.
    effort = next((v for v in (entry.get("perTurnEffort"), entry.get("effort")) if isinstance(v, str) and v), None)
    try:
        counts = [int(usage.get(kind) or 0) for kind in TOKEN_KINDS]
        tokens = tokens_of(usage)
    except (TypeError, ValueError, AttributeError):
        return None
    return request_id, [when.timestamp(), tokens, project_name(entry, path), model, bool(entry.get("isSidechain")), effort, *counts]


def read_new_requests(path, state, cutoff):
    """Adds the requests written to `path` since `state['offset']` to `state['requests']`.

    The logs only ever grow at the end, so a file that was read before only needs its new
    bytes. A last line that isn't finished yet is left for next time.
    """
    with path.open("rb") as handle:
        handle.seek(state["offset"])
        data = handle.read()
    complete, newline, tail = data.rpartition(b"\n")
    lines = complete.split(b"\n") if newline else []
    consumed = len(complete) + 1 if newline else 0
    if tail.strip():
        try:
            json.loads(tail)
        except ValueError:
            pass  # half written
        else:
            lines.append(tail)
            consumed = len(data)
    requests = state["requests"]
    for line in lines:
        if b'"usage"' not in line:  # most lines are not model responses, so skip parsing them
            continue
        found = request_from_line(line, path, cutoff)
        if found:
            request_id, record = found
            previous = requests.get(request_id)
            if previous is None or previous[1] < record[1]:
                requests[request_id] = record
    state["offset"] += consumed


def load_scan_cache(cache_file, cutoff):
    """The saved per-file progress, or {} when there is none or it covers too little time."""
    saved = read_json(cache_file) if cache_file else None
    if not isinstance(saved, dict) or saved.get("version") != SCAN_CACHE_VERSION:
        return {}
    files = saved.get("files")
    if not isinstance(files, dict) or not isinstance(saved.get("since"), (int, float)):
        return {}
    if saved["since"] > cutoff.timestamp():
        return {}  # it dropped requests this caller still wants
    return files


def scan_requests(projects, cutoff, cache_file=None):
    """Each request since `cutoff`, with its project, model, and whether a subagent made it.

    Claude Code writes the same request several times as it streams, so each is counted once.
    With a cache_file, what each log file contributed is remembered, so the next run reads only
    what was added since instead of everything again (which takes many seconds on a busy machine).
    """
    if not projects.is_dir():
        return []
    cached = load_scan_cache(cache_file, cutoff)
    files = {}
    by_request = {}
    for path in projects.rglob("*.jsonl"):
        try:
            stat = path.stat()
            if datetime.fromtimestamp(stat.st_mtime, timezone.utc) < cutoff:
                continue
            state = cached.get(str(path))
            valid = isinstance(state, dict) and isinstance(state.get("requests"), dict)
            if not valid or not isinstance(state.get("offset"), int) or state["offset"] > stat.st_size:
                state = {"offset": 0, "requests": {}}  # new, damaged, or shorter than before: start over
            read_new_requests(path, state, cutoff)
        except OSError:
            continue
        files[str(path)] = state
        for request_id, record in state["requests"].items():
            try:
                stamp, tokens, project, model, subagent, effort, *counts = record
                when = datetime.fromtimestamp(stamp, timezone.utc)
            except (TypeError, ValueError, OverflowError, OSError):
                continue  # a damaged entry costs one request, not the whole scan
            previous = by_request.get(request_id)
            if when >= cutoff and (previous is None or previous["tokens"] < tokens):
                by_request[request_id] = {
                    "when": when, "tokens": tokens, "project": project, "model": model, "subagent": subagent,
                    "effort": effort, **dict(zip(TOKEN_KINDS, counts)),
                }
    if cache_file:
        save_scan_cache(cache_file, files, cutoff)
    return list(by_request.values())


def save_scan_cache(cache_file, files, cutoff):
    """Saves per-file progress, dropping requests older than the cutoff. A failure only costs speed."""
    since = cutoff.timestamp()
    pruned = {
        path: {"offset": state["offset"], "requests": {rid: r for rid, r in state["requests"].items() if r[0] >= since}}
        for path, state in files.items()
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": SCAN_CACHE_VERSION, "since": since, "files": pruned}, separators=(",", ":")))
        tmp.replace(cache_file)
    except OSError:
        pass


def scan_usage(projects, cutoff, cache_file=None):
    """(timestamp, tokens) for each request since `cutoff`."""
    return [(request["when"], request["tokens"]) for request in scan_requests(projects, cutoff, cache_file)]


def recent_rate(records, start, now):
    """Tokens per minute over the last PACE_MINUTES, within the current window."""
    pace_start = max(now - timedelta(minutes=PACE_MINUTES), start)
    recent = sum(tokens for when, tokens in records if pace_start <= when <= now)
    return recent / PACE_MINUTES


def next_point(percent, limit, tokens_per_minute):
    """The next whole percent, and minutes until it is reached at the recent pace."""
    if percent >= 100:
        return None, None
    next_percent = math.floor(percent) + 1
    if not limit or tokens_per_minute <= 0:
        return next_percent, None
    tokens_remaining = (next_percent - percent) / 100 * limit
    return next_percent, tokens_remaining / tokens_per_minute


def format_remaining(seconds):
    minutes = max(int(seconds // 60), 0)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins} min"


def format_age(seconds):
    minutes = max(int(seconds // 60), 0)
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 24 * 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes // (24 * 60)}d"


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


def local_time_text(when):
    """A time in the user's own timezone, such as 'Sun 5:30 AM'."""
    local = when.astimezone()
    return f"{local.strftime('%a')} {local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def format_percent(value):
    """A percent with a decimal only when it is small, so 17.5 reads '18' and 5.25 reads '5.3'."""
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}".rstrip("0").rstrip(".")


def pace_verdict(percent, minutes_to_reset, window_hours, now):
    """A verdict on the average pace so far this window, as (headline, full sentence).

    Says whether you are on track, tight or using it too fast, in plain words. Used when the
    weekly limit is the only limit, since the short-pace projection stays quiet for windows
    that reset more than PROJECTION_MAX_HOURS away. Needs some of the window to have passed,
    so it says nothing right after a reset. None when it can't be told.
    """
    if minutes_to_reset is None or minutes_to_reset <= 0 or not 0 < percent < 100:
        return None
    elapsed = window_hours * 60 - minutes_to_reset
    if elapsed < AVERAGE_PACE_MIN_HOURS * 60:
        return None
    rate = percent / elapsed
    minutes_to_full = (100 - percent) / rate
    if minutes_to_full < minutes_to_reset:
        gap = format_remaining((minutes_to_reset - minutes_to_full) * 60)
        lockout = local_time_text(now + timedelta(minutes=minutes_to_full))
        headline = "⚠ Using it too fast"
        return headline, f"{headline}. At this rate you run out ~{lockout}, {gap} before the reset."
    finish = round(percent + rate * minutes_to_reset)
    headline = "✓ On track" if finish < ON_TRACK_BELOW_PERCENT else "◐ Tight"
    return headline, f"{headline}. You'll finish the week near {finish}%."


def average_projection_text(percent, minutes_to_reset, window_hours, now):
    """The full sentence of pace_verdict, or None."""
    verdict = pace_verdict(percent, minutes_to_reset, window_hours, now)
    return verdict[1] if verdict else None


def activity_chart(records, now):
    """One bar per day for the last week, longest for the busiest day, with the tokens beside it.

    Drawn with the same full-height bar characters as the usage bar, because partial blocks
    render at the same height in some fonts. Built from the Claude Code session logs, so it
    shows the shape of your week and not exact usage. Days are local calendar days, with today
    last. None when there is nothing to show.
    """
    today = now.astimezone().date()
    days = [today - timedelta(days=ago) for ago in range(CHART_DAYS - 1, -1, -1)]
    totals = dict.fromkeys(days, 0)
    for when, tokens in records:
        day = when.astimezone().date()
        if day in totals:
            totals[day] += tokens
    busiest = max(totals.values())
    if busiest <= 0:
        return None

    lines = ["Activity · last 7 days"]
    for day in days:
        tokens = totals[day]
        label = "Today" if day == today else day.strftime("%a")
        # Any use shows at least one block, so a light day never looks like an empty one.
        filled = max(1, round(tokens / busiest * CHART_BAR_WIDTH)) if tokens > 0 else 0
        bar = "█" * filled + "░" * (CHART_BAR_WIDTH - filled)
        lines.append(f"{label:<{CHART_LABEL_WIDTH}}{bar}  {format_tokens(tokens)}")
    return "\n".join(lines)


def budget_text(percent, minutes_to_reset, used_today=None):
    """How much of the limit you can use each day and still last until the reset.

    used_today is the percent used since midnight when it can be told, else None. Says nothing
    once the limit is reached or the window has reset.
    """
    if minutes_to_reset is None or minutes_to_reset <= 0 or percent >= 100:
        return None
    remaining = 100 - percent
    days_left = minutes_to_reset / 1440
    if days_left < 1:
        return f"Budget: ~{format_percent(remaining)}% left for the last {format_remaining(minutes_to_reset * 60)}."
    per_day = remaining / days_left
    text = f"Budget: ~{format_percent(per_day)}% a day."
    if used_today is not None:
        text += f" Today: {format_percent(used_today)}%"
        text += ", over budget." if used_today > per_day else "."
    return text


def minutes_to_next_percent(series, now):
    """Minutes until the percent next goes up, from how fast it moved in the last hour.

    Returns None when the reading is old or the percent hasn't moved recently.
    """
    recent = recent_readings(series, now)
    rate = percent_rate(recent) if recent else None
    if rate is None:
        return None
    latest_when, latest_percent = recent[-1]
    since_reading = (now - latest_when).total_seconds() / 60
    if latest_percent == math.floor(latest_percent):
        # Whole-number readings: the next point arrives one step after the last increase.
        last_increase = next(when for when, percent in recent if percent == latest_percent)
        return max(1 / rate - (now - last_increase).total_seconds() / 60, 0)
    return max((math.floor(latest_percent) + 1 - latest_percent) / rate - since_reading, 0)


def usage_bar(percent, width=BAR_WIDTH):
    filled = max(0, min(width, round(percent / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def estimate_text(minutes):
    if minutes < 1:
        return "under a minute"
    return "~" + format_remaining(minutes * 60)


def render_block(label, width, percent, nxt, minutes, idle, resets_in, projection=None, budget=None):
    """One limit as a bar, with the next point on the same line and the reset time under it."""
    head = f"{label:<{width + 2}}{usage_bar(percent)}  {math.floor(percent)}%"
    if percent >= 100:
        head += "  limit reached"
    elif nxt is not None and minutes is not None:
        head += f" → {nxt}% in {estimate_text(minutes)}"
    elif idle:
        head += " · no recent activity"
    lines = [head]
    if resets_in is not None:
        lines.append(f"{' ' * (width + 2)}resets in {resets_in}")
    if projection:
        lines.append(f"{' ' * (width + 2)}{projection}")
    if budget:
        lines.append(f"{' ' * (width + 2)}{budget}")
    return "\n".join(lines)


def percent_since_sync(setting, records, start, now):
    """The percent you last synced plus the usage since, as (percent, seconds since the sync).

    Anchoring to the last sync keeps the error to the usage since then, instead of letting a
    mismatch in the tokens-per-percent ratio grow across the whole window. None when there is
    no usable sync in the current window, so the caller falls back to counting the whole window.
    """
    synced = (setting or {}).get("synced")
    limit = (setting or {}).get("limit")
    if not isinstance(synced, dict) or not limit:
        return None
    try:
        synced_at = parse_time(synced["at"])
        base = float(synced["percent"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not start <= synced_at <= now:
        return None
    since = sum(tokens for when, tokens in records if synced_at < when <= now)
    return min(base + since / limit * 100, 100.0), (now - synced_at).total_seconds()


def estimate_percent(setting, records, start, now):
    """(percent, seconds since the sync it is anchored to, or None) from a saved calibration."""
    anchored = percent_since_sync(setting, records, start, now)
    if anchored:
        return anchored
    used = sum(tokens for when, tokens in records if start <= when <= now)
    return used / setting["limit"] * 100, None


SECTIONS = ("summary", "verdict", "budget", "chart", "all")
SECTION_ALIASES = {"week": "chart"}


def build_report(home, now, section="summary"):
    """The report. The summary is the bars with a one-line headline; a section opens one detail.

    section is one of SECTIONS (or an alias): "verdict", "budget" and "chart" add that detail
    under the bars, and "all" adds everything.
    """
    section = SECTION_ALIASES.get(section, section)
    if section not in SECTIONS:
        raise ValueError(f"'{section}' is not a section. Choose from: {', '.join(SECTIONS[1:])}")
    config = read_json(config_path(home)) or {}
    live = read_live(home)
    readings = live.get("rate_limits") or {}
    records = None

    entries, sources, missing, shown = [], {}, [], set()
    estimated = False
    sync_ages = []
    weekly_only = bool(config.get("weekly_only"))
    no_five_hour_reported = False
    for key, window in WINDOWS.items():
        if key == "five_hour" and weekly_only:
            continue
        setting = config.get(key)
        reading = readings.get(window["live"])
        if not setting and not reading:
            if key == "five_hour" and readings.get(WINDOWS["weekly"]["live"]):
                # Claude reports a weekly limit but no 5-hour one, as on Ultra. Not something to set up.
                no_five_hour_reported = True
            else:
                missing.append(key)
            continue
        if records is None:
            records = scan_usage(home / ".claude" / "projects", now - timedelta(days=PROJECTS_DAYS), scan_cache_path(home))

        period = timedelta(hours=window["hours"])
        limit = setting.get("limit") if setting else None
        anchor = parse_time(setting["reset_at"]) if setting and setting.get("reset_at") else None
        start = window_start(anchor, period, now) if anchor else now - period
        tokens_per_minute = recent_rate(records, start, now)

        if reading:
            percent = float(reading["used_percentage"])
            resets_at = datetime.fromtimestamp(int(reading["resets_at"]), timezone.utc)
            saved_at = datetime.fromtimestamp(int(live.get("saved_at", 0)), timezone.utc)
            sources[window["label"]] = f"Claude Code, saved {format_age((now - saved_at).total_seconds())} ago"
            series = window_series(home, window["live"], reading["resets_at"])
            nxt = math.floor(percent) + 1 if percent < 100 else None
            minutes = minutes_to_next_percent(series, now)
            if minutes is None and limit and percent < 100:
                minutes = next_point(percent, limit, tokens_per_minute)[1]
            fresh = now - saved_at <= timedelta(minutes=FRESH_MINUTES)
            idle = minutes is None and fresh and len(series) >= 2 and percent < 100
            rate = pace_in_percent(series, now)
            if rate is None and limit and tokens_per_minute > 0:
                rate = tokens_per_minute / limit * 100
        else:
            percent, sync_age = estimate_percent(setting, records, start, now)
            if sync_age is not None:
                sync_ages.append(sync_age)
            resets_at = start + period if anchor else None
            sources[window["label"]] = "estimate from your calibration"
            estimated = True
            nxt, minutes = next_point(percent, limit, tokens_per_minute)
            idle = nxt is not None and minutes is None
            rate = tokens_per_minute / limit * 100 if tokens_per_minute > 0 else None

        resets_in = format_remaining((resets_at - now).total_seconds()) if resets_at else None
        minutes_to_reset = (resets_at - now).total_seconds() / 60 if resets_at else None
        projection = projection_text(percent, rate, minutes_to_reset)
        headline = None
        if projection is None and key == "weekly" and "five_hour" not in shown:
            verdict = pace_verdict(percent, minutes_to_reset, window["hours"], now)
            if verdict:
                headline, projection = verdict
        budget = None
        if key == "weekly":
            used_today = None
            if limit:
                midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
                since = max(midnight.astimezone(timezone.utc), start)
                used_today = sum(tokens for when, tokens in records if since <= when <= now) / limit * 100
            budget = budget_text(percent, minutes_to_reset, used_today)
        entries.append((window["label"], percent, nxt, minutes, idle, resets_in, projection, budget, headline))
        shown.add(key)

    live_on = "on" if readings else "off"
    width = max((len(entry[0]) for entry in entries), default=0)
    blocks = []
    for label, percent, nxt, minutes, idle, resets_in, projection, budget, headline in entries:
        if section in ("verdict", "all"):
            line = projection
        elif section == "summary":
            line = headline or projection  # a short headline where there is a longer verdict to open
        else:
            line = None
        blocks.append(render_block(
            label, width, percent, nxt, minutes, idle, resets_in, line,
            budget if section in ("budget", "all") else None,
        ))
    if not blocks:
        lines = ["Setup needed. Ask the user for each of these, then run calibrate:"]
        for key in missing:
            label = WINDOWS[key]["label"].lower()
            lines.append(f"  {key}: the {label} percent Claude shows now, and when it resets")
        lines.append(f"Live numbers: {live_on}")
        return "\n".join(lines) + shortcut_hint(home)

    text = "\n\n".join(blocks)
    chart = activity_chart(records or [], now)
    if section in ("chart", "all") and chart:
        text += "\n\n" + chart
    available = {
        "verdict": any(entry[6] for entry in entries),
        "budget": any(entry[7] for entry in entries),
        "chart": bool(chart),
    }
    if section == "summary":
        # Only list what the summary is hiding: a verdict is hidden only when it was cut to a headline.
        hidden = dict(available, verdict=any(entry[8] for entry in entries))
        names = [name for name, there in hidden.items() if there]
        if names:
            text += f"\n\nMore detail: {' · '.join(names)} · all"
    elif section != "all" and not available[section]:
        text += f"\n\nNo {section} to show yet."
    distinct = set(sources.values())
    if len(distinct) == 1:
        text += f"\n\nSource: {distinct.pop()}"
    else:
        text += "\n\nSource: " + "; ".join(f"{name}: {how}" for name, how in sources.items())
    if estimated:
        ask = "Tell me the % your app shows and I'll sync to it."
        if not sync_ages:
            text += f"\n\n≈ Estimate, can be a few points off. {ask}"
        else:
            age = format_age(max(sync_ages))
            if max(sync_ages) > STALE_SYNC_HOURS * 3600:
                text += f"\n\n≈ Estimate from your sync {age} ago, so it may have drifted. {ask}"
            else:
                text += f"\n\n≈ Estimate from your sync {age} ago, can be a few points off. {ask}"
    if no_five_hour_reported:
        text += "\n\nClaude reports no 5-hour limit for your account, only a weekly one."
    if missing:
        names = ", ".join(WINDOWS[key]["label"] for key in missing)
        text += f"\n\nNot set up yet: {names}. Ask the user for its percent and reset time to add it."
    if not records and not readings:
        text += f"\n\nNo Claude Code usage found in the last {PROJECTS_DAYS} days."
    return f"{text}\nLive numbers: {live_on}{shortcut_hint(home)}"


def model_label(name):
    """'claude-sonnet-5-5' becomes 'Sonnet 5.5'. Names that don't fit the pattern are shown as they are."""
    if not name:
        return "unknown"
    match = re.fullmatch(r"claude-([a-z]+)-(\d+(?:-\d{1,2})*)(?:-\d{8})?", name)
    if not match:
        return name
    return f"{match.group(1).capitalize()} {match.group(2).replace('-', '.')}"


def format_tokens(count):
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)


def grouped_shares(requests, key, top=5):
    """(name, tokens) per group, largest first. Groups past `top` are folded into 'other'."""
    totals = {}
    for request in requests:
        name = key(request)
        totals[name] = totals.get(name, 0) + request["tokens"]
    ordered = sorted(totals.items(), key=lambda item: -item[1])
    shown = ordered[:top]
    rest = sum(tokens for _, tokens in ordered[top:])
    if rest:
        shown.append(("other", rest))
    return shown


def share_lines(title, rows, total, notes=None):
    """A titled list of bars. `notes` maps a row's name to extra text shown after its percent."""
    name_width = 26
    lines = [title]
    for name, tokens in rows:
        share = round(tokens / total * 100)
        shown = name if len(name) <= name_width - 1 else name[: name_width - 2] + "…"
        note = f"  {notes[name]}" if notes and name in notes else ""
        lines.append(f"  {shown:<{name_width}}{usage_bar(share, 12)}  {share:>3}%{note}")
    return lines


def request_notes(requests, key):
    """{group name: '41.7k requests'} for each group, so a share can be read as a number of requests too."""
    counts = {}
    for request in requests:
        name = key(request)
        counts[name] = counts.get(name, 0) + 1
    return {name: f"{format_tokens(count)} request{'' if count == 1 else 's'}" for name, count in counts.items()}


def effort_label(request):
    return (request.get("effort") or "not recorded").capitalize()


def model_name(request):
    return model_label(request["model"])


# What each kind of token is called in the prices file, matched to the scan's names for them.
PRICE_KINDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_write": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
}
PRICES_EXAMPLE = """{
  "currency": "$",
  "per_million_tokens": {
    "your-model-id": {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "a-family-of-models-*": {"input": 0.0, "output": 0.0}
  }
}"""


def prices_path(home):
    return data_dir(home) / "prices.json"


def load_prices(home):
    """(prices, currency symbol) from the user's prices.json, or None when there is no such file.

    token-bar ships with no prices: they change, and they differ by account. Raises ValueError,
    saying what is wrong, when the file isn't in the shape described in PRICES_EXAMPLE.
    """
    path = prices_path(home)
    if not path.exists():
        return None
    data = read_json(path)
    table = data.get("per_million_tokens") if isinstance(data, dict) else None
    if not isinstance(table, dict) or not table:
        raise ValueError(f"{path} should hold a \"per_million_tokens\" table, like this:\n{PRICES_EXAMPLE}")
    prices = {}
    for pattern, kinds in table.items():
        if not isinstance(kinds, dict):
            raise ValueError(f"{path}: the entry for \"{pattern}\" should list prices, such as {{\"input\": 3.0, \"output\": 15.0}}")
        for kind, value in kinds.items():
            if kind not in PRICE_KINDS:
                raise ValueError(f"{path}: \"{kind}\" is not a kind of token. Use: {', '.join(PRICE_KINDS)}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{path}: the {kind} price for \"{pattern}\" should be a number, 0 or more")
        prices[pattern] = {kind: float(value) for kind, value in kinds.items()}
    currency = data.get("currency")
    return prices, currency if isinstance(currency, str) and currency else "$"


def price_for(model, prices):
    """The prices for a model id: an exact match, else the longest 'family-*' entry that fits, else None."""
    if not model:
        return None
    if model in prices:
        return prices[model]
    families = [p for p in prices if p.endswith("*") and model.startswith(p[:-1])]
    return prices[max(families, key=len)] if families else None


def request_cost(request, price):
    """What one request would cost at these prices, in the price file's currency."""
    return sum(request[PRICE_KINDS[kind]] * per_million for kind, per_million in price.items()) / 1_000_000


def priced_costs(requests, prices, key):
    """({group name: cost}, {model id: requests with no price}) for these requests."""
    costs, unpriced = {}, {}
    for request in requests:
        price = price_for(request["model"], prices)
        if price is None:
            name = request["model"] or "unknown"
            unpriced[name] = unpriced.get(name, 0) + 1
            continue
        name = key(request)
        costs[name] = costs.get(name, 0.0) + request_cost(request, price)
    return costs, unpriced


def cost_lines(title, costs, symbol):
    """A titled list of bars for what each group cost, largest first, with a total."""
    total = sum(costs.values())
    ordered = sorted(costs.items(), key=lambda item: -item[1])
    lines = [title]
    for name, amount in ordered[:6]:
        share = round(amount / total * 100) if total else 0
        shown = name if len(name) <= 25 else name[:24] + "…"
        lines.append(f"  {shown:<26}{usage_bar(share, 12)}  {symbol}{amount:>10,.2f}  {share:>3}%")
    rest = sum(amount for _, amount in ordered[6:])
    if rest:
        lines.append(f"  {'other':<26}{'':<14}  {symbol}{rest:>10,.2f}")
    lines.append(f"  {'Total':<26}{'':<14}  {symbol}{total:>10,.2f}")
    return lines


def cost_text(requests, loaded, window_label, prices_file):
    """What the tokens would cost at the user's own prices, by model and by effort."""
    if not requests:
        return f"No Claude Code usage in the current {window_label} window yet."
    if loaded is None:
        seen = sorted({r["model"] for r in requests if r["model"]})[:8]
        lines = [
            "Cost needs a prices file, because token-bar has no built-in prices: they change, and they differ by account.",
            "Create this file with your own prices per million tokens, then run it again:",
            f"  {prices_file}",
            "",
            PRICES_EXAMPLE,
            "",
            "An id ending in * matches a family of models. Kinds you leave out count as 0, and models you leave out show as \"no price\".",
        ]
        if seen:
            lines.append("Models seen in this window: " + ", ".join(seen))
        return "\n".join(lines)

    prices, symbol = loaded
    by_model, unpriced = priced_costs(requests, prices, model_name)
    lines = [f"What these tokens would cost at your prices · current {window_label} window", ""]
    if by_model:
        lines += cost_lines("By model", by_model, symbol)
        if any(request.get("effort") for request in requests):
            lines += [""] + cost_lines("By effort", priced_costs(requests, prices, effort_label)[0], symbol)
    if unpriced:
        missing = ", ".join(f"{name} ({count:,} request{'' if count == 1 else 's'})" for name, count in sorted(unpriced.items()))
        lines += ["", f"No price for: {missing}"]
    lines += [
        "",
        "This is the cost of the input, output and cache tokens at the prices in your prices.json.",
        "A subscription isn't billed per token, so it isn't what you pay.",
        "It shows which models and efforts are the expensive ones.",
    ]
    return "\n".join(lines)


def window_bounds(home, key, now):
    """Start of the current window: from Claude's reset time, your calibrated reset, or a rolling guess."""
    window = WINDOWS[key]
    period = timedelta(hours=window["hours"])
    reading = (read_live(home).get("rate_limits") or {}).get(window["live"])
    if reading:
        reset = datetime.fromtimestamp(int(reading["resets_at"]), timezone.utc)
        if reset > now:
            return reset - period
    setting = (read_json(config_path(home)) or {}).get(key)
    if setting and setting.get("reset_at"):
        return window_start(parse_time(setting["reset_at"]), period, now)
    return now - period


def breakdown_text(requests, window_label):
    total = sum(request["tokens"] for request in requests)
    if not total:
        return f"No Claude Code usage in the current {window_label} window yet."
    subagent = sum(request["tokens"] for request in requests if request["subagent"])
    kinds = [("Main conversation", total - subagent), ("Subagents", subagent)]

    lines = [f"Where your tokens went in the current {window_label} window · {format_tokens(total)} tokens counted", ""]
    lines += share_lines("By project", grouped_shares(requests, lambda r: r["project"]), total)
    lines += [""] + share_lines("By model", grouped_shares(requests, model_name), total, request_notes(requests, model_name))
    if any(request.get("effort") for request in requests):  # only when the logs record an effort level
        lines += [""] + share_lines(
            "By effort", grouped_shares(requests, effort_label), total, request_notes(requests, effort_label)
        )
    lines += [""] + share_lines("By who made the request", [row for row in kinds if row[1]], total)
    lines += ["", "These are shares of the tokens counted from your logs. Claude weighs tokens in ways the logs",
              "don't show, so they are not exact shares of your limit."]
    return "\n".join(lines)


def warned_path(home):
    return data_dir(home) / "warned.json"


def estimated_readings(home, now):
    """{window key: (percent, resets_at epoch)} from the synced estimate, for when there are no live numbers.

    Only windows whose estimate is anchored to a sync count. An estimate that was never synced
    can be well off, and a false alarm is worse than no heads-up.
    """
    config = read_json(config_path(home)) or {}
    found = {}
    records = None
    for key, window in WINDOWS.items():
        setting = config.get(key)
        if (key == "five_hour" and config.get("weekly_only")) or not isinstance(setting, dict):
            continue
        if not setting.get("limit") or not setting.get("reset_at"):
            continue
        try:
            anchor = parse_time(setting["reset_at"])
        except (ValueError, TypeError, AttributeError):
            continue
        period = timedelta(hours=window["hours"])
        start = window_start(anchor, period, now)
        if records is None:
            records = scan_usage(home / ".claude" / "projects", now - timedelta(days=PROJECTS_DAYS), scan_cache_path(home))
        percent, sync_age = estimate_percent(setting, records, start, now)
        if sync_age is not None:
            found[key] = (percent, int((start + period).timestamp()))
    return found


def warning_for(home, now):
    """A heads-up the first time a window crosses a warning level in a reset period, else None.

    Uses Claude's saved readings when they are fresh, which is fast enough to run on every
    prompt. Without them (the desktop app has no live numbers), it uses the synced estimate,
    which reads the log files' new bytes through the scan cache. Says nothing when it has neither.
    """
    live = read_live(home)
    readings = live.get("rate_limits") or {}
    saved_at = datetime.fromtimestamp(int(live.get("saved_at", 0)), timezone.utc)
    live_fresh = bool(readings) and now - saved_at <= timedelta(minutes=FRESH_MINUTES)

    state = read_json(warned_path(home)) or {}
    estimates = None
    messages = []
    for key, window in WINDOWS.items():
        reading = readings.get(window["live"]) if live_fresh else None
        if reading:
            percent, reset, estimated = float(reading["used_percentage"]), int(reading["resets_at"]), False
        else:
            if estimates is None:
                estimates = estimated_readings(home, now)
            if key not in estimates:
                continue
            (percent, reset), estimated = estimates[key], True
        level = max((threshold for threshold in WARN_LEVELS if percent >= threshold), default=0)
        previous = state.get(key) or {}
        already_told = previous.get("level", 0) if previous.get("reset") == reset else 0
        if level > already_told:
            state[key] = {"reset": reset, "level": level}
            resets_in = format_remaining(reset - now.timestamp())
            how = "is around" if estimated else "is at"
            note = " (estimate)" if estimated else ""
            messages.append(f"{window['label']} usage {how} {math.floor(percent)}%{note}, resetting in {resets_in}")
    if not messages:
        return None
    write_json(warned_path(home), state)
    return "token-bar: " + "; ".join(messages)


def hook_ran_path(home):
    return data_dir(home) / "hook_last_run.json"


def record_hook_run(home, now):
    """Notes that the hook ran, so `hook-check` can tell whether hooks fire in this setup. Never raises."""
    try:
        write_json(hook_ran_path(home), {"at": int(now.timestamp())})
    except OSError:
        pass


def hook_check_text(home, now):
    saved = read_json(hook_ran_path(home))
    at = saved.get("at") if isinstance(saved, dict) else None
    if not isinstance(at, (int, float)):
        return (
            "The heads-up hook hasn't run on this machine yet. It runs each time you send a message in "
            "Claude Code, so send one and check again. If it still says this, hooks aren't running in your setup."
        )
    return f"The heads-up hook last ran {format_age(now.timestamp() - at)} ago."


def warning_output(home, now):
    """The JSON a Claude Code hook prints to show a message to the user. Empty when there is nothing to say."""
    record_hook_run(home, now)
    if os.environ.get("TOKEN_BAR_NO_WARN"):
        return ""
    try:
        message = warning_for(home, now)
    except (OSError, ValueError, TypeError):
        return ""
    return json.dumps({"systemMessage": message}) if message else ""


def calibrate(home, key, percent_text, reset_text, now):
    """Sets a window's limit from the percent Claude shows. Without a reset time, keeps the saved one."""
    try:
        percent = float(percent_text.strip().rstrip("%"))
    except ValueError:
        raise ValueError(f"'{percent_text}' is not a percentage")
    if not 0 < percent <= 100:
        raise ValueError(f"'{percent_text}' must be above 0 and at most 100")

    window = WINDOWS[key]
    config = read_json(config_path(home)) or {}
    if reset_text:
        anchor = parse_reset(reset_text, now)
    else:
        saved = (config.get(key) or {}).get("reset_at")
        if not saved:
            raise ValueError(
                f"I don't know when your {window['label'].lower()} limit resets yet. "
                f"Give me the percent and the reset time, such as {percent_text.strip()} \"Sun 5:30 AM\"."
            )
        anchor = parse_time(saved)
    period = timedelta(hours=window["hours"])
    start = window_start(anchor, period, now)
    records = scan_usage(home / ".claude" / "projects", now - timedelta(days=PROJECTS_DAYS), scan_cache_path(home))
    used = sum(tokens for when, tokens in records if start <= when <= now)
    if used == 0:
        raise ValueError("no Claude usage in this window yet, so there is nothing to calibrate against. Use Claude a little, then try again.")

    limit = round(used / (percent / 100))
    config[key] = {
        "limit": limit,
        "reset_at": anchor.isoformat(),
        "synced": {"percent": percent, "at": now.isoformat()},
    }
    write_json(config_path(home), config)
    return f"Calibrated. Your {window['label'].lower()} limit is about {limit:,} tokens, resetting {local_time_text(anchor)}."


def set_weekly_only(home, enabled):
    """Remembers that the account has no 5-hour limit, so it is never asked about or shown."""
    config = read_json(config_path(home)) or {}
    if enabled:
        config["weekly_only"] = True
    else:
        config.pop("weekly_only", None)
    write_json(config_path(home), config)
    if enabled:
        return "Got it. token-bar will only track your weekly limit."
    return "token-bar will track both your 5-hour and weekly limits again."


def default_window(home):
    """The weekly window when the account has no 5-hour limit, otherwise the 5-hour window."""
    if (read_json(config_path(home)) or {}).get("weekly_only"):
        return "weekly"
    readings = read_live(home).get("rate_limits") or {}
    if readings.get(WINDOWS["weekly"]["live"]) and not readings.get(WINDOWS["five_hour"]["live"]):
        return "weekly"
    return "five_hour"


def level_color(percent):
    """Green below 70%, yellow from 70%, red from 90%."""
    for threshold, code in LEVEL_COLORS:
        if percent >= threshold:
            return code
    return LEVEL_COLORS[-1][1]


def statusline_text(model, readings, now, color):
    """The one-line status line: model, a bar for each window, and the next reset."""

    def paint(text, code):
        return f"{code}{text}{ANSI_RESET}" if color else text

    parts = [paint(model, ANSI_DIM)] if model else []
    resets = []
    for short, key in (("5h", "five_hour"), ("wk", "seven_day")):
        reading = readings.get(key)
        if not reading:
            continue
        percent = float(reading["used_percentage"])
        parts.append(paint(f"{short} {usage_bar(percent, STATUSLINE_BAR_WIDTH)} {math.floor(percent)}%", level_color(percent)))
        resets.append(datetime.fromtimestamp(int(reading["resets_at"]), timezone.utc))

    if not resets:
        return paint("token-bar: no readings yet", ANSI_DIM)
    upcoming = [at for at in resets if at > now]
    if upcoming:
        parts.append(paint(f"resets {format_remaining((min(upcoming) - now).total_seconds())}", ANSI_DIM))
    return paint(" · ", ANSI_DIM).join(parts)


def save_statusline_payload(home, payload_text, now, color=False):
    """Saves Claude's rate limits from a status line payload, and returns the status line text."""
    try:
        payload = json.loads(payload_text)
    except ValueError:
        payload = None
    payload = payload if isinstance(payload, dict) else {}
    rate_limits = payload.get("rate_limits")
    if rate_limits:
        try:
            write_json(live_path(home), {"rate_limits": rate_limits, "saved_at": int(now.timestamp())})
            append_history(home, rate_limits, now)
        except OSError as error:
            print(f"token-bar: could not save readings: {error}", file=sys.stderr)

    readings = (read_json(live_path(home)) or {}).get("rate_limits") or {}
    model = (payload.get("model") or {}).get("display_name") if isinstance(payload.get("model"), dict) else None
    return statusline_text(model, readings, now, color)


def shortcut_path(home):
    return home / ".claude" / "skills" / "token" / "SKILL.md"


def shortcut_hint(home):
    """A last line telling the skill the short /token command isn't set up. Empty once it is."""
    return "" if shortcut_path(home).exists() else "\nShort command: off"


SHORTCUT_SKILL = f"""---
name: token
description: Show your Claude usage against your limits (5-hour and weekly, or weekly only on Ultra), when the next whole percent lands, and what is using it up. Use when the user runs /token or asks how close they are to their Claude limit.
argument-hint: [status | verdict | budget | chart | all | breakdown | cost | sync | calibrate | statusline]
---

This is a shortcut for the token-bar plugin. Call the Skill tool with skill `token-bar:token` and args `$ARGUMENTS`, then follow that skill's instructions exactly.

If that skill isn't available, the token-bar plugin isn't installed. Tell the user to run these two commands in Claude Code, then try again:

    /plugin marketplace add 56steve/Token-Bar
    /plugin install token-bar --marketplace 56steve/Token-Bar

{SHORTCUT_MARKER}
"""


def install_shortcut(home):
    """Adds a personal /token skill, so the command works without the plugin's name in front.

    Plugin skills always carry the plugin name, but a skill in the user's own skills folder
    doesn't. The skill only hands off to the plugin's own skill, so it never goes out of date.
    A skill the user wrote themselves is never replaced.
    """
    target = shortcut_path(home)
    if target.exists():
        current = target.read_text(errors="ignore")
        if SHORTCUT_MARKER not in current:
            return "kept", 3
        if current == SHORTCUT_SKILL:
            return "already", 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SHORTCUT_SKILL)
    return "set", 0


def enable_statusline(home, script_path):
    """Adds the status line to Claude's settings. Never replaces a status line the user already has."""
    settings_file = home / ".claude" / "settings.json"
    settings = {}
    if settings_file.exists():
        settings = json.loads(settings_file.read_text())
    launcher = data_dir(home) / "statusline_launcher.py"
    command = f'python3 "{launcher}"'
    current = settings.get("statusLine")
    current_command = current.get("command", "") if isinstance(current, dict) else ""
    if current_command == command:
        return "already", 0
    ours = "token_bar.py" in current_command or "statusline_launcher.py" in current_command
    if current and not ours:
        return "kept", 3

    # The launcher lives in a stable place and finds the newest installed plugin each run,
    # so a plugin update can't leave the setting pointing at a removed folder.
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(script_path).parent / "statusline_launcher.py", launcher)
    settings["statusLine"] = {"type": "command", "command": command}
    write_json(settings_file, settings)
    return "set", 0


def main(argv, home, stdin_text=None, script_path=None):
    """Runs one command. Returns (text to print, exit code)."""
    parser = argparse.ArgumentParser(prog="token_bar.py")
    commands = parser.add_subparsers(dest="command")
    status = commands.add_parser("status")
    status.add_argument("section", nargs="?", default="summary", choices=[*SECTIONS, *SECTION_ALIASES])
    cal = commands.add_parser("calibrate")
    cal.add_argument("window", choices=sorted(WINDOWS))
    cal.add_argument("percent")
    cal.add_argument("reset", nargs="+")
    commands.add_parser("statusline")
    commands.add_parser("enable-statusline")
    commands.add_parser("warn")
    commands.add_parser("hook-check")
    commands.add_parser("install-shortcut")
    breakdown = commands.add_parser("breakdown")
    breakdown.add_argument("window", nargs="?", choices=sorted(WINDOWS))
    cost = commands.add_parser("cost")
    cost.add_argument("window", nargs="?", choices=sorted(WINDOWS))
    sync = commands.add_parser("sync")
    sync.add_argument("percent")
    sync.add_argument("window", nargs="?", choices=sorted(WINDOWS))
    weekly_only = commands.add_parser("weekly-only")
    weekly_only.add_argument("setting", nargs="?", choices=("on", "off"), default="on")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    try:
        if args.command in (None, "status"):
            return build_report(home, now, getattr(args, "section", "summary")), 0
        if args.command == "warn":
            return warning_output(home, now), 0
        if args.command == "hook-check":
            return hook_check_text(home, now), 0
        if args.command == "install-shortcut":
            result, code = install_shortcut(home)
            messages = {
                "set": "Added the short /token command. If it isn't listed yet, restart Claude Code.",
                "already": "The short /token command was already added.",
                "kept": "You already have your own /token skill, so it was left as it is.",
            }
            return messages[result], code
        if args.command == "sync":
            return calibrate(home, args.window or default_window(home), args.percent, None, now), 0
        if args.command == "weekly-only":
            return set_weekly_only(home, args.setting == "on"), 0
        if args.command in ("breakdown", "cost"):
            window = args.window or default_window(home)
            start = window_bounds(home, window, now)
            cutoff = now - timedelta(days=PROJECTS_DAYS)  # the same horizon as everywhere else, so the cache is shared
            everything = scan_requests(home / ".claude" / "projects", cutoff, scan_cache_path(home))
            requests = [r for r in everything if start <= r["when"] <= now]
            label = WINDOWS[window]["label"].lower()
            if args.command == "cost":
                return cost_text(requests, load_prices(home), label, prices_path(home)), 0
            return breakdown_text(requests, label), 0
        if args.command == "calibrate":
            return calibrate(home, args.window, args.percent, " ".join(args.reset), now), 0
        if args.command == "statusline":
            text = stdin_text if stdin_text is not None else sys.stdin.read()
            color = "NO_COLOR" not in os.environ  # https://no-color.org
            return save_statusline_payload(home, text, now, color=color), 0
        if args.command == "enable-statusline":
            script = script_path or Path(__file__).resolve()
            result, code = enable_statusline(home, script)
            messages = {
                "set": "Status line set. Run /token-bar:token once more to see your live numbers.",
                "already": "The status line was already set up.",
                "kept": (
                    "You already have a different status line, so it was left as it is.\n"
                    "To get live numbers too, add this line to your status line script, after it reads its input:\n"
                    f'  printf \'%s\' "$input" | python3 "{script}" statusline > /dev/null'
                ),
            }
            return messages[result], code
    except ValueError as error:
        return f"token-bar: {error}", 1
    return parser.format_help(), 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the bars need UTF-8, which some Windows consoles lack
    output, code = main(sys.argv[1:], Path.home())
    if output:  # the warn hook prints nothing when there is nothing to say
        print(output)
    sys.exit(code)
