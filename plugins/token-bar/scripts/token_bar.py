#!/usr/bin/env python3
"""token-bar: see how close you are to your Claude usage limits.

Standard library only. Used by the /token skill and by the Claude Code status line.
Data lives in ~/.claude/token-bar/, next to Claude's own session logs.
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PACE_MINUTES = 30
PROJECTS_DAYS = 7
ROUGH_BELOW_PERCENT = 10
BAR_WIDTH = 20
FRESH_MINUTES = 30
LOOKBACK_MINUTES = 60
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


def scan_usage(projects, cutoff):
    """(timestamp, tokens) for each request since `cutoff`. Streamed repeats are counted once."""
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
            if previous is None or previous[1] < tokens:
                by_request[request_id] = (when, tokens)
    return list(by_request.values())


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


def minutes_to_next_percent(series, now):
    """Minutes until the percent next goes up, from how fast it moved in the last hour.

    Returns None when the reading is old or the percent hasn't moved recently.
    """
    if len(series) < 2:
        return None
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
    since_reading = (now - latest_when).total_seconds() / 60
    if latest_percent == math.floor(latest_percent):
        # Whole-number readings: the next point arrives one step after the last increase.
        last_increase = next(when for when, percent in recent if percent == latest_percent)
        return max(1 / rate - (now - last_increase).total_seconds() / 60, 0)
    return max((math.floor(latest_percent) + 1 - latest_percent) / rate - since_reading, 0)


def usage_bar(percent):
    filled = max(0, min(BAR_WIDTH, round(percent / 100 * BAR_WIDTH)))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def estimate_text(minutes):
    if minutes < 1:
        return "under a minute"
    return "~" + format_remaining(minutes * 60)


def render_block(label, width, percent, nxt, minutes, idle, resets_in):
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
        else:
            used = sum(tokens for when, tokens in records if start <= when <= now)
            percent = used / limit * 100
            resets_at = start + period if anchor else None
            sources[window["label"]] = "estimate from your calibration"
            nxt, minutes = next_point(percent, limit, tokens_per_minute)
            idle = nxt is not None and minutes is None

        resets_in = format_remaining((resets_at - now).total_seconds()) if resets_at else None
        entries.append((window["label"], percent, nxt, minutes, idle, resets_in))

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


def save_statusline_payload(home, payload_text, now):
    """Saves Claude's rate limits from a status line payload, and returns the one-line summary."""
    try:
        payload = json.loads(payload_text)
    except ValueError:
        payload = None
    rate_limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if rate_limits:
        try:
            write_json(live_path(home), {"rate_limits": rate_limits, "saved_at": int(now.timestamp())})
            append_history(home, rate_limits, now)
        except OSError as error:
            print(f"token-bar: could not save readings: {error}", file=sys.stderr)

    readings = (read_json(live_path(home)) or {}).get("rate_limits") or {}
    parts = []
    for key, window in WINDOWS.items():
        reading = readings.get(window["live"])
        if reading:
            parts.append(f"{window['label'].lower()} {math.floor(float(reading['used_percentage']))}%")
    return " · ".join(parts) or "token-bar: no readings yet"


def enable_statusline(home, script_path):
    """Adds the status line to Claude's settings. Never replaces a status line the user already has."""
    settings_file = home / ".claude" / "settings.json"
    settings = {}
    if settings_file.exists():
        settings = json.loads(settings_file.read_text())
    command = f'python3 "{script_path}" statusline'
    current = settings.get("statusLine")
    if current and current.get("command") == command:
        return "already", 0
    if current:
        return "kept", 3
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
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    try:
        if args.command in (None, "status"):
            return build_report(home, now), 0
        if args.command == "calibrate":
            return calibrate(home, args.window, args.percent, " ".join(args.reset), now), 0
        if args.command == "statusline":
            text = stdin_text if stdin_text is not None else sys.stdin.read()
            return save_statusline_payload(home, text, now), 0
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
    print(output)
    sys.exit(code)
