use chrono::{DateTime, Duration, Utc};
use serde::Serialize;

use crate::record::UsageRecord;

/// Usage at one moment, measured against the limit.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Snapshot {
    pub used_tokens: u64,
    pub percent: f64,
    /// The next whole percent above the current usage, such as 54 when at 53.4%.
    pub next_whole_percent: Option<u32>,
    /// Minutes until usage reaches `next_whole_percent` at the recent pace. `None` without recent activity.
    pub minutes_to_next_percent: Option<f64>,
    pub tokens_per_minute: f64,
}

/// Measures usage against a limit over a rolling window, and estimates the time to the next whole percent.
#[derive(Debug, Clone, Copy)]
pub struct Estimator {
    limit_tokens: u64,
    window: Duration,
    pace_window: Duration,
}

impl Estimator {
    /// Returns `None` when the limit or either span is not positive.
    pub fn new(limit_tokens: u64, window: Duration, pace_window: Duration) -> Option<Self> {
        if limit_tokens == 0 || window <= Duration::zero() || pace_window <= Duration::zero() {
            return None;
        }
        Some(Self {
            limit_tokens,
            window,
            pace_window,
        })
    }

    pub fn snapshot(&self, records: &[UsageRecord], now: DateTime<Utc>) -> Snapshot {
        let window_start = now - self.window;
        let in_window: Vec<&UsageRecord> = records
            .iter()
            .filter(|r| r.timestamp >= window_start && r.timestamp <= now)
            .collect();

        let used: u64 = in_window.iter().map(|r| r.tokens).sum();
        let percent = used as f64 / self.limit_tokens as f64 * 100.0;

        let pace_start = now - self.pace_window;
        let recent: u64 = in_window
            .iter()
            .filter(|r| r.timestamp >= pace_start)
            .map(|r| r.tokens)
            .sum();
        let tokens_per_minute = recent as f64 / (self.pace_window.num_seconds() as f64 / 60.0);

        if percent >= 100.0 {
            return Snapshot {
                used_tokens: used,
                percent,
                next_whole_percent: None,
                minutes_to_next_percent: None,
                tokens_per_minute,
            };
        }

        let next_whole_percent = percent.floor() as u32 + 1;
        let tokens_at_next = next_whole_percent as f64 / 100.0 * self.limit_tokens as f64;
        let tokens_remaining = tokens_at_next - used as f64;
        let minutes_to_next_percent =
            (tokens_per_minute > 0.0).then(|| tokens_remaining / tokens_per_minute);

        Snapshot {
            used_tokens: used,
            percent,
            next_whole_percent: Some(next_whole_percent),
            minutes_to_next_percent,
            tokens_per_minute,
        }
    }
}

/// Works out a user's limit from the percent Claude shows them for the same window.
///
/// For example, 4,000,000 tokens at 40% means a limit of 10,000,000. Returns `None`
/// when there is no usage yet or the percent is not above 0 and at most 100.
pub fn limit_from_percent(used_tokens: u64, percent: f64) -> Option<u64> {
    if used_tokens == 0 || !(percent > 0.0 && percent <= 100.0) {
        return None;
    }
    let limit = (used_tokens as f64 / (percent / 100.0)).round();
    Some(limit as u64)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn now() -> DateTime<Utc> {
        "2026-10-29T12:00:00Z".parse().expect("valid test time")
    }

    fn record(id: &str, minutes_ago: i64, tokens: u64) -> UsageRecord {
        UsageRecord {
            request_id: id.to_string(),
            timestamp: now() - Duration::minutes(minutes_ago),
            tokens,
        }
    }

    fn estimator() -> Estimator {
        Estimator::new(1_000, Duration::hours(5), Duration::minutes(30)).expect("valid estimator")
    }

    #[test]
    fn reports_percent_and_time_to_the_next_whole_percent() {
        // 530 used in the window (53%). 330 of them in the last 30 minutes.
        let records = vec![record("old", 180, 200), record("recent", 10, 330)];

        let snapshot = estimator().snapshot(&records, now());

        assert_eq!(snapshot.used_tokens, 530);
        assert!((snapshot.percent - 53.0).abs() < 1e-9);
        assert_eq!(snapshot.next_whole_percent, Some(54));
        // 10 more tokens reach 54% at 11 tokens per minute.
        let minutes = snapshot
            .minutes_to_next_percent
            .expect("recent pace gives an estimate");
        assert!((minutes - 10.0 / (330.0 / 30.0)).abs() < 1e-9);
    }

    #[test]
    fn ignores_records_outside_the_window() {
        let records = vec![record("expired", 6 * 60, 900)];

        let snapshot = estimator().snapshot(&records, now());

        assert_eq!(snapshot.used_tokens, 0);
        assert_eq!(snapshot.minutes_to_next_percent, None);
    }

    #[test]
    fn gives_no_time_estimate_without_recent_activity() {
        let records = vec![record("earlier", 120, 500)];

        let snapshot = estimator().snapshot(&records, now());

        assert!((snapshot.percent - 50.0).abs() < 1e-9);
        assert_eq!(snapshot.minutes_to_next_percent, None);
    }

    #[test]
    fn stops_estimating_once_the_limit_is_reached() {
        let records = vec![record("full", 5, 1_000)];

        let snapshot = estimator().snapshot(&records, now());

        assert_eq!(snapshot.next_whole_percent, None);
        assert_eq!(snapshot.minutes_to_next_percent, None);
    }

    #[test]
    fn limit_is_used_tokens_divided_by_the_shown_percent() {
        assert_eq!(limit_from_percent(4_000_000, 40.0), Some(10_000_000));
        assert_eq!(limit_from_percent(53, 100.0), Some(53));
    }

    #[test]
    fn no_limit_without_usage_or_with_an_out_of_range_percent() {
        assert_eq!(limit_from_percent(0, 40.0), None);
        assert_eq!(limit_from_percent(100, 0.0), None);
        assert_eq!(limit_from_percent(100, 150.0), None);
    }

    #[test]
    fn rejects_a_zero_limit() {
        assert!(Estimator::new(0, Duration::hours(5), Duration::minutes(30)).is_none());
    }
}
