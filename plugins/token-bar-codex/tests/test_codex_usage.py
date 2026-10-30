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


def usage_line(when, total, primary):
    return json.dumps({
        "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": {"total_tokens": total}},
            "rate_limits": {"primary": primary, "secondary": None, "plan_type": "plus"},
        },
    })


def meta_lines(cwd, model):
    return [
        json.dumps({"type": "session_meta", "payload": {"cwd": cwd}}),
        json.dumps({"type": "turn_context", "payload": {"cwd": cwd, "model": model}}),
    ]


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

        self.assertIn("5-hour  " + "█" * 4 + "░" * 16 + "  21%", text)
        self.assertIn("Weekly  " + "█" * 16 + "░" * 4 + "  78%", text)
        self.assertIn("resets in 2d 16h", text)
        self.assertIn("Source: Codex session log, saved 5 min ago. Plan: plus.", text)

    def test_uses_the_newest_reading_in_a_session_file(self):
        old = window(10.0, 300, self.now + timedelta(hours=4))
        new = window(40.0, 300, self.now + timedelta(hours=3))
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(hours=1), old),
            token_count_line(self.now - timedelta(minutes=1), new),
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("█" * 8 + "░" * 12 + "  40%", text)

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

        self.assertIn("█" * 7 + "░" * 13 + "  33%", text)
        self.assertIn("saved 30 min ago", text)

    def test_says_when_a_window_has_reset_since_the_reading(self):
        primary = window(90.0, 300, self.now - timedelta(minutes=10))
        self.write_session("rollout-a.jsonl", [token_count_line(self.now - timedelta(hours=6), primary)])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("reset since this reading", text)

    def test_estimates_the_next_point_from_whole_number_readings(self):
        resets = self.now + timedelta(hours=3)
        # The percent rose by 1 every 10 minutes and reached 23 two minutes ago.
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=minutes), window(percent, 300, resets))
            for minutes, percent in [(32, 20.0), (22, 21.0), (12, 22.0), (2, 23.0)]
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("23% → 24% in ~8 min", text)

    def test_estimates_the_next_point_from_fractional_readings(self):
        resets = self.now + timedelta(hours=3)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=24), window(20.2, 300, resets)),
            token_count_line(self.now - timedelta(minutes=4), window(20.5, 300, resets)),
        ])

        text = codex_usage.main(self.home, self.now)

        # 0.3 points in 20 minutes, so 0.5 points remain: about 33 minutes, minus the 4 already passed.
        self.assertIn("20% → 21% in ~29 min", text)

    def test_says_so_when_the_percent_has_not_moved(self):
        resets = self.now + timedelta(hours=3)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=20), window(22.0, 300, resets)),
            token_count_line(self.now - timedelta(minutes=2), window(22.0, 300, resets)),
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("22% · no recent activity", text)

    def test_gives_no_estimate_from_an_old_reading(self):
        resets = self.now + timedelta(hours=3)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=minutes), window(percent, 300, resets))
            for minutes, percent in [(70, 20.0), (50, 22.0), (40, 23.0)]
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertNotIn("→", text)
        self.assertNotIn("no recent activity", text)

    def test_readings_from_an_earlier_reset_period_are_not_mixed_in(self):
        old_reset = self.now - timedelta(hours=1)
        new_reset = self.now + timedelta(hours=4)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=30), window(95.0, 300, old_reset)),
            token_count_line(self.now - timedelta(minutes=2), window(2.0, 300, new_reset)),
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("░" * 20 + "  2%", text)
        self.assertNotIn("→", text)
        self.assertNotIn("95%", text)

    def test_projection_says_when_the_pace_hits_the_limit_before_the_reset(self):
        self.assertEqual(
            codex_usage.projection_text(70, 0.5, 100),
            "at this pace you'd hit 100% in ~1h 0m, about 40 min before the reset",
        )

    def test_projection_says_where_the_pace_ends_up_when_it_makes_the_reset(self):
        self.assertEqual(codex_usage.projection_text(40, 0.1, 120), "at this pace you'll reach ~52% by the reset")

    def test_projection_is_skipped_for_long_windows_and_missing_data(self):
        self.assertIsNone(codex_usage.projection_text(40, 0.1, 3000))
        self.assertIsNone(codex_usage.projection_text(40, None, 120))
        self.assertIsNone(codex_usage.projection_text(100, 0.1, 120))

    def test_the_answer_shows_the_projection(self):
        resets = self.now + timedelta(hours=3)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=minutes), window(percent, 300, resets))
            for minutes, percent in [(32, 20.0), (22, 21.0), (12, 22.0), (2, 23.0)]
        ])

        text = codex_usage.main(self.home, self.now)

        self.assertIn("at this pace you'll reach ~41% by the reset", text)

    def test_a_monthly_window_gets_no_projection(self):
        resets = self.now + timedelta(days=20)
        self.write_session("rollout-a.jsonl", [
            token_count_line(self.now - timedelta(minutes=minutes), window(percent, 43200, resets))
            for minutes, percent in [(32, 20.0), (2, 23.0)]
        ])

        self.assertNotIn("at this pace", codex_usage.main(self.home, self.now))

    def test_breakdown_splits_tokens_by_project_and_model(self):
        primary = window(30.0, 300, self.now + timedelta(hours=3))
        self.write_session("rollout-a.jsonl", meta_lines("/work/alpha", "gpt-a") + [
            usage_line(self.now - timedelta(minutes=60), 1000, primary),
            usage_line(self.now - timedelta(minutes=30), 1600, primary),
        ])
        self.write_session("rollout-b.jsonl", meta_lines("/work/beta", "gpt-b") + [
            usage_line(self.now - timedelta(minutes=20), 400, primary),
        ])

        text = codex_usage.main(self.home, self.now, command="breakdown")

        self.assertIn("2k tokens counted", text)
        self.assertRegex(text, r"alpha\s+█+░*\s+80%")
        self.assertRegex(text, r"beta\s+█+░*\s+20%")
        self.assertRegex(text, r"gpt-a\s+█+░*\s+80%")
        self.assertIn("not exact shares of your limit", text)

    def test_breakdown_counts_only_the_increase_inside_the_window(self):
        primary = window(30.0, 300, self.now + timedelta(hours=3))
        self.write_session("rollout-a.jsonl", meta_lines("/work/alpha", "gpt-a") + [
            usage_line(self.now - timedelta(hours=10), 5000, primary),
            usage_line(self.now - timedelta(minutes=60), 5300, primary),
        ])

        text = codex_usage.main(self.home, self.now, command="breakdown")

        self.assertIn("300 tokens counted", text)

    def test_breakdown_says_so_without_any_usage(self):
        empty = self.home / "empty"
        empty.mkdir()

        text = codex_usage.main(empty, self.now, command="breakdown")

        self.assertEqual(text, "No Codex usage found yet. Use Codex once, then run this again.")

    def test_token_counts_are_shortened(self):
        self.assertEqual(codex_usage.format_tokens(4_247_811), "4.2M")
        self.assertEqual(codex_usage.format_tokens(53_000), "53k")
        self.assertEqual(codex_usage.format_tokens(800), "800")

    def test_groups_past_the_top_are_folded_into_other(self):
        uses = [{"tokens": 100 - i, "project": f"p{i}"} for i in range(7)]

        rows = codex_usage.grouped_shares(uses, lambda use: use["project"], top=5)

        self.assertEqual([name for name, _ in rows], ["p0", "p1", "p2", "p3", "p4", "other"])

    def test_no_sessions_gives_a_clear_message(self):
        empty_home = self.home / "empty"
        empty_home.mkdir()

        self.assertEqual(codex_usage.main(empty_home, self.now), "No Codex usage found yet. Use Codex once, then run this again.")


if __name__ == "__main__":
    unittest.main()
