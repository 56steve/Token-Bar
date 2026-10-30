import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "token-bar" / "scripts"))

import codex_usage  # noqa: E402


def token_count_line(when, primary=None, secondary=None, plan="plus"):
    return json.dumps({
        "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": 1}},
            "rate_limits": {"primary": primary, "secondary": secondary, "plan_type": plan},
        },
    })


def window(percent, minutes, resets_at):
    return {"used_percent": percent, "window_minutes": minutes, "resets_at": int(resets_at.timestamp())}


class CodexUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.sessions = self.home / ".codex" / "sessions" / "2026" / "10" / "29"
        self.sessions.mkdir(parents=True)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def tearDown(self):
        self.tmp.cleanup()

    def write_session(self, name, lines):
        path = self.sessions / name
        path.write_text("\n".join(lines))
        return path

    def test_labels_known_and_unknown_windows(self):
        self.assertEqual(codex_usage.window_label(300), "5-hour")
        self.assertEqual(codex_usage.window_label(10080), "Weekly")
        self.assertEqual(codex_usage.window_label(43200), "Monthly")
        self.assertEqual(codex_usage.window_label(120), "2-hour")
        self.assertEqual(codex_usage.window_label(2880), "2-day")

    def test_shows_both_windows_with_resets_and_the_source(self):
        primary = window(21.4, 300, self.now + timedelta(hours=2))
        secondary = window(78.2, 10080, self.now + timedelta(days=2, hours=16))
        self.write_session("rollout-a.jsonl", [token_count_line(self.now - timedelta(minutes=5), primary, secondary)])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("5-hour usage   21%", text)
        self.assertIn("Weekly usage   78%", text)
        self.assertIn("Resets       in 2d 16h", text)
        self.assertIn("Source: Codex session log, saved 5 min ago. Plan: plus.", text)

    def test_uses_the_newest_reading_in_a_session_file(self):
        old = window(10.0, 300, self.now + timedelta(hours=4))
        new = window(40.0, 300, self.now + timedelta(hours=3))
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(hours=1), old),
            token_count_line(self.now - timedelta(minutes=1), new),
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("5-hour usage   40%", text)

    def test_ignores_lines_that_are_not_token_counts_or_have_no_windows(self):
        self.write_session("rollout-a.jsonl", [
            json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}),
            "not json at all",
            token_count_line(self.now, None, None),
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertEqual(text, "No Codex usage found yet. Use Codex once, then run this again.")

    def test_skips_a_newer_entry_without_windows_and_uses_the_last_real_reading(self):
        real = window(33.0, 300, self.now + timedelta(hours=3))
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=30), real),
            token_count_line(self.now - timedelta(minutes=1), None, None),
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("5-hour usage   33%", text)
        self.assertIn("saved 30 min ago", text)

    def test_says_when_a_window_has_reset_since_the_reading(self):
        primary = window(90.0, 300, self.now - timedelta(minutes=10))
        self.write_session("rollout-a.jsonl", [token_count_line(self.now - timedelta(hours=6), primary)])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("Reset since this reading", text)

    def test_estimates_the_next_point_from_whole_number_readings(self):
        resets = self.now + timedelta(hours=3)
        # The percent rose by 1 every 10 minutes and reached 23 two minutes ago.
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=minutes), window(percent, 300, resets))
            for minutes, percent in [(32, 20.0), (22, 21.0), (12, 22.0), (2, 23.0)]
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("Next point   24% in about 8 min", text)

    def test_estimates_the_next_point_from_fractional_readings(self):
        resets = self.now + timedelta(hours=3)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=24), window(20.2, 300, resets)),
            token_count_line(self.now - timedelta(minutes=4), window(20.5, 300, resets)),
        ])

        text = codex_usage.main(self.home, self.now)

        # 0.3 points in 20 minutes, so 0.5 points remain: about 33 minutes, minus the 4 already passed.
        self.assertIn("Next point   21% in about 29 min", text)

    def test_says_so_when_the_percent_has_not_moved(self):
        resets = self.now + timedelta(hours=3)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=20), window(22.0, 300, resets)),
            token_count_line(self.now - timedelta(minutes=2), window(22.0, 300, resets)),
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("Next point   23%, no recent activity to estimate from", text)

    def test_gives_no_estimate_from_an_old_reading(self):
        resets = self.now + timedelta(hours=3)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=minutes), window(percent, 300, resets))
            for minutes, percent in [(70, 20.0), (50, 22.0), (40, 23.0)]
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertNotIn("Next point", text)

    def test_readings_from_an_earlier_reset_period_are_not_mixed_in(self):
        old_reset = self.now - timedelta(hours=1)
        new_reset = self.now + timedelta(hours=4)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=30), window(95.0, 300, old_reset)),
            token_count_line(self.now - timedelta(minutes=2), window(2.0, 300, new_reset)),
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("5-hour usage   2%", text)
        self.assertIn("Next point   3%, no recent activity to estimate from", text)

    def test_no_sessions_gives_a_clear_message(self):
        empty_home = self.home / "empty"
        empty_home.mkdir()

        self.assertEqual(codex_usage.main(empty_home, self.now), "No Codex usage found yet. Use Codex once, then run this again.")


if __name__ == "__main__":
    unittest.main()
