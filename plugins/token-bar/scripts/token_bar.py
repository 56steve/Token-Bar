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
WARN_LEVELS = (80, 90)
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


def scan_requests(projects, cutoff):
    """Each request since `cutoff`, with its project, model, and whether a subagent made it.

    Claude Code writes the same request several times as it streams, so each is counted once.
    """
    if not projects.is_dir():
        return []
    by_request = {}
    for path in projects.rglob("*.jsonl"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                continue
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            usage = (entry.get("message") or {}).get("usage")
            request_id = entry.get("requestId")
            stamp = entry.get("timestamp")
            if not usage or not request_id or not stamp:
                continue
            try:
                when = parse_time(stamp)
            except ValueError:
                continue
            if when < cutoff:
                continue
            tokens = tokens_of(usage)
            previous = by_request.get(request_id)
            if previous is None or previous["tokens"] < tokens:
                by_request[request_id] = {
                    "when": when,
                    "tokens": tokens,
                    "project": project_name(entry, path),
                    "model": (entry.get("message") or {}).get("model"),
                    "subagent": bool(entry.get("isSidechain")),
                }
    return list(by_request.values())


def scan_usage(projects, cutoff):
    """(timestamp, tokens) for each request since `cutoff`."""
    return [(request["when"], request["tokens"]) for request in scan_requests(projects, cutoff)]


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


def render_block(label, width, percent, nxt, minutes, idle, resets_in, projection=None):
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
    return "\n".join(lines)


def build_report(home, now):
    config = read_json(config_path(home)) or {}
    live = read_live(home)
    readings = live.get("rate_limits") or {}
    records = None

    entries, sources, missing = [], {}, []
    for key, window in WINDOWS.items():
        setting = config.get(key)
        reading = readings.get(window["live"])
        if not setting and not reading:
            missing.append(key)
            continue
        if records is None:
            records = scan_usage(home / ".claude" / "projects", now - timedelta(days=PROJECTS_DAYS))

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
            used = sum(tokens for when, tokens in records if start <= when <= now)
            percent = used / limit * 100
            resets_at = start + period if anchor else None
            sources[window["label"]] = "estimate from your calibration"
            nxt, minutes = next_point(percent, limit, tokens_per_minute)
            idle = nxt is not None and minutes is None
            rate = tokens_per_minute / limit * 100 if tokens_per_minute > 0 else None

        resets_in = format_remaining((resets_at - now).total_seconds()) if resets_at else None
        minutes_to_reset = (resets_at - now).total_seconds() / 60 if resets_at else None
        projection = projection_text(percent, rate, minutes_to_reset)
        entries.append((window["label"], percent, nxt, minutes, idle, resets_in, projection))

    live_on = "on" if readings else "off"
    width = max((len(entry[0]) for entry in entries), default=0)
    blocks = [render_block(label, width, *rest) for label, *rest in entries]
    if not blocks:
        lines = ["Setup needed. Ask the user for each of these, then run calibrate:"]
        for key in missing:
            label = WINDOWS[key]["label"].lower()
            lines.append(f"  {key}: the {label} percent Claude shows now, and when it resets")
        lines.append(f"Live numbers: {live_on}")
        return "\n".join(lines)

    text = "\n\n".join(blocks)
    distinct = set(sources.values())
    if len(distinct) == 1:
        text += f"\n\nSource: {distinct.pop()}"
    else:
        text += "\n\nSource: " + "; ".join(f"{name}: {how}" for name, how in sources.items())
    if missing:
        names = ", ".join(WINDOWS[key]["label"] for key in missing)
        text += f"\n\nNot set up yet: {names}. Ask the user for its percent and reset time to add it."
    if not records and not readings:
        text += f"\n\nNo Claude Code usage found in the last {PROJECTS_DAYS} days."
    return f"{text}\nLive numbers: {live_on}"


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


def share_lines(title, rows, total):
    name_width = 26
    lines = [title]
    for name, tokens in rows:
        share = round(tokens / total * 100)
        shown = name if len(name) <= name_width - 1 else name[: name_width - 2] + "…"
        lines.append(f"  {shown:<{name_width}}{usage_bar(share, 12)}  {share:>3}%")
    return lines


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
    lines += [""] + share_lines("By model", grouped_shares(requests, lambda r: model_label(r["model"])), total)
    lines += [""] + share_lines("By who made the request", [row for row in kinds if row[1]], total)
    lines += ["", "These are shares of the tokens counted from your logs. Claude weighs tokens in ways the logs",
              "don't show, so they are not exact shares of your limit."]
    return "\n".join(lines)


def warned_path(home):
    return data_dir(home) / "warned.json"


def warning_for(home, now):
    """A heads-up the first time a window crosses a warning level in a reset period, else None.

    Uses only Claude's saved readings, so it is fast enough to run on every prompt, and it
    says nothing when the readings are old.
    """
    live = read_live(home)
    readings = live.get("rate_limits") or {}
    saved_at = datetime.fromtimestamp(int(live.get("saved_at", 0)), timezone.utc)
    if not readings or now - saved_at > timedelta(minutes=FRESH_MINUTES):
        return None

    state = read_json(warned_path(home)) or {}
    messages = []
    for key, window in WINDOWS.items():
        reading = readings.get(window["live"])
        if not reading:
            continue
        percent = float(reading["used_percentage"])
        reset = int(reading["resets_at"])
        level = max((threshold for threshold in WARN_LEVELS if percent >= threshold), default=0)
        previous = state.get(key) or {}
        already_told = previous.get("level", 0) if previous.get("reset") == reset else 0
        if level > already_told:
            state[key] = {"reset": reset, "level": level}
            resets_in = format_remaining(reset - now.timestamp())
            messages.append(f"{window['label']} usage is at {math.floor(percent)}%, resetting in {resets_in}")
    if not messages:
        return None
    write_json(warned_path(home), state)
    return "token-bar: " + "; ".join(messages)


def warning_output(home, now):
    """The JSON a Claude Code hook prints to show a message to the user. Empty when there is nothing to say."""
    if os.environ.get("TOKEN_BAR_NO_WARN"):
        return ""
    try:
        message = warning_for(home, now)
    except (OSError, ValueError, TypeError):
        return ""
    return json.dumps({"systemMessage": message}) if message else ""


def calibrate(home, key, percent_text, reset_text, now):
    try:
        percent = float(percent_text.strip().rstrip("%"))
    except ValueError:
        raise ValueError(f"'{percent_text}' is not a percentage")
    if not 0 < percent <= 100:
        raise ValueError(f"'{percent_text}' must be above 0 and at most 100")

    window = WINDOWS[key]
    anchor = parse_reset(reset_text, now)
    period = timedelta(hours=window["hours"])
    start = window_start(anchor, period, now)
    records = scan_usage(home / ".claude" / "projects", now - timedelta(days=PROJECTS_DAYS))
    used = sum(tokens for when, tokens in records if start <= when <= now)
    if used == 0:
        raise ValueError("no Claude usage in this window yet, so there is nothing to calibrate against. Use Claude a little, then try again.")

    limit = round(used / (percent / 100))
    config = read_json(config_path(home)) or {}
    config[key] = {"limit": limit, "reset_at": anchor.isoformat()}
    write_json(config_path(home), config)
    local_reset = anchor.astimezone().strftime("%a %I:%M %p").lstrip("0")
    return f"Calibrated. Your {window['label'].lower()} limit is about {limit:,} tokens, resetting {local_reset}."


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
    commands.add_parser("status")
    cal = commands.add_parser("calibrate")
    cal.add_argument("window", choices=sorted(WINDOWS))
    cal.add_argument("percent")
    cal.add_argument("reset", nargs="+")
    commands.add_parser("statusline")
    commands.add_parser("enable-statusline")
    commands.add_parser("warn")
    breakdown = commands.add_parser("breakdown")
    breakdown.add_argument("window", nargs="?", choices=sorted(WINDOWS), default="five_hour")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    try:
        if args.command in (None, "status"):
            return build_report(home, now), 0
        if args.command == "warn":
            return warning_output(home, now), 0
        if args.command == "breakdown":
            start = window_bounds(home, args.window, now)
            requests = [r for r in scan_requests(home / ".claude" / "projects", start) if r["when"] <= now]
            return breakdown_text(requests, WINDOWS[args.window]["label"].lower()), 0
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
