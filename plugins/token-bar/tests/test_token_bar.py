import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

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
        self.assertIn("5-hour usage   53%", text)
        self.assertIn("Source       estimate from your calibration", text)
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

        self.assertIn("5-hour usage   21%", text)
        self.assertIn("Resets       in 2h 0m", text)
        self.assertIn("Source       Claude Code, saved 0 min ago", text)
        self.assertIn("Live numbers: on", text)

    def test_readings_saved_by_the_command_line_tool_are_used_too(self):
        reset = int((self.now + timedelta(hours=3)).timestamp())
        cli_file = token_bar.other_live_paths(self.home)[0]
        token_bar.write_json(cli_file, {
            "rate_limits": {"seven_day": {"used_percentage": 78.2, "resets_at": reset}},
            "saved_at": int(self.now.timestamp()),
        })

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("Weekly usage   78%", text)
        self.assertIn("Live numbers: on", text)


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

        self.assertEqual(summary, "weekly 78%")
        saved = json.loads(token_bar.live_path(self.home).read_text())
        self.assertEqual(saved["saved_at"], int(self.now.timestamp()))

    def test_payload_without_rate_limits_saves_nothing(self):
        summary = token_bar.save_statusline_payload(self.home, json.dumps({"model": {}}), self.now)

        self.assertEqual(summary, "token-bar: no readings yet")
        self.assertFalse(token_bar.live_path(self.home).exists())

    def test_enable_sets_the_status_line_and_keeps_other_settings(self):
        settings = self.home / ".claude" / "settings.json"
        token_bar.write_json(settings, {"theme": "dark"})

        result, code = token_bar.enable_statusline(self.home, Path("/opt/token_bar.py"))

        self.assertEqual((result, code), ("set", 0))
        saved = json.loads(settings.read_text())
        self.assertEqual(saved["theme"], "dark")
        self.assertEqual(saved["statusLine"]["command"], 'python3 "/opt/token_bar.py" statusline')

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


if __name__ == "__main__":
    unittest.main()
