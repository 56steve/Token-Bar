use std::fs;
use std::path::{Path, PathBuf};

use chrono::{DateTime, TimeZone, Utc};
use serde::Deserialize;

/// Claude's own reading for one limit: the percent used and when it resets.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct LiveWindow {
    pub used_percentage: f64,
    /// Unix time in seconds, as Claude Code reports it.
    pub resets_at: i64,
}

impl LiveWindow {
    pub fn resets_at_utc(&self) -> Option<DateTime<Utc>> {
        Utc.timestamp_opt(self.resets_at, 0).single()
    }
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
struct RawLive {
    rate_limits: RawRateLimits,
    saved_at: i64,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
struct RawRateLimits {
    five_hour: Option<LiveWindow>,
    seven_day: Option<LiveWindow>,
}

/// The rate-limit readings the status line saved from Claude Code.
#[derive(Debug, Clone, PartialEq)]
pub struct LiveLimits {
    pub five_hour: Option<LiveWindow>,
    pub seven_day: Option<LiveWindow>,
    /// When Claude Code last reported these readings.
    pub saved_at: DateTime<Utc>,
}

/// `<config dir>/token-bar/rate_limits.json`, written by the Claude Code status line.
pub fn default_live_path() -> Option<PathBuf> {
    dirs::config_dir().map(|dir| dir.join("token-bar").join("rate_limits.json"))
}

/// Parses the saved readings. Returns `None` when the text is not in the expected shape.
pub fn parse_live(text: &str) -> Option<LiveLimits> {
    let raw: RawLive = serde_json::from_str(text).ok()?;
    Some(LiveLimits {
        five_hour: raw.rate_limits.five_hour,
        seven_day: raw.rate_limits.seven_day,
        saved_at: Utc.timestamp_opt(raw.saved_at, 0).single()?,
    })
}

/// Reads the saved readings, or `None` when the file is missing or unreadable.
pub fn load_live(path: &Path) -> Option<LiveLimits> {
    parse_live(&fs::read_to_string(path).ok()?)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"{"rate_limits":{"five_hour":{"used_percentage":21.4,"resets_at":1793336400},"seven_day":{"used_percentage":78.2,"resets_at":1793475000}},"saved_at":1793259701}"#;

    #[test]
    fn reads_both_windows_with_their_reset_times() {
        let live = parse_live(SAMPLE).expect("valid sample");

        let five = live.five_hour.expect("five-hour reading");
        assert!((five.used_percentage - 21.4).abs() < 1e-9);
        assert_eq!(
            five.resets_at_utc().map(|t| t.timestamp()),
            Some(1_793_336_400)
        );
        let week = live.seven_day.expect("weekly reading");
        assert!((week.used_percentage - 78.2).abs() < 1e-9);
        assert_eq!(live.saved_at.timestamp(), 1_793_259_701);
    }

    #[test]
    fn a_missing_window_is_none_not_an_error() {
        let text = r#"{"rate_limits":{"seven_day":{"used_percentage":10.0,"resets_at":1793475000}},"saved_at":1793259701}"#;

        let live = parse_live(text).expect("valid sample");

        assert_eq!(live.five_hour, None);
        assert!(live.seven_day.is_some());
    }

    #[test]
    fn rejects_text_in_the_wrong_shape() {
        assert!(parse_live("{not json").is_none());
        assert!(parse_live(r#"{"something":"else"}"#).is_none());
    }

    #[test]
    fn a_missing_file_gives_none() {
        let path = std::env::temp_dir().join("token-bar-live-does-not-exist.json");

        assert!(load_live(&path).is_none());
    }
}
