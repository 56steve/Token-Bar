use chrono::{DateTime, Datelike, Duration, Local, NaiveTime, TimeZone, Utc, Weekday};

/// Start of the window that contains `now`, for a window of `period` that resets at `anchor`.
///
/// Claude's limits reset on fixed schedules, so the window is counted from the last reset,
/// not from a rolling point in the past. Any reset time works as the anchor.
pub fn window_start(anchor: DateTime<Utc>, period: Duration, now: DateTime<Utc>) -> DateTime<Utc> {
    let period_ms = period.num_milliseconds();
    let elapsed_ms = (now - anchor).num_milliseconds();
    let windows_elapsed = elapsed_ms.div_euclid(period_ms);
    anchor + Duration::milliseconds(windows_elapsed * period_ms)
}

/// Parses a local reset time such as "2:20 PM" or "Sun 5:30 AM" and returns its next occurrence after `now`.
pub fn next_reset(text: &str, now: DateTime<Utc>) -> Option<DateTime<Utc>> {
    let mut words: Vec<&str> = text.split_whitespace().collect();
    let weekday = words.first().and_then(|word| word.parse::<Weekday>().ok());
    if weekday.is_some() {
        words.remove(0);
    }
    let clock = words.join(" ").to_uppercase();
    let time = NaiveTime::parse_from_str(&clock, "%I:%M %p")
        .or_else(|_| NaiveTime::parse_from_str(&clock, "%I:%M%p"))
        .ok()?;

    let today = now.with_timezone(&Local).date_naive();
    for offset in 0..8 {
        let date = today + Duration::days(offset);
        if weekday.is_some_and(|wanted| date.weekday() != wanted) {
            continue;
        }
        let Some(local) = Local.from_local_datetime(&date.and_time(time)).earliest() else {
            continue;
        };
        let occurrence = local.with_timezone(&Utc);
        if occurrence > now {
            return Some(occurrence);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Timelike;

    fn utc(text: &str) -> DateTime<Utc> {
        text.parse().expect("valid test time")
    }

    #[test]
    fn window_starts_at_the_last_reset() {
        let anchor = utc("2026-10-29T14:20:00Z");
        let five_hours = Duration::hours(5);

        assert_eq!(
            window_start(anchor, five_hours, utc("2026-10-29T12:00:00Z")),
            utc("2026-10-29T09:20:00Z")
        );
        assert_eq!(
            window_start(anchor, five_hours, utc("2026-10-29T15:00:00Z")),
            utc("2026-10-29T14:20:00Z")
        );
        assert_eq!(window_start(anchor, five_hours, anchor), anchor);
    }

    #[test]
    fn anchors_work_when_they_are_in_the_future() {
        let anchor = utc("2026-10-29T18:00:00Z");

        assert_eq!(
            window_start(anchor, Duration::hours(5), utc("2026-10-29T12:00:00Z")),
            utc("2026-10-29T08:00:00Z")
        );
    }

    #[test]
    fn parses_a_daily_reset_time_as_the_next_local_occurrence() {
        let now = utc("2026-10-29T12:00:00Z");

        let reset = next_reset("2:20 PM", now).expect("valid time");

        assert!(reset > now);
        assert!(reset - now <= Duration::hours(24));
        let local = reset.with_timezone(&Local);
        assert_eq!((local.hour(), local.minute()), (14, 20));
    }

    #[test]
    fn parses_a_weekly_reset_with_a_weekday() {
        let now = utc("2026-10-29T12:00:00Z");

        let reset = next_reset("Sun 5:30 AM", now).expect("valid day and time");

        assert!(reset > now);
        assert!(reset - now <= Duration::days(8));
        let local = reset.with_timezone(&Local);
        assert_eq!(local.weekday(), Weekday::Sun);
        assert_eq!((local.hour(), local.minute()), (5, 30));
    }

    #[test]
    fn rejects_text_that_is_not_a_time() {
        assert!(next_reset("sometime", utc("2026-10-29T12:00:00Z")).is_none());
        assert!(next_reset("Funday 5:30 AM", utc("2026-10-29T12:00:00Z")).is_none());
    }
}
