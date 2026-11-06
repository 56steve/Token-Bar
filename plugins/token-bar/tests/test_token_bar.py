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


def request_entry(request_id, when, tokens, cwd, model="claude-sonnet-5-5", subagent=False):
    return json.dumps({
        "type": "assistant",
        "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "requestId": request_id,
        "cwd": cwd,
        "isSidechain": subagent,
        "message": {"model": model, "usage": {"input_tokens": tokens, "output_tokens": 0}},
    })


def usage_entry(request_id, when, model, effort=None, per_turn=None, input_tokens=0, output=0, cache_write=0, cache_read=0):
    """A model response with every kind of token, and the effort fields the logs may carry."""
    entry = {
        "type": "assistant",
        "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "requestId": request_id,
        "cwd": "/work/demo",
        "message": {"model": model, "usage": {
            "input_tokens": input_tokens, "output_tokens": output,
            "cache_creation_input_tokens": cache_write, "cache_read_input_tokens": cache_read,
        }},
    }
    if effort is not None:
        entry["effort"] = effort
    if per_turn is not None:
        entry["perTurnEffort"] = per_turn
    return json.dumps(entry)


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

    def save_weekly(self, percent, hours_to_reset):
        reset = int((self.now + timedelta(hours=hours_to_reset)).timestamp())
        token_bar.write_json(token_bar.live_path(self.home), {
            "rate_limits": {"seven_day": {"used_percentage": percent, "resets_at": reset}},
            "saved_at": int(self.now.timestamp()),
        })

    def test_a_weekly_only_account_is_not_asked_for_a_5_hour_limit(self):
        self.save_weekly(30, hours_to_reset=100)

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("Weekly  ", text)
        self.assertNotIn("Not set up yet", text)
        self.assertNotIn("5-hour  ", text)
        self.assertIn("Claude reports no 5-hour limit for your account, only a weekly one.", text)

    def test_a_missing_weekly_reading_is_still_asked_for(self):
        reset = int((self.now + timedelta(hours=2)).timestamp())
        token_bar.write_json(token_bar.live_path(self.home), {
            "rate_limits": {"five_hour": {"used_percentage": 21, "resets_at": reset}},
            "saved_at": int(self.now.timestamp()),
        })

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("Not set up yet: Weekly.", text)

    def test_weekly_only_setting_hides_the_5_hour_window_without_live_numbers(self):
        message = token_bar.main(["weekly-only"], self.home)[0]
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=5), 100))
        token_bar.calibrate(self.home, "weekly", "10", "Sun 5:30 AM", self.now)

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("only track your weekly limit", message)
        self.assertIn("Weekly  ", text)
        self.assertNotIn("5-hour", text)
        self.assertNotIn("Not set up yet", text)

    def test_weekly_only_with_nothing_set_up_asks_only_for_the_weekly_limit(self):
        token_bar.main(["weekly-only"], self.home)

        text = token_bar.build_report(self.home, self.now)

        self.assertTrue(text.startswith("Setup needed."))
        self.assertIn("weekly:", text)
        self.assertNotIn("five_hour:", text)

    def test_weekly_only_can_be_turned_off_again(self):
        token_bar.main(["weekly-only"], self.home)

        message = token_bar.main(["weekly-only", "off"], self.home)[0]
        text = token_bar.build_report(self.home, self.now)

        self.assertIn("both your 5-hour and weekly", message)
        self.assertIn("five_hour:", text)

    def test_weekly_only_account_gets_a_pace_projection_for_the_week(self):
        # 3 days into the 7-day window at 30%: 10% a day, so about 70% by the reset.
        self.save_weekly(30, hours_to_reset=96)

        text = token_bar.build_report(self.home, self.now, "verdict")

        self.assertIn("✓ On track. You'll finish the week near 70%.", text)

    def test_a_pace_that_ends_close_to_the_limit_is_called_tight(self):
        # 3 days in at 40%: 0.56% an hour, so about 93% by the reset.
        self.save_weekly(40, hours_to_reset=96)

        text = token_bar.build_report(self.home, self.now, "verdict")

        self.assertIn("◐ Tight. You'll finish the week near 93%.", text)

    def test_weekly_average_pace_says_when_the_limit_comes_first(self):
        # 2 days in at 60%: 1.25% an hour, so the last 40% takes 32 more hours, well before the reset.
        self.save_weekly(60, hours_to_reset=120)

        text = token_bar.build_report(self.home, self.now, "verdict")

        self.assertRegex(
            text,
            r"⚠ Using it too fast\. At this rate you run out ~\w{3} \d{1,2}:\d{2} [AP]M, 3d 16h before the reset\.",
        )

    def test_weekly_average_pace_stays_quiet_right_after_a_reset(self):
        self.assertIsNone(token_bar.average_projection_text(5, 167 * 60, 168, self.now))
        self.assertIsNone(token_bar.average_projection_text(100, 60, 168, self.now))
        self.assertIsNone(token_bar.average_projection_text(0, 60, 168, self.now))

    def test_the_report_shows_a_daily_budget_for_the_week(self):
        # 30% used and 4 days left: 70% spread over 4 days is 17.5% a day.
        self.save_weekly(30, hours_to_reset=96)

        text = token_bar.build_report(self.home, self.now, "budget")

        self.assertIn("Budget: ~18% a day.", text)

    def test_the_weekly_block_is_the_bar_then_the_reset_then_the_verdict_then_the_budget(self):
        self.save_weekly(30, hours_to_reset=96)

        lines = token_bar.build_report(self.home, self.now, "all").splitlines()

        self.assertEqual(lines[0], "Weekly  " + "█" * 6 + "░" * 14 + "  30%")
        self.assertEqual(lines[1], "        resets in 4d 0h")
        self.assertTrue(lines[2].startswith("        ✓ On track"))
        self.assertTrue(lines[3].startswith("        Budget: ~18% a day."))

    def test_the_budget_counts_down_the_hours_on_the_last_day(self):
        self.assertEqual(
            token_bar.budget_text(30, 10 * 60),
            "Budget: ~70% left for the last 10h 0m.",
        )

    def test_the_budget_says_how_much_was_used_today_and_when_it_is_over(self):
        self.assertEqual(
            token_bar.budget_text(30, 96 * 60, used_today=5),
            "Budget: ~18% a day. Today: 5%.",
        )
        self.assertEqual(
            token_bar.budget_text(30, 96 * 60, used_today=25),
            "Budget: ~18% a day. Today: 25%, over budget.",
        )

    def test_the_budget_stays_quiet_when_the_limit_is_reached_or_the_window_reset(self):
        self.assertIsNone(token_bar.budget_text(100, 96 * 60))
        self.assertIsNone(token_bar.budget_text(30, 0))
        self.assertIsNone(token_bar.budget_text(30, None))

    def tokens_on(self, days_ago, tokens):
        """A (time, tokens) record at noon, local time, the given number of days before today."""
        day = self.now.astimezone().date() - timedelta(days=days_ago)
        return datetime.combine(day, datetime.min.time()).replace(hour=12).astimezone().astimezone(timezone.utc), tokens

    def test_the_activity_chart_has_one_bar_per_day_with_the_busiest_longest(self):
        # Oldest to newest: 100k, 800k, 400k, none, 200k, 800k, 50k (today).
        records = [self.tokens_on(6, 100_000), self.tokens_on(5, 800_000), self.tokens_on(4, 400_000),
                   self.tokens_on(2, 200_000), self.tokens_on(1, 800_000), self.tokens_on(0, 50_000)]

        lines = token_bar.activity_chart(records, self.now).splitlines()

        self.assertEqual(lines[0], "Activity · last 7 days")
        self.assertEqual(len(lines), 8)
        filled = [line.count("█") for line in lines[1:]]
        self.assertEqual(filled, [2, 14, 7, 0, 4, 14, 1])
        self.assertTrue(lines[-1].startswith("Today"))
        self.assertTrue(lines[-1].endswith("█░░░░░░░░░░░░░  50k"))
        self.assertTrue(lines[2].endswith("800k"))
        self.assertTrue(lines[4].endswith("░░░░░░░░░░░░░░  0"))

    def test_any_use_shows_at_least_one_block(self):
        records = [self.tokens_on(1, 1_000_000), self.tokens_on(0, 1)]

        lines = token_bar.activity_chart(records, self.now).splitlines()

        self.assertEqual([line.count("█") for line in lines[-2:]], [14, 1])

    def test_the_activity_chart_ignores_days_outside_the_week_and_says_nothing_without_usage(self):
        self.assertIsNone(token_bar.activity_chart([], self.now))
        self.assertIsNone(token_bar.activity_chart([self.tokens_on(9, 500)], self.now))

    def test_the_report_ends_with_the_activity_chart_when_there_is_usage(self):
        self.save_weekly(30, hours_to_reset=100)
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=2), 500))

        text = token_bar.build_report(self.home, self.now, "all")

        self.assertIn("Activity · last 7 days", text)
        self.assertLess(text.index("Budget"), text.index("Activity"))
        self.assertLess(text.index("Activity"), text.index("Source:"))

    def usage_in_the_logs(self):
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=2), 500))

    def test_the_summary_is_the_bar_a_headline_and_what_can_be_opened(self):
        self.save_weekly(30, hours_to_reset=96)
        self.usage_in_the_logs()

        lines = token_bar.build_report(self.home, self.now).splitlines()

        self.assertEqual(lines[0], "Weekly  " + "█" * 6 + "░" * 14 + "  30%")
        self.assertEqual(lines[1], "        resets in 4d 0h")
        self.assertEqual(lines[2], "        ✓ On track")
        text = "\n".join(lines)
        self.assertIn("More detail: verdict · budget · chart · all", text)
        self.assertNotIn("Budget:", text)
        self.assertNotIn("Activity", text)
        self.assertNotIn("You'll finish", text)

    def test_each_section_opens_only_its_own_detail(self):
        self.save_weekly(30, hours_to_reset=96)
        self.usage_in_the_logs()

        verdict = token_bar.build_report(self.home, self.now, "verdict")
        budget = token_bar.build_report(self.home, self.now, "budget")
        chart = token_bar.build_report(self.home, self.now, "chart")

        self.assertIn("✓ On track. You'll finish the week near 70%.", verdict)
        self.assertNotIn("Budget:", verdict + chart)
        self.assertIn("Budget: ~18% a day.", budget)
        self.assertNotIn("You'll finish", budget + chart)
        self.assertIn("Activity · last 7 days", chart)
        self.assertNotIn("Activity", verdict + budget)
        for text in (verdict, budget, chart):
            self.assertIn("Weekly  ", text)
            self.assertNotIn("More detail", text)

    def test_week_is_another_name_for_the_chart(self):
        self.save_weekly(30, hours_to_reset=96)
        self.usage_in_the_logs()

        self.assertEqual(
            token_bar.build_report(self.home, self.now, "week"),
            token_bar.build_report(self.home, self.now, "chart"),
        )

    def test_all_shows_everything(self):
        self.save_weekly(30, hours_to_reset=96)
        self.usage_in_the_logs()

        text = token_bar.build_report(self.home, self.now, "all")

        for part in ("On track. You'll finish", "Budget: ~18% a day.", "Activity · last 7 days"):
            self.assertIn(part, text)
        self.assertNotIn("More detail", text)

    def test_a_section_with_nothing_to_show_says_so(self):
        reset = int((self.now + timedelta(hours=2)).timestamp())
        token_bar.write_json(token_bar.live_path(self.home), {
            "rate_limits": {"five_hour": {"used_percentage": 21, "resets_at": reset}},
            "saved_at": int(self.now.timestamp()),
        })

        self.assertIn("No budget to show yet.", token_bar.build_report(self.home, self.now, "budget"))
        self.assertNotIn("More detail", token_bar.build_report(self.home, self.now))

    def test_the_status_command_takes_a_section_and_rejects_a_bad_one(self):
        self.save_weekly(30, hours_to_reset=96)

        text, code = token_bar.main(["status", "budget"], self.home)
        self.assertEqual(code, 0)
        self.assertIn("Budget: ~18% a day.", text)
        with self.assertRaises(SystemExit):
            token_bar.main(["status", "nonsense"], self.home)
        with self.assertRaises(ValueError):
            token_bar.build_report(self.home, self.now, "nonsense")

    def test_percents_get_a_decimal_only_when_small(self):
        self.assertEqual(token_bar.format_percent(17.5), "18")
        self.assertEqual(token_bar.format_percent(5.25), "5.2")
        self.assertEqual(token_bar.format_percent(5.0), "5")

    def test_there_is_no_budget_for_the_5_hour_window(self):
        reset = int((self.now + timedelta(hours=2)).timestamp())
        token_bar.write_json(token_bar.live_path(self.home), {
            "rate_limits": {"five_hour": {"used_percentage": 21, "resets_at": reset}},
            "saved_at": int(self.now.timestamp()),
        })

        self.assertNotIn("Budget", token_bar.build_report(self.home, self.now))

    def test_times_read_naturally_in_the_local_timezone(self):
        # Naive datetimes are read as local time, so these are Sunday in any timezone.
        def sunday(hour, minute):
            return datetime(2026, 11, 8, hour, minute).astimezone()

        self.assertEqual(token_bar.local_time_text(sunday(5, 30)), "Sun 5:30 AM")
        self.assertEqual(token_bar.local_time_text(sunday(17, 5)), "Sun 5:05 PM")
        self.assertEqual(token_bar.local_time_text(sunday(0, 0)), "Sun 12:00 AM")

    def test_no_average_pace_projection_while_a_5_hour_limit_exists(self):
        reset = int((self.now + timedelta(hours=96)).timestamp())
        soon = int((self.now + timedelta(hours=2)).timestamp())
        token_bar.write_json(token_bar.live_path(self.home), {
            "rate_limits": {
                "five_hour": {"used_percentage": 20, "resets_at": soon},
                "seven_day": {"used_percentage": 30, "resets_at": reset},
            },
            "saved_at": int(self.now.timestamp()),
        })

        text = token_bar.build_report(self.home, self.now)

        for verdict in ("On track", "Tight", "Using it too fast"):
            self.assertNotIn(verdict, text)
        self.assertNotIn("Claude reports no 5-hour limit", text)

    def test_breakdown_defaults_to_the_weekly_window_when_there_is_no_5_hour_limit(self):
        self.save_weekly(30, hours_to_reset=100)
        (self.projects / "session.jsonl").write_text(request_entry("req_1", self.now - timedelta(hours=1), 500, "/work/demo"))

        text = token_bar.main(["breakdown"], self.home)[0]

        self.assertIn("current weekly window", text)

    def test_breakdown_still_defaults_to_the_5_hour_window_otherwise(self):
        text = token_bar.main(["breakdown"], self.home)[0]

        self.assertIn("current 5-hour window", text)

    def test_an_estimate_says_it_is_an_estimate_and_how_to_fix_it(self):
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=5), 530_000))
        token_bar.calibrate(self.home, "five_hour", "53", "2:20 PM", self.now)

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("≈ Estimate from your sync 0 min ago, can be a few points off", text)
        self.assertIn("the % your app shows", text)

    def test_live_numbers_carry_no_estimate_warning(self):
        self.save_weekly(30, hours_to_reset=100)

        self.assertNotIn("Estimate", token_bar.build_report(self.home, self.now))

    def test_sync_reuses_the_saved_reset_and_moves_the_percent(self):
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=5), 610_000))
        token_bar.main(["weekly-only"], self.home)
        token_bar.calibrate(self.home, "weekly", "61", "Sun 5:30 AM", self.now)
        saved = json.loads(token_bar.config_path(self.home).read_text())["weekly"]["reset_at"]

        message, code = token_bar.main(["sync", "63"], self.home)
        text = token_bar.build_report(self.home, datetime.now(timezone.utc))

        self.assertEqual(code, 0)
        self.assertIn("Calibrated. Your weekly limit", message)
        self.assertIn("63%", text)
        self.assertEqual(json.loads(token_bar.config_path(self.home).read_text())["weekly"]["reset_at"], saved)

    def test_sync_without_a_saved_reset_asks_for_it(self):
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=5), 100))
        token_bar.main(["weekly-only"], self.home)

        message, code = token_bar.main(["sync", "63"], self.home)

        self.assertEqual(code, 1)
        self.assertIn("don't know when your weekly limit resets", message)
        self.assertIn('63 "Sun 5:30 AM"', message)

    def test_sync_picks_the_window_you_name(self):
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=5), 400_000))
        token_bar.calibrate(self.home, "five_hour", "40", "2:20 PM", self.now)

        message, _ = token_bar.main(["sync", "50", "five_hour"], self.home)

        self.assertIn("Your 5-hour limit is about 800,000 tokens", message)

    def test_sync_rejects_a_bad_percent(self):
        message, code = token_bar.main(["sync", "lots"], self.home)

        self.assertEqual(code, 1)
        self.assertIn("is not a percentage", message)

    def scan(self, cutoff=None, cached=True):
        cutoff = cutoff or self.now - timedelta(days=7)
        cache = token_bar.scan_cache_path(self.home) if cached else None
        return token_bar.scan_requests(self.home / ".claude" / "projects", cutoff, cache)

    def test_a_cached_scan_gives_the_same_requests_as_an_uncached_one(self):
        self.write_requests([
            request_entry("r1", self.now - timedelta(minutes=10), 600, "/work/alpha"),
            request_entry("r2", self.now - timedelta(minutes=9), 300, "/work/beta", model="claude-haiku-5-5", subagent=True),
            request_entry("r2", self.now - timedelta(minutes=9), 350, "/work/beta", model="claude-haiku-5-5", subagent=True),
        ])
        def key(request):
            return request["when"], request["tokens"], request["project"], request["model"], request["subagent"]

        plain = sorted(map(key, self.scan(cached=False)))

        self.assertEqual(sorted(map(key, self.scan())), plain)
        self.assertEqual(sorted(map(key, self.scan())), plain)  # and again, now from the cache
        self.assertEqual(sorted(tokens for _, tokens, *_ in plain), [350, 600])  # the streamed r2 counted once

    def test_a_second_scan_reads_only_what_was_added(self):
        path = self.projects / "session.jsonl"
        first = request_line("req_a", self.now - timedelta(minutes=20), 10_000)
        path.write_text(first + "\n")
        self.scan()
        with path.open("r+") as handle:
            handle.write("x" * len(first))  # overwrite in place, so reading it again would find nothing
        with path.open("a") as handle:
            handle.write(request_line("req_b", self.now - timedelta(minutes=5), 3_000) + "\n")

        tokens = sorted(request["tokens"] for request in self.scan())

        self.assertEqual(tokens, [3_000, 10_000])  # req_a came from the cache, not from the file

    def test_a_streamed_request_keeps_its_biggest_count_across_scans(self):
        path = self.projects / "session.jsonl"
        path.write_text(request_line("req_a", self.now - timedelta(minutes=5), 10) + "\n")
        self.scan()
        with path.open("a") as handle:
            handle.write(request_line("req_a", self.now - timedelta(minutes=5), 25) + "\n")

        self.assertEqual([request["tokens"] for request in self.scan()], [25])

    def test_a_file_that_got_shorter_is_read_again_from_the_start(self):
        path = self.projects / "session.jsonl"
        path.write_text("\n".join(request_line(f"r{i}", self.now - timedelta(minutes=i + 1), 1_000) for i in range(3)))
        self.assertEqual(len(self.scan()), 3)

        path.write_text(request_line("fresh", self.now - timedelta(minutes=1), 7))

        self.assertEqual([request["tokens"] for request in self.scan()], [7])

    def test_a_deleted_file_stops_counting(self):
        path = self.projects / "session.jsonl"
        path.write_text(request_line("req_a", self.now - timedelta(minutes=5), 100))
        self.assertEqual(len(self.scan()), 1)

        path.unlink()

        self.assertEqual(self.scan(), [])

    def test_a_half_written_last_line_waits_until_it_is_finished(self):
        path = self.projects / "session.jsonl"
        done = request_line("req_a", self.now - timedelta(minutes=9), 100)
        second = request_line("req_b", self.now - timedelta(minutes=8), 200)
        path.write_text(done + "\n" + second[:40])
        self.assertEqual([request["tokens"] for request in self.scan()], [100])

        path.write_text(done + "\n" + second + "\n")

        self.assertEqual(sorted(request["tokens"] for request in self.scan()), [100, 200])

    def test_a_last_line_without_a_newline_still_counts_when_it_is_complete(self):
        (self.projects / "session.jsonl").write_text(request_line("req_a", self.now - timedelta(minutes=9), 100))

        self.assertEqual([request["tokens"] for request in self.scan()], [100])

    def test_a_stray_invalid_byte_does_not_lose_the_request(self):
        line = request_line("req_a", self.now - timedelta(minutes=9), 100).encode()
        line = line.replace(b'"type": "assistant"', b'"type": "assistant", "note": "bad\xff"')
        line = line.replace(b'"type":"assistant"', b'"type":"assistant","note":"bad\xff"')
        (self.projects / "session.jsonl").write_bytes(line + b"\n")

        self.assertIn(b"\xff", line)
        self.assertEqual([request["tokens"] for request in self.scan()], [100])

    def test_a_damaged_or_old_cache_is_ignored_and_replaced(self):
        (self.projects / "session.jsonl").write_text(request_line("req_a", self.now - timedelta(minutes=9), 100))
        cache = token_bar.scan_cache_path(self.home)
        for bad in ("not json at all", json.dumps({"version": 0, "since": 0, "files": {}}),
                    json.dumps({"version": token_bar.SCAN_CACHE_VERSION, "since": "x", "files": []}),
                    json.dumps({"version": token_bar.SCAN_CACHE_VERSION, "since": 0, "files": {"k": "v"}})):
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(bad)

            self.assertEqual([request["tokens"] for request in self.scan()], [100])
            self.assertEqual(json.loads(cache.read_text())["version"], token_bar.SCAN_CACHE_VERSION)

    def test_a_cache_that_covers_less_time_is_not_trusted_for_a_longer_look(self):
        self.write_requests([
            request_line("old", self.now - timedelta(days=3), 500),
            request_line("new", self.now - timedelta(minutes=5), 50),
        ])
        short = self.scan(cutoff=self.now - timedelta(hours=1))
        self.assertEqual([request["tokens"] for request in short], [50])

        longer = self.scan(cutoff=self.now - timedelta(days=7))

        self.assertEqual(sorted(request["tokens"] for request in longer), [50, 500])

    def test_the_cache_drops_requests_older_than_the_cutoff(self):
        self.write_requests([
            request_line("old", self.now - timedelta(days=3), 500),
            request_line("new", self.now - timedelta(minutes=5), 50),
        ])

        self.scan(cutoff=self.now - timedelta(hours=1))

        saved = json.loads(token_bar.scan_cache_path(self.home).read_text())
        kept = [r for state in saved["files"].values() for r in state["requests"].values()]
        self.assertEqual([r[1] for r in kept], [50])

    def test_the_report_and_the_breakdown_share_one_cache(self):
        self.save_weekly(30, hours_to_reset=96)
        self.write_requests([request_entry("r1", self.now - timedelta(minutes=10), 600, "/work/alpha")])

        token_bar.build_report(self.home, self.now)
        cache = token_bar.scan_cache_path(self.home)
        saved_once = cache.read_text()
        text, _ = token_bar.main(["breakdown"], self.home)

        self.assertTrue(cache.exists())
        self.assertIn("alpha", text)
        self.assertEqual(json.loads(cache.read_text())["files"].keys(), json.loads(saved_once)["files"].keys())

    def test_requests_carry_their_effort_and_each_kind_of_token(self):
        when = self.now - timedelta(minutes=5)
        self.write_requests([usage_entry("r1", when, "model-a", per_turn="medium", input_tokens=10, output=20, cache_write=30, cache_read=40)])

        (request,) = self.scan()

        self.assertEqual(request["effort"], "medium")
        self.assertEqual(
            [request[kind] for kind in token_bar.TOKEN_KINDS], [10, 20, 30, 40]
        )
        self.assertEqual(request["tokens"], 60)  # what counts against the limit leaves out cache reads

    def test_the_per_turn_effort_wins_and_odd_values_are_ignored(self):
        when = self.now - timedelta(minutes=5)
        self.write_requests([
            usage_entry("a", when, "m", effort="medium", per_turn="high", output=1),
            usage_entry("b", when, "m", effort="low", output=1),
            usage_entry("c", when, "m", output=1),
            json.dumps({**json.loads(usage_entry("d", when, "m", output=1)), "perTurnEffort": 5, "effort": ""}),
        ])

        efforts = {request["tokens"] and request["effort"] for request in self.scan()}

        self.assertEqual(efforts, {"high", "low", None})

    def test_an_older_cache_is_read_again_because_it_lacks_the_new_fields(self):
        when = self.now - timedelta(minutes=5)
        self.write_requests([usage_entry("r1", when, "model-a", per_turn="high", output=7)])
        cache = token_bar.scan_cache_path(self.home)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"version": 1, "since": 0, "files": {}}))

        (request,) = self.scan()

        self.assertEqual(request["effort"], "high")
        self.assertEqual(json.loads(cache.read_text())["version"], token_bar.SCAN_CACHE_VERSION)

    def breakdown_requests(self):
        when = self.now - timedelta(minutes=5)
        self.write_requests([
            usage_entry("a", when, "claude-sonnet-5-5", per_turn="medium", output=600),
            usage_entry("b", when, "claude-sonnet-5-5", per_turn="medium", output=200),
            usage_entry("c", when, "claude-opus-5-5", per_turn="high", output=200),
        ])
        return self.scan()

    def test_the_breakdown_shows_requests_by_model_and_by_effort(self):
        text = token_bar.breakdown_text(self.breakdown_requests(), "weekly")

        self.assertRegex(text, r"Sonnet 5\.5\s+█+░*\s+80%\s+2 requests")
        self.assertRegex(text, r"Opus 5\.5\s+█+░*\s+20%\s+1 request\b")
        self.assertIn("By effort", text)
        self.assertRegex(text, r"Medium\s+█+░*\s+80%\s+2 requests")
        self.assertRegex(text, r"High\s+█+░*\s+20%\s+1 request\b")

    def test_there_is_no_effort_section_when_the_logs_record_none(self):
        self.write_requests([usage_entry("a", self.now - timedelta(minutes=5), "claude-sonnet-5-5", output=100)])

        text = token_bar.breakdown_text(self.scan(), "weekly")

        self.assertNotIn("By effort", text)
        self.assertIn("By model", text)

    def write_prices(self, table, **extra):
        path = token_bar.prices_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"per_million_tokens": table, **extra}))

    def priced_requests(self):
        when = self.now - timedelta(minutes=5)
        self.write_requests([
            usage_entry("a", when, "model-a", per_turn="high", input_tokens=1_000_000, output=100_000, cache_write=200_000, cache_read=5_000_000),
            usage_entry("b", when, "family-x-2", per_turn="medium", input_tokens=1_000_000),
            usage_entry("c", when, "no-price-model", per_turn="medium", output=500),
        ])
        return self.scan()

    def test_the_cost_is_worked_out_from_each_kind_of_token_at_the_users_prices(self):
        self.write_prices({
            "model-a": {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.3},
            "family-x-*": {"input": 2},
        }, currency="€")

        text = token_bar.cost_text(self.priced_requests(), token_bar.load_prices(self.home), "weekly", token_bar.prices_path(self.home))

        # model-a: 3 + 1.5 + 0.75 + 1.5 = 6.75, family-x-2: 2.00.
        self.assertIn("€      6.75", text)
        self.assertIn("€      2.00", text)
        self.assertIn("€      8.75", text)  # the total
        self.assertIn("No price for: no-price-model (1 request)", text)
        self.assertIn("By effort", text)
        self.assertRegex(text, r"High\s+█+░*\s+€\s+6\.75")
        self.assertIn("isn't billed per token", text)

    def test_the_cost_says_how_to_set_prices_when_there_is_no_file(self):
        text = token_bar.cost_text(self.priced_requests(), token_bar.load_prices(self.home), "weekly", token_bar.prices_path(self.home))

        self.assertIn("no built-in prices", text)
        self.assertIn(str(token_bar.prices_path(self.home)), text)
        self.assertIn('"per_million_tokens"', text)
        self.assertIn("Models seen in this window: family-x-2, model-a, no-price-model", text)

    def test_the_longest_family_entry_wins_and_unknown_models_have_no_price(self):
        prices = {"claude-opus-*": {"input": 1}, "claude-opus-5-*": {"input": 2}, "exact": {"input": 9}}

        self.assertEqual(token_bar.price_for("claude-opus-5-5", prices), {"input": 2})
        self.assertEqual(token_bar.price_for("claude-opus-4-7", prices), {"input": 1})
        self.assertEqual(token_bar.price_for("exact", prices), {"input": 9})
        self.assertIsNone(token_bar.price_for("claude-haiku-4-5", prices))
        self.assertIsNone(token_bar.price_for(None, prices))

    def test_a_prices_file_with_a_mistake_says_what_is_wrong(self):
        path = token_bar.prices_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        for content, expected in (
            ("{}", "per_million_tokens"),
            (json.dumps({"per_million_tokens": {"m": "cheap"}}), "should list prices"),
            (json.dumps({"per_million_tokens": {"m": {"inputs": 1}}}), "not a kind of token"),
            (json.dumps({"per_million_tokens": {"m": {"input": -1}}}), "a number, 0 or more"),
            (json.dumps({"per_million_tokens": {"m": {"input": "3"}}}), "a number, 0 or more"),
            (json.dumps({"per_million_tokens": {"m": {"input": True}}}), "a number, 0 or more"),
            ("not json", "per_million_tokens"),
        ):
            path.write_text(content)

            with self.assertRaises(ValueError) as caught:
                token_bar.load_prices(self.home)

            self.assertIn(expected, str(caught.exception))

    def test_the_cost_command_reads_the_prices_file_and_reports_a_bad_one(self):
        token_bar.write_json(token_bar.config_path(self.home), {"weekly_only": True})
        self.priced_requests()
        self.write_prices({"model-a": {"output": 10}})

        text, code = token_bar.main(["cost"], self.home)

        self.assertEqual(code, 0)
        self.assertIn("$", text)
        self.assertIn("No price for:", text)

        token_bar.prices_path(self.home).write_text(json.dumps({"per_million_tokens": {"m": {"input": -5}}}))
        text, code = token_bar.main(["cost"], self.home)
        self.assertEqual(code, 1)
        self.assertIn("a number, 0 or more", text)

    def test_the_cost_says_so_when_there_is_no_usage(self):
        self.assertEqual(
            token_bar.cost_text([], None, "weekly", token_bar.prices_path(self.home)),
            "No Claude Code usage in the current weekly window yet.",
        )

    def synced_setting(self, percent, minutes_ago, limit=1_000_000):
        at = self.now - timedelta(minutes=minutes_ago)
        return {"limit": limit, "synced": {"percent": percent, "at": at.isoformat()}}

    def save_weekly_config(self, setting):
        """Saves a weekly setting whose reset is three days away, so the window is the last four days."""
        setting = dict(setting, reset_at=(self.now + timedelta(days=3)).isoformat())
        token_bar.write_json(token_bar.config_path(self.home), {"weekly": setting, "weekly_only": True})

    def test_the_estimate_is_the_last_sync_plus_the_usage_since(self):
        start = self.now - timedelta(days=3)
        records = [
            (self.now - timedelta(hours=5), 500_000),    # before the sync, already in its percent
            (self.now - timedelta(minutes=30), 100_000),
            (self.now - timedelta(minutes=5), 50_000),
        ]

        percent, age = token_bar.percent_since_sync(self.synced_setting(60, 60), records, start, self.now)

        self.assertAlmostEqual(percent, 75)
        self.assertEqual(age, 3600)

    def test_usage_at_the_moment_of_the_sync_is_already_in_the_synced_percent(self):
        setting = self.synced_setting(40, 10)
        at = self.now - timedelta(minutes=10)

        percent, _ = token_bar.percent_since_sync(setting, [(at, 200_000)], self.now - timedelta(days=1), self.now)

        self.assertEqual(percent, 40)

    def test_the_synced_estimate_stops_at_100(self):
        percent, _ = token_bar.percent_since_sync(
            self.synced_setting(95, 60), [(self.now - timedelta(minutes=5), 200_000)], self.now - timedelta(days=1), self.now
        )

        self.assertEqual(percent, 100)

    def test_there_is_no_anchor_without_a_usable_sync_in_this_window(self):
        start = self.now - timedelta(days=1)
        good = self.synced_setting(50, 10)

        self.assertIsNone(token_bar.percent_since_sync({"limit": 1_000_000}, [], start, self.now))
        self.assertIsNone(token_bar.percent_since_sync(None, [], start, self.now))
        self.assertIsNone(token_bar.percent_since_sync(dict(good, limit=None), [], start, self.now))
        self.assertIsNone(token_bar.percent_since_sync(self.synced_setting(50, 30 * 60), [], start, self.now))
        self.assertIsNone(token_bar.percent_since_sync(self.synced_setting(50, -10), [], start, self.now))
        for broken in ({"percent": "lots", "at": self.now.isoformat()}, {"percent": 50},
                       {"percent": 50, "at": "garbage"}, "nope"):
            setting = {"limit": 1_000_000, "synced": broken}
            self.assertIsNone(token_bar.percent_since_sync(setting, [], start, self.now))

    def test_calibrate_records_the_sync(self):
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(minutes=5), 530_000))

        token_bar.calibrate(self.home, "five_hour", "53", "2:20 PM", self.now)

        synced = json.loads(token_bar.config_path(self.home).read_text())["five_hour"]["synced"]
        self.assertEqual(synced["percent"], 53.0)
        self.assertEqual(token_bar.parse_time(synced["at"]), self.now)

    def test_a_report_after_a_sync_adds_only_the_usage_since_it(self):
        # Synced at 50% an hour ago with a 1M limit, then 100k more tokens: 60%.
        self.save_weekly_config(self.synced_setting(50, 60))
        (self.projects / "session.jsonl").write_text("\n".join([
            request_line("req_1", self.now - timedelta(hours=2), 400_000),
            request_line("req_2", self.now - timedelta(minutes=30), 100_000),
        ]))

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("  60%", text)
        self.assertIn("≈ Estimate from your sync 1h 0m ago, can be a few points off.", text)

    def test_the_old_way_still_works_for_a_calibration_saved_before_syncs_were_recorded(self):
        self.save_weekly_config({"limit": 1_000_000})
        (self.projects / "session.jsonl").write_text(request_line("req_1", self.now - timedelta(hours=2), 300_000))

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("  30%", text)
        self.assertIn("≈ Estimate, can be a few points off.", text)
        self.assertNotIn("from your sync", text)

    def test_an_old_sync_says_the_estimate_may_have_drifted(self):
        self.save_weekly_config(self.synced_setting(50, 14 * 60))

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("≈ Estimate from your sync 14h 0m ago, so it may have drifted.", text)

    def test_syncing_again_moves_the_anchor_to_the_new_percent(self):
        self.save_weekly_config(self.synced_setting(50, 60))
        (self.projects / "session.jsonl").write_text(request_line("req_1", datetime.now(timezone.utc) - timedelta(minutes=30), 100_000))

        message, code = token_bar.main(["sync", "57"], self.home)
        text = token_bar.build_report(self.home, datetime.now(timezone.utc))

        self.assertEqual(code, 0)
        self.assertIn("Calibrated.", message)
        self.assertIn("  57%", text)
        self.assertIn("sync 0 min ago", text)

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

    def test_projection_says_when_the_pace_hits_the_limit_before_the_reset(self):
        text = token_bar.projection_text(70, 0.5, 100)

        self.assertEqual(text, "at this pace you'd hit 100% in ~1h 0m, about 40 min before the reset")

    def test_projection_says_where_the_pace_ends_up_when_it_makes_the_reset(self):
        self.assertEqual(token_bar.projection_text(40, 0.1, 120), "at this pace you'll reach ~52% by the reset")

    def test_projection_is_skipped_for_long_windows_and_missing_data(self):
        self.assertIsNone(token_bar.projection_text(40, 0.1, 3000))
        self.assertIsNone(token_bar.projection_text(40, None, 120))
        self.assertIsNone(token_bar.projection_text(100, 0.1, 120))
        self.assertIsNone(token_bar.projection_text(40, 0.1, None))

    def test_the_report_shows_the_projection_from_the_percent_history(self):
        resets = self.now + timedelta(hours=3)
        self.save_readings([(32, 20.0), (22, 21.0), (12, 22.0), (2, 23.0)], resets)

        text = token_bar.build_report(self.home, self.now)

        self.assertIn("at this pace you'll reach ~41% by the reset", text)

    def test_the_report_has_no_projection_without_a_pace(self):
        resets = self.now + timedelta(hours=3)
        self.save_readings([(2, 22.0)], resets)

        self.assertNotIn("at this pace", token_bar.build_report(self.home, self.now))

    def write_requests(self, entries):
        (self.projects / "session.jsonl").write_text("\n".join(entries))

    def test_breakdown_shares_by_project_model_and_subagent(self):
        when = self.now - timedelta(minutes=10)
        self.write_requests([
            request_entry("r1", when, 600, "/work/alpha"),
            request_entry("r2", when, 300, "/work/beta", model="claude-haiku-5-5", subagent=True),
            request_entry("r3", when, 100, "/work/alpha"),
        ])
        requests = token_bar.scan_requests(self.home / ".claude" / "projects", self.now - timedelta(hours=1))

        text = token_bar.breakdown_text(requests, "5-hour")

        self.assertIn("1k tokens counted", text)
        self.assertRegex(text, r"alpha\s+█+░*\s+70%")
        self.assertRegex(text, r"beta\s+█+░*\s+30%")
        self.assertRegex(text, r"Sonnet 5\.5\s+█+░*\s+70%")
        self.assertRegex(text, r"Haiku 5\.5\s+█+░*\s+30%")
        self.assertRegex(text, r"Subagents\s+█+░*\s+30%")
        self.assertIn("not exact shares of your limit", text)

    def test_breakdown_counts_a_streamed_request_once(self):
        when = self.now - timedelta(minutes=10)
        self.write_requests([request_entry("r1", when, 500, "/work/alpha")] * 3)
        requests = token_bar.scan_requests(self.home / ".claude" / "projects", self.now - timedelta(hours=1))

        self.assertEqual(sum(r["tokens"] for r in requests), 500)

    def test_breakdown_command_says_so_when_there_is_no_usage(self):
        text, code = token_bar.main(["breakdown"], self.home)

        self.assertEqual(code, 0)
        self.assertEqual(text, "No Claude Code usage in the current 5-hour window yet.")

    def test_breakdown_command_reads_the_projects_folder(self):
        self.write_requests([request_entry("r1", datetime.now(timezone.utc) - timedelta(minutes=5), 900, "/work/alpha")])

        text, _ = token_bar.main(["breakdown"], self.home)

        self.assertIn("Where your tokens went in the current 5-hour window", text)
        self.assertIn("alpha", text)

    def test_the_window_starts_at_claudes_reset_minus_its_length(self):
        reset = self.now + timedelta(hours=2)
        token_bar.write_json(token_bar.live_path(self.home), {
            "rate_limits": {"five_hour": {"used_percentage": 10, "resets_at": int(reset.timestamp())}},
            "saved_at": int(self.now.timestamp()),
        })

        start = token_bar.window_bounds(self.home, "five_hour", self.now)

        self.assertEqual(start, reset - timedelta(hours=5))

    def test_model_names_are_made_readable(self):
        self.assertEqual(token_bar.model_label("claude-sonnet-5-5"), "Sonnet 5.5")
        self.assertEqual(token_bar.model_label("claude-sonnet-5"), "Sonnet 5")
        self.assertEqual(token_bar.model_label("claude-opus-4-1-20250805"), "Opus 4.1")
        self.assertEqual(token_bar.model_label("some-other-model"), "some-other-model")
        self.assertEqual(token_bar.model_label(None), "unknown")

    def test_token_counts_are_shortened(self):
        self.assertEqual(token_bar.format_tokens(4_247_811), "4.2M")
        self.assertEqual(token_bar.format_tokens(53_000), "53k")
        self.assertEqual(token_bar.format_tokens(800), "800")

    def test_groups_past_the_top_are_folded_into_other(self):
        requests = [{"tokens": 100 - i, "project": f"p{i}"} for i in range(7)]

        rows = token_bar.grouped_shares(requests, lambda r: r["project"], top=5)

        self.assertEqual([name for name, _ in rows], ["p0", "p1", "p2", "p3", "p4", "other"])
        self.assertEqual(rows[-1][1], 95 + 94)

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


class WarnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        self.reset = int((self.now + timedelta(hours=2)).timestamp())

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, percent, saved_minutes_ago=0, reset=None, key="five_hour"):
        token_bar.write_json(token_bar.live_path(self.home), {
            "rate_limits": {key: {"used_percentage": percent, "resets_at": reset or self.reset}},
            "saved_at": int((self.now - timedelta(minutes=saved_minutes_ago)).timestamp()),
        })

    def test_warns_once_when_a_window_crosses_80(self):
        self.save(82)

        first = token_bar.warning_output(self.home, self.now)
        second = token_bar.warning_output(self.home, self.now)

        self.assertEqual(json.loads(first), {"systemMessage": "token-bar: 5-hour usage is at 82%, resetting in 2h 0m"})
        self.assertEqual(second, "")

    def test_warns_again_at_90_but_not_between_levels(self):
        self.save(82)
        token_bar.warning_output(self.home, self.now)
        self.save(86)
        self.assertEqual(token_bar.warning_output(self.home, self.now), "")

        self.save(91)

        self.assertIn("is at 91%", token_bar.warning_output(self.home, self.now))

    def test_a_new_reset_period_warns_again(self):
        self.save(85)
        token_bar.warning_output(self.home, self.now)
        self.save(85, reset=self.reset + 5 * 3600)

        self.assertIn("is at 85%", token_bar.warning_output(self.home, self.now))

    def test_stays_quiet_below_80_and_for_old_readings_and_without_readings(self):
        self.save(79)
        self.assertEqual(token_bar.warning_output(self.home, self.now), "")

        self.save(95, saved_minutes_ago=45)
        self.assertEqual(token_bar.warning_output(self.home, self.now), "")

        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(token_bar.warning_output(empty, self.now), "")

    def test_can_be_turned_off_with_an_environment_variable(self):
        import os
        from unittest import mock

        self.save(95)
        with mock.patch.dict(os.environ, {"TOKEN_BAR_NO_WARN": "1"}):
            self.assertEqual(token_bar.warning_output(self.home, self.now), "")

    def test_a_broken_state_file_never_raises(self):
        self.save(90)
        token_bar.warned_path(self.home).parent.mkdir(parents=True, exist_ok=True)
        token_bar.warned_path(self.home).write_text("{ not json")

        self.assertIn("is at 90%", token_bar.warning_output(self.home, self.now))

    def save_estimate(self, percent, key="weekly", hours_synced_ago=1, synced=True, **extra):
        """Saves a calibration whose reset is three days away, synced at `percent`, as the desktop app has."""
        setting = {"limit": 1_000_000, "reset_at": (self.now + timedelta(hours=72)).isoformat()}
        if synced:
            setting["synced"] = {"percent": percent, "at": (self.now - timedelta(hours=hours_synced_ago)).isoformat()}
        token_bar.write_json(token_bar.config_path(self.home), {key: setting, **extra})

    def write_log(self, minutes_ago, tokens):
        projects = self.home / ".claude" / "projects" / "demo"
        projects.mkdir(parents=True, exist_ok=True)
        when = self.now - timedelta(minutes=minutes_ago)
        (projects / "session.jsonl").write_text(request_line("req_1", when, tokens))

    def test_the_synced_estimate_warns_when_there_are_no_live_numbers(self):
        self.save_estimate(82)

        message = json.loads(token_bar.warning_output(self.home, self.now))["systemMessage"]

        self.assertEqual(message, "token-bar: Weekly usage is around 82% (estimate), resetting in 3d 0h")

    def test_the_estimate_warns_once_then_again_at_90(self):
        self.save_estimate(82)
        token_bar.warning_output(self.home, self.now)
        self.assertEqual(token_bar.warning_output(self.home, self.now), "")
        self.save_estimate(86)
        self.assertEqual(token_bar.warning_output(self.home, self.now), "")

        self.save_estimate(91)

        self.assertIn("around 91% (estimate)", token_bar.warning_output(self.home, self.now))

    def test_the_estimate_counts_the_usage_since_the_sync(self):
        # Synced at 70% an hour ago, then 150k of a 1M limit: 85%.
        self.save_estimate(70)
        self.write_log(minutes_ago=30, tokens=150_000)

        self.assertIn("around 85% (estimate)", token_bar.warning_output(self.home, self.now))

    def test_an_estimate_below_80_stays_quiet(self):
        self.save_estimate(79)

        self.assertEqual(token_bar.warning_output(self.home, self.now), "")

    def test_a_calibration_that_was_never_synced_does_not_warn(self):
        self.save_estimate(0, synced=False)
        self.write_log(minutes_ago=30, tokens=900_000)  # the old method would read this as 90%

        self.assertEqual(token_bar.warning_output(self.home, self.now), "")

    def test_fresh_live_numbers_win_over_the_estimate(self):
        self.save_estimate(85)
        self.save(50, key="seven_day", reset=int((self.now + timedelta(hours=72)).timestamp()))

        self.assertEqual(token_bar.warning_output(self.home, self.now), "")

    def test_old_live_numbers_fall_back_to_the_estimate(self):
        self.save_estimate(85)
        self.save(50, saved_minutes_ago=45, key="seven_day", reset=int((self.now + timedelta(hours=72)).timestamp()))

        self.assertIn("around 85% (estimate)", token_bar.warning_output(self.home, self.now))

    def test_there_is_no_5_hour_estimate_when_the_account_has_no_5_hour_limit(self):
        self.save_estimate(85, key="five_hour", weekly_only=True)

        self.assertEqual(token_bar.warning_output(self.home, self.now), "")

    def test_a_broken_calibration_never_raises(self):
        for setting in ({"limit": 1_000_000, "reset_at": "garbage"}, {"limit": "lots", "reset_at": self.now.isoformat()},
                        {"limit": 1_000_000}, "nope"):
            token_bar.write_json(token_bar.config_path(self.home), {"weekly": setting})

            self.assertEqual(token_bar.warning_output(self.home, self.now), "")

    def test_the_hook_notes_that_it_ran_even_with_warnings_turned_off(self):
        import os
        from unittest import mock

        self.assertIn("hasn't run on this machine yet", token_bar.hook_check_text(self.home, self.now))

        with mock.patch.dict(os.environ, {"TOKEN_BAR_NO_WARN": "1"}):
            token_bar.warning_output(self.home, self.now)

        self.assertEqual(
            token_bar.hook_check_text(self.home, self.now + timedelta(minutes=7)),
            "The heads-up hook last ran 7 min ago.",
        )

    def test_the_hook_check_command_reports_the_last_run(self):
        token_bar.warning_output(self.home, datetime.now(timezone.utc))

        text, code = token_bar.main(["hook-check"], self.home)

        self.assertEqual(code, 0)
        self.assertEqual(text, "The heads-up hook last ran 0 min ago.")


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_adds_a_personal_token_skill_that_hands_off_to_the_plugin(self):
        result = token_bar.install_shortcut(self.home)

        text = token_bar.shortcut_path(self.home).read_text()
        self.assertEqual(result, ("set", 0))
        self.assertIn("name: token\n", text)
        self.assertIn("skill `token-bar:token` and args `$ARGUMENTS`", text)
        self.assertIn("/plugin install token-bar --marketplace 56steve/Token-Bar", text)
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text)
        self.assertIn(token_bar.SHORTCUT_MARKER, text)

    def test_running_it_twice_says_it_is_already_added(self):
        token_bar.install_shortcut(self.home)

        self.assertEqual(token_bar.install_shortcut(self.home), ("already", 0))

    def test_a_skill_the_user_wrote_is_never_replaced(self):
        mine = token_bar.shortcut_path(self.home)
        mine.parent.mkdir(parents=True)
        mine.write_text("---\nname: token\n---\nMy own skill.\n")

        result = token_bar.install_shortcut(self.home)

        self.assertEqual(result, ("kept", 3))
        self.assertEqual(mine.read_text(), "---\nname: token\n---\nMy own skill.\n")

    def test_an_older_copy_of_ours_is_brought_up_to_date(self):
        old = token_bar.shortcut_path(self.home)
        old.parent.mkdir(parents=True)
        old.write_text(f"old text\n\n{token_bar.SHORTCUT_MARKER}\n")

        result = token_bar.install_shortcut(self.home)

        self.assertEqual(result, ("set", 0))
        self.assertIn("name: token", old.read_text())

    def test_the_command_reports_what_it_did(self):
        text, code = token_bar.main(["install-shortcut"], self.home)

        self.assertEqual(code, 0)
        self.assertIn("Added the short /token command", text)

    def test_status_says_the_short_command_is_off_until_it_is_added(self):
        before = token_bar.build_report(self.home, datetime.now(timezone.utc))
        token_bar.install_shortcut(self.home)
        after = token_bar.build_report(self.home, datetime.now(timezone.utc))

        self.assertTrue(before.endswith("Short command: off"))
        self.assertNotIn("Short command", after)


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
