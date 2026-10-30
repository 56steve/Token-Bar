import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import statusline_launcher  # noqa: E402
import token_bar  # noqa: E402


def request_line(request_id, when, input_tokens, output_tokens=0):
    return json.dumps({
        "type": "assistant",
        "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "requestId": request_id,
        "message": {"usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}},
    })


class WindowTests(unittest.TestCase):
    def test_window_starts_at_the_last_fixed_reset(self):
        anchor = datetime(2026, 10, 29, 14, 20, tzinfo=timezone.utc)
        period = timedelta(hours=5)

        self.assertEqual(
            token_bar.window_start(anchor, period, datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)),
            datetime(2026, 10, 29, 9, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(
            token_bar.window_start(anchor, period, datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)),
            anchor,
        )

    def test_parses_a_daily_reset_as_the_next_local_occurrence(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

        reset = token_bar.parse_reset("2:20 PM", now)

        self.assertGreater(reset, now)
        self.assertLessEqual(reset - now, timedelta(hours=24))
        local = reset.astimezone()
        self.assertEqual((local.hour, local.minute), (14, 20))

    def test_parses_a_weekly_reset_with_a_weekday(self):
        now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

        reset = token_bar.parse_reset("Sun 5:30 AM", now)

        local = reset.astimezone()
        self.assertEqual(local.weekday(), 6)
        self.assertEqual((local.hour, local.minute), (5, 30))
        self.assertLessEqual(reset - now, timedelta(days=8))

    def test_rejects_text_that_is_not_a_time(self):
        with self.assertRaises(ValueError):
            token_bar.parse_reset("sometime", datetime.now(timezone.utc))


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.projects = self.home / ".claude" / "projects" / "demo"
        self.projects.mkdir(parents=True)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_each_request_once_when_streamed_repeatedly(self):
        lines = [
            request_line("req_a", self.now - timedelta(minutes=20), 10, 5),
            request_line("req_a", self.now - timedelta(minutes=19), 10, 5),
            request_line("req_b", self.now - timedelta(minutes=10), 3, 4),
        ]
        (self.projects / "session.jsonl").write_text("\n".join(lines))

        records = token_bar.scan_usage(self.home / ".claude" / "projects", self.now - timedelta(hours=1))

        self.assertEqual(sorted(tokens for _, tokens in records), [7, 15])

    def test_skips_requests_before_the_cutoff(self):
        (self.projects / "old.jsonl").write_text(request_line("req_old", self.now - timedelta(days=30), 100))

        records = token_bar.scan_usage(self.home / ".claude" / "projects", self.now - timedelta(days=7))

        self.assertEqual(records, [])

    def test_status_says_setup_is_needed_on_a_fresh_install(self):
        text = token_bar.build_report(self.home, self.now)

        self.assertTrue(text.startswith("Setup needed."))
        self.assertIn("five_hour:", text)
        self.assertIn("weekly:", text)
        self.assertIn("Live numbers: off", text)

    def test_calibrate_then_status_matches_the_percent_entered(self):
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=5), 530_000))

        message = token_bar.calibrate(self.home, "five_hour", "53", "2:20 PM", self.now)
        text = token_bar.build_report(self.home, self.now)

        self.assertIn("Calibrated. Your 5-hour limit is about 1,000,000 tokens", message)
        self.assertIn("█" * 11 + "░" * 9 + "  53% → 54% in under a minute", text)
        self.assertIn("Source: estimate from your calibration", text)
        config = json.loads((self.home / ".claude" / "token-bar" / "config.json").read_text())
        self.assertEqual(config["five_hour"]["limit"], 1_000_000)

    def test_calibrate_refuses_when_there_is_no_usage_yet(self):
        with self.assertRaises(ValueError):
            token_bar.calibrate(self.home, "five_hour", "40", "2:20 PM", self.now)

    def test_claude_code_reading_is_shown_with_its_source(self):
        reset = int((self.now + timedelta(hours=2)).timestamp())
        live = token_bar.live_path(self.home)
        token_bar.write_json(live, {
            "rate_limits": {"five_hour": {"used_percentage": 21.4, "resets_at": reset}},
            "saved_at": int(self.now.timestamp()),
        })

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("5-hour  " + "█" * 4 + "░" * 16 + "  21%", text)
        self.assertIn("resets in 2h 0m", text)
        self.assertIn("Source: Claude Code, saved 0 min ago", text)
        self.assertIn("Live numbers: on", text)

    def test_readings_saved_by_the_command_line_tool_are_used_too(self):
        reset = int((self.now + timedelta(hours=3)).timestamp())
        cli_file = token_bar.other_live_paths(self.home)[0]
        token_bar.write_json(cli_file, {
            "rate_limits": {"seven_day": {"used_percentage": 78.2, "resets_at": reset}},
            "saved_at": int(self.now.timestamp()),
        })

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("Weekly  ", text)
        self.assertIn("78%", text)
        self.assertIn("Live numbers: on", text)

    def save_readings(self, readings, resets):
        """Saves (minutes_ago, percent) readings the way the status line would, oldest first."""
        for minutes_ago, percent in readings:
            when = self.now - timedelta(minutes=minutes_ago)
            limits = {"five_hour": {"used_percentage": percent, "resets_at": int(resets.timestamp())}}
            token_bar.save_statusline_payload(self.home, json.dumps({"rate_limits": limits}), when)

    def test_next_point_comes_from_the_percent_history_with_no_calibration(self):
        resets = self.now + timedelta(hours=3)
        # The percent rose by 1 every 10 minutes and reached 23 two minutes ago.
        self.save_readings([(32, 20.0), (22, 21.0), (12, 22.0), (2, 23.0)], resets)

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("23% → 24% in ~8 min", text)

    def test_next_point_works_with_fractional_percents(self):
        resets = self.now + timedelta(hours=3)
        self.save_readings([(24, 20.2), (4, 20.5)], resets)

        text = token_bar.build_report(self.home, self.now)

        # 0.3 points in 20 minutes, so 0.5 points remain: about 33 minutes, minus the 4 already passed.
        self.assertIn("20% → 21% in ~29 min", text)

    def test_says_when_the_percent_has_not_moved(self):
        resets = self.now + timedelta(hours=3)
        self.save_readings([(20, 22.0), (2, 22.0)], resets)

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("22% · no recent activity", text)

    def test_a_single_reading_shows_no_estimate_and_no_idle_message(self):
        resets = self.now + timedelta(hours=3)
        self.save_readings([(2, 22.0)], resets)

        text = token_bar.build_report(self.home, self.now)

        self.assertNotIn("→", text)
        self.assertNotIn("no recent activity", text)

    def test_an_old_reading_gives_no_estimate(self):
        resets = self.now + timedelta(hours=3)
        self.save_readings([(70, 20.0), (50, 22.0), (40, 23.0)], resets)

        text = token_bar.build_report(self.home, self.now)

        self.assertNotIn("→", text)

    def test_unchanged_readings_within_a_minute_are_not_stored_twice(self):
        resets = self.now + timedelta(hours=3)
        self.save_readings([(5, 30.0), (5, 30.0)], resets)

        history = json.loads(token_bar.history_path(self.home).read_text())

        self.assertEqual(len(history), 1)

    def test_old_history_is_dropped(self):
        resets = self.now + timedelta(hours=3)
        self.save_readings([(4 * 60, 10.0), (1, 30.0)], resets)

        history = json.loads(token_bar.history_path(self.home).read_text())

        self.assertEqual(len(history), 1)

    def test_the_bar_fills_in_proportion(self):
        self.assertEqual(token_bar.usage_bar(0), "░" * 20)
        self.assertEqual(token_bar.usage_bar(100), "█" * 20)
        self.assertEqual(token_bar.usage_bar(21), "█" * 4 + "░" * 16)
        self.assertEqual(token_bar.usage_bar(150), "█" * 20)


class StatuslineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saves_claude_readings_from_the_status_line_payload(self):
        payload = json.dumps({"rate_limits": {"seven_day": {"used_percentage": 78.2, "resets_at": 1793475000}}})

        summary = token_bar.save_statusline_payload(self.home, payload, self.now)

        self.assertIn("wk " + "█" * 8 + "░" * 2 + " 78%", summary)
        saved = json.loads(token_bar.live_path(self.home).read_text())
        self.assertEqual(saved["saved_at"], int(self.now.timestamp()))

    def test_payload_without_rate_limits_saves_nothing(self):
        summary = token_bar.save_statusline_payload(self.home, json.dumps({"model": {}}), self.now)

        self.assertEqual(summary, "token-bar: no readings yet")
        self.assertFalse(token_bar.live_path(self.home).exists())

    def test_enable_sets_the_status_line_and_keeps_other_settings(self):
        settings = self.home / ".claude" / "settings.json"
        token_bar.write_json(settings, {"theme": "dark"})

        result, code = token_bar.enable_statusline(self.home, Path(token_bar.__file__).resolve())

        self.assertEqual((result, code), ("set", 0))
        saved = json.loads(settings.read_text())
        launcher = self.home / ".claude" / "token-bar" / "statusline_launcher.py"
        self.assertEqual(saved["theme"], "dark")
        self.assertEqual(saved["statusLine"]["command"], f'python3 "{launcher}"')
        self.assertTrue(launcher.exists())

    def test_enable_replaces_an_older_token_bar_command_with_the_launcher(self):
        settings = self.home / ".claude" / "settings.json"
        old = 'python3 "/old/cache/0.1.0/scripts/token_bar.py" statusline'
        token_bar.write_json(settings, {"statusLine": {"type": "command", "command": old}})

        result, code = token_bar.enable_statusline(self.home, Path(token_bar.__file__).resolve())

        self.assertEqual((result, code), ("set", 0))
        self.assertIn("statusline_launcher.py", json.loads(settings.read_text())["statusLine"]["command"])

    def test_enable_twice_says_it_is_already_set_up(self):
        script = Path(token_bar.__file__).resolve()
        token_bar.enable_statusline(self.home, script)

        self.assertEqual(token_bar.enable_statusline(self.home, script), ("already", 0))

    def test_enable_leaves_a_different_status_line_alone(self):
        settings = self.home / ".claude" / "settings.json"
        token_bar.write_json(settings, {"statusLine": {"type": "command", "command": "bash mine.sh"}})

        result, code = token_bar.enable_statusline(self.home, Path("/opt/token_bar.py"))

        self.assertEqual((result, code), ("kept", 3))
        self.assertEqual(json.loads(settings.read_text())["statusLine"]["command"], "bash mine.sh")

    def test_a_kept_status_line_comes_with_the_line_to_add(self):
        settings = self.home / ".claude" / "settings.json"
        token_bar.write_json(settings, {"statusLine": {"type": "command", "command": "bash mine.sh"}})

        text, code = token_bar.main(["enable-statusline"], self.home, script_path=Path("/opt/token_bar.py"))

        self.assertEqual(code, 3)
        self.assertIn("left as it is", text)
        self.assertIn('python3 "/opt/token_bar.py" statusline', text)


class LauncherTests(unittest.TestCase):
    def test_picks_the_highest_version_numerically(self):
        paths = [
            "/h/.claude/plugins/cache/token-bar/token-bar/0.1.9/scripts/token_bar.py",
            "/h/.claude/plugins/cache/token-bar/token-bar/0.1.10/scripts/token_bar.py",
            "/h/.claude/plugins/cache/token-bar/token-bar/0.1.2/scripts/token_bar.py",
        ]

        self.assertTrue(statusline_launcher.newest(paths).endswith("0.1.10/scripts/token_bar.py"))

    def test_no_installed_plugin_gives_none(self):
        self.assertIsNone(statusline_launcher.newest([]))


class StatuslineDisplayTests(unittest.TestCase):
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    GREEN, YELLOW, RED, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[0m"

    def readings(self, five=None, week=None, five_resets=None):
        out = {}
        if five is not None:
            resets = five_resets or self.now + timedelta(hours=3, minutes=10)
            out["five_hour"] = {"used_percentage": five, "resets_at": int(resets.timestamp())}
        if week is not None:
            out["seven_day"] = {"used_percentage": week, "resets_at": int((self.now + timedelta(days=2)).timestamp())}
        return out

    def test_plain_text_without_color(self):
        text = token_bar.statusline_text("Opus", self.readings(21, 40), self.now, color=False)

        self.assertEqual(text, "Opus · 5h ██░░░░░░░░ 21% · wk ████░░░░░░ 40% · resets 3h 10m")

    def test_color_follows_the_level(self):
        low = token_bar.statusline_text(None, self.readings(21), self.now, color=True)
        mid = token_bar.statusline_text(None, self.readings(78), self.now, color=True)
        high = token_bar.statusline_text(None, self.readings(94), self.now, color=True)

        self.assertIn(self.GREEN + "5h", low)
        self.assertIn(self.YELLOW + "5h", mid)
        self.assertIn(self.RED + "5h", high)
        self.assertIn(self.RESET, high)

    def test_each_window_is_colored_on_its_own(self):
        text = token_bar.statusline_text(None, self.readings(21, 83), self.now, color=True)

        self.assertIn(self.GREEN + "5h", text)
        self.assertIn(self.YELLOW + "wk", text)

    def test_color_thresholds_sit_at_70_and_90(self):
        self.assertEqual(token_bar.level_color(0), self.GREEN)
        self.assertEqual(token_bar.level_color(69.9), self.GREEN)
        self.assertEqual(token_bar.level_color(70), self.YELLOW)
        self.assertEqual(token_bar.level_color(89.9), self.YELLOW)
        self.assertEqual(token_bar.level_color(90), self.RED)
        self.assertEqual(token_bar.level_color(100), self.RED)

    def test_no_color_codes_when_color_is_off(self):
        text = token_bar.statusline_text("Opus", self.readings(94, 80), self.now, color=False)

        self.assertNotIn("\033", text)

    def test_shows_only_the_windows_claude_reported(self):
        text = token_bar.statusline_text(None, self.readings(week=55), self.now, color=False)

        self.assertTrue(text.startswith("wk "))
        self.assertNotIn("5h", text)

    def test_says_so_when_there_are_no_readings(self):
        self.assertEqual(token_bar.statusline_text(None, {}, self.now, color=False), "token-bar: no readings yet")

    def test_skips_the_reset_for_a_window_that_has_already_reset(self):
        past = self.now - timedelta(minutes=5)

        text = token_bar.statusline_text(None, self.readings(five=50, five_resets=past), self.now, color=False)

        self.assertNotIn("resets", text)

    def test_the_command_colors_unless_no_color_is_set(self):
        import os
        from unittest import mock

        payload = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 94, "resets_at": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())}}})
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NO_COLOR", None)
                colored, _ = token_bar.main(["statusline"], home, stdin_text=payload)
            with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
                plain, _ = token_bar.main(["statusline"], home, stdin_text=payload)

        self.assertIn("\033[31m", colored)
        self.assertNotIn("\033", plain)


if __name__ == "__main__":
    unittest.main()
