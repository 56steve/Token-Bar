use std::io::{self, BufRead, IsTerminal, Write};
use std::path::Path;
use std::process::ExitCode;

use chrono::{DateTime, Duration, Utc};
use serde_json::{json, Map, Value};
use token_bar_core::{
    default_config_path, default_logs_root, limit_from_percent, load_config, next_reset,
    save_config, scan_records, window_start, Config, Estimator, Snapshot,
};

const PACE_WINDOW_MINUTES: i64 = 30;
const WEEK_HOURS: f64 = 7.0 * 24.0;

/// Below this percent the estimate rests on little usage, so it is marked as rough.
const ROUGH_ESTIMATE_BELOW_PERCENT: f64 = 10.0;

const USAGE: &str = "\
Usage:
  token-bar                              Show usage against both limits
  token-bar --json                       Same, as JSON for scripts and plugins
  token-bar calibrate                    Set both limits, asking for each percent and reset time
  token-bar calibrate <percent> \"<reset>\"
                                         Set the 5-hour limit, such as 21 \"2:20 PM\"
  token-bar calibrate --weekly <percent> \"<reset>\"
                                         Set the weekly limit, such as 78 \"Sun 5:30 AM\"
  token-bar calibrate --tokens <n>       Set the 5-hour limit directly, in tokens
  token-bar help                         Show this help";

/// One of the two limits Claude enforces.
#[derive(Clone, Copy)]
enum Limit {
    FiveHour,
    Weekly,
}

impl Limit {
    fn label(self) -> &'static str {
        match self {
            Limit::FiveHour => "5-hour",
            Limit::Weekly => "Weekly",
        }
    }

    fn key(self) -> &'static str {
        match self {
            Limit::FiveHour => "five_hour",
            Limit::Weekly => "weekly",
        }
    }

    fn example_percent(self) -> &'static str {
        match self {
            Limit::FiveHour => "40",
            Limit::Weekly => "78",
        }
    }

    fn example_reset(self) -> &'static str {
        match self {
            Limit::FiveHour => "2:20 PM",
            Limit::Weekly => "Sun 5:30 AM",
        }
    }

    fn hours(self, config: &Config) -> f64 {
        match self {
            Limit::FiveHour => config.window_hours,
            Limit::Weekly => WEEK_HOURS,
        }
    }

    fn configured(self, config: &Config) -> Option<u64> {
        match self {
            Limit::FiveHour => config.limit_tokens,
            Limit::Weekly => config.weekly_limit_tokens,
        }
    }

    fn reset(self, config: &Config) -> Option<DateTime<Utc>> {
        match self {
            Limit::FiveHour => config.five_hour_reset,
            Limit::Weekly => config.weekly_reset,
        }
    }

    fn set_limit(self, config: &mut Config, limit: u64) {
        match self {
            Limit::FiveHour => config.limit_tokens = Some(limit),
            Limit::Weekly => config.weekly_limit_tokens = Some(limit),
        }
    }

    fn set_reset(self, config: &mut Config, reset: DateTime<Utc>) {
        match self {
            Limit::FiveHour => config.five_hour_reset = Some(reset),
            Limit::Weekly => config.weekly_reset = Some(reset),
        }
    }
}

const LIMITS: [Limit; 2] = [Limit::FiveHour, Limit::Weekly];

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(output) => {
            println!("{output}");
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("token-bar: {message}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: &[String]) -> Result<String, String> {
    let config_path =
        default_config_path().ok_or("could not find a config folder for this user")?;
    let arguments: Vec<&str> = args.iter().map(String::as_str).collect();
    match arguments.as_slice() {
        [] => status_or_setup(&config_path, false),
        ["--json"] => status(&config_path, true),
        ["calibrate"] => calibrate_interactive(&config_path),
        ["calibrate", "--tokens", tokens] => calibrate_tokens(&config_path, tokens),
        ["calibrate", "--weekly", percent] => {
            calibrate_percent(&config_path, Limit::Weekly, percent, None)
        }
        ["calibrate", "--weekly", percent, reset] => {
            calibrate_percent(&config_path, Limit::Weekly, percent, Some(reset))
        }
        ["calibrate", percent] => calibrate_percent(&config_path, Limit::FiveHour, percent, None),
        ["calibrate", percent, reset] => {
            calibrate_percent(&config_path, Limit::FiveHour, percent, Some(reset))
        }
        ["help" | "--help" | "-h"] => Ok(USAGE.to_string()),
        _ => Err(format!("unknown command\n\n{USAGE}")),
    }
}

/// On first run in a terminal, asks for the percents once. Scripts and pipes never get a prompt.
fn status_or_setup(config_path: &Path, json: bool) -> Result<String, String> {
    let needs_setup = LIMITS
        .iter()
        .all(|limit| limit.configured(&load_config(config_path)).is_none());
    if needs_setup && !json && io::stdin().is_terminal() {
        let setup = calibrate_interactive(config_path)?;
        let still_unset = LIMITS
            .iter()
            .all(|limit| limit.configured(&load_config(config_path)).is_none());
        if still_unset {
            return Ok(setup);
        }
        return Ok(format!("{setup}\n\n{}", status(config_path, false)?));
    }
    status(config_path, json)
}

/// Start of the window that is current now. Counted from the last reset when one is known,
/// otherwise from a rolling point in the past.
fn current_window_start(limit: Limit, config: &Config, now: DateTime<Utc>) -> DateTime<Utc> {
    let period = hours_to_duration(limit.hours(config));
    match limit.reset(config) {
        Some(anchor) => window_start(anchor, period, now),
        None => now - period,
    }
}

fn status(config_path: &Path, json: bool) -> Result<String, String> {
    let config = load_config(config_path);
    let root = default_logs_root().ok_or("could not find your home folder")?;
    let now = Utc::now();

    let configured: Vec<Limit> = LIMITS
        .into_iter()
        .filter(|limit| limit.configured(&config).is_some())
        .collect();
    let longest = configured
        .iter()
        .map(|limit| limit.hours(&config))
        .fold(0.0_f64, f64::max);
    if configured.is_empty() {
        return Err("no limit set yet. Run: token-bar calibrate".to_string());
    }
    let records = scan_records(&root, now - hours_to_duration(longest));

    let mut reports = Vec::new();
    for limit in configured {
        let Some(limit_tokens) = limit.configured(&config) else {
            continue;
        };
        let period = hours_to_duration(limit.hours(&config));
        let start = current_window_start(limit, &config, now);
        let estimator =
            Estimator::new(limit_tokens, period, Duration::minutes(PACE_WINDOW_MINUTES))
                .ok_or("the limit must be above zero")?;
        let snapshot = estimator.snapshot_since(&records, start, now);
        let resets_at = limit.reset(&config).map(|_| start + period);
        reports.push((limit, snapshot, resets_at));
    }

    if json {
        let mut object = Map::new();
        for (limit, snapshot, resets_at) in &reports {
            let mut value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
            if let Some(map) = value.as_object_mut() {
                map.insert(
                    "resets_at".to_string(),
                    resets_at.map_or(Value::Null, |at| json!(at.to_rfc3339())),
                );
            }
            object.insert(limit.key().to_string(), value);
        }
        return serde_json::to_string_pretty(&Value::Object(object))
            .map_err(|error| error.to_string());
    }

    let mut blocks: Vec<String> = reports
        .iter()
        .map(|(limit, snapshot, resets_at)| {
            let resets_in = resets_at.map(|at| format_remaining(at - now));
            render_block(*limit, snapshot, resets_in.as_deref())
        })
        .collect();
    if records.is_empty() {
        blocks.push(format!(
            "No Claude Code usage found in the last {longest} hours."
        ));
    }
    Ok(blocks.join("\n\n"))
}

fn calibrate_interactive(config_path: &Path) -> Result<String, String> {
    println!("Set up token-bar once. Check the usage Claude shows you right now.");
    let mut messages = Vec::new();
    for limit in LIMITS {
        let percent_question = format!(
            "{} percent (for example {}), or press Enter to skip: ",
            limit.label(),
            limit.example_percent()
        );
        let Some(percent) = ask(&percent_question)? else {
            continue;
        };
        let reset_question = format!(
            "{} reset time (for example \"{}\"): ",
            limit.label(),
            limit.example_reset()
        );
        let reset = ask(&reset_question)?;
        let message = calibrate_percent(config_path, limit, &percent, reset.as_deref())
            .unwrap_or_else(|error| error);
        messages.push(message);
    }
    if messages.is_empty() {
        return Ok("Skipped. Run token-bar calibrate when you're ready.".to_string());
    }
    Ok(messages.join("\n"))
}

fn ask(question: &str) -> Result<Option<String>, String> {
    print!("{question}");
    io::stdout().flush().map_err(|error| error.to_string())?;

    let mut answer = String::new();
    io::stdin()
        .lock()
        .read_line(&mut answer)
        .map_err(|error| error.to_string())?;
    let trimmed = answer.trim();
    Ok(if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    })
}

fn calibrate_percent(
    config_path: &Path,
    limit: Limit,
    percent_text: &str,
    reset_text: Option<&str>,
) -> Result<String, String> {
    let percent = parse_percent(percent_text)?;
    let mut config = load_config(config_path);
    let now = Utc::now();

    match reset_text {
        Some(text) => {
            let reset = next_reset(text, now).ok_or_else(|| {
                format!(
                    "'{text}' is not a reset time. Try something like \"{}\".",
                    limit.example_reset()
                )
            })?;
            limit.set_reset(&mut config, reset);
        }
        None if limit.reset(&config).is_none() => {
            return Err(format!(
                "the {} limit also needs its reset time, such as \"{}\".",
                limit.label().to_lowercase(),
                limit.example_reset()
            ));
        }
        None => {}
    }

    let root = default_logs_root().ok_or("could not find your home folder")?;
    let start = current_window_start(limit, &config, now);
    let used: u64 = scan_records(&root, start)
        .iter()
        .filter(|record| record.timestamp <= now)
        .map(|record| record.tokens)
        .sum();
    let limit_tokens = limit_from_percent(used, percent).ok_or(
        "no Claude Code usage in this window yet, so there is nothing to calibrate against. Use Claude a little, then try again.",
    )?;

    limit.set_limit(&mut config, limit_tokens);
    save_config(config_path, &config).map_err(|error| format!("could not save config: {error}"))?;

    let mut message = format!(
        "Calibrated. Your {} limit is about {limit_tokens} tokens.",
        limit.label().to_lowercase()
    );
    if percent < ROUGH_ESTIMATE_BELOW_PERCENT {
        message.push_str(
            " The estimate is rough at this usage. Calibrate again after more use for accuracy.",
        );
    }
    Ok(message)
}

fn calibrate_tokens(config_path: &Path, tokens: &str) -> Result<String, String> {
    let limit: u64 = tokens
        .replace(',', "")
        .parse()
        .map_err(|_| format!("'{tokens}' is not a whole number of tokens"))?;
    if limit == 0 {
        return Err("the limit must be above zero".to_string());
    }
    let mut config = load_config(config_path);
    Limit::FiveHour.set_limit(&mut config, limit);
    save_config(config_path, &config).map_err(|error| format!("could not save config: {error}"))?;
    Ok(format!("Limit set to {limit} tokens."))
}

fn parse_percent(text: &str) -> Result<f64, String> {
    let percent: f64 = text
        .trim()
        .trim_end_matches('%')
        .trim()
        .parse()
        .map_err(|_| format!("'{text}' is not a percentage"))?;
    if percent > 0.0 && percent <= 100.0 {
        Ok(percent)
    } else {
        Err(format!("'{text}' must be above 0 and at most 100"))
    }
}

fn format_remaining(remaining: Duration) -> String {
    let minutes = remaining.num_minutes().max(0);
    let days = minutes / (24 * 60);
    let hours = (minutes % (24 * 60)) / 60;
    let mins = minutes % 60;
    if days > 0 {
        format!("{days}d {hours}h")
    } else if hours > 0 {
        format!("{hours}h {mins}m")
    } else {
        format!("{mins} min")
    }
}

fn render_block(limit: Limit, snapshot: &Snapshot, resets_in: Option<&str>) -> String {
    let mut lines = vec![format!(
        "{} usage   {}%",
        limit.label(),
        snapshot.percent.floor() as u64
    )];

    match (
        snapshot.next_whole_percent,
        snapshot.minutes_to_next_percent,
    ) {
        (_, _) if snapshot.percent >= 100.0 => lines.push("  Limit reached.".to_string()),
        (Some(next), Some(minutes)) => lines.push(format!(
            "  Next point   {next}% in about {} min",
            minutes.round() as u64
        )),
        (Some(next), None) => lines.push(format!(
            "  Next point   {next}%, no recent activity to estimate from"
        )),
        (None, _) => {}
    }

    if let Some(remaining) = resets_in {
        lines.push(format!("  Resets       in {remaining}"));
    }

    lines.push(format!(
        "  Pace         {} tokens per minute, last {PACE_WINDOW_MINUTES} min",
        snapshot.tokens_per_minute.round() as u64
    ));
    lines.join("\n")
}

fn hours_to_duration(hours: f64) -> Duration {
    Duration::milliseconds((hours * 3_600_000.0) as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snapshot(percent: f64, next: Option<u32>, minutes: Option<f64>) -> Snapshot {
        Snapshot {
            used_tokens: 530,
            percent,
            next_whole_percent: next,
            minutes_to_next_percent: minutes,
            tokens_per_minute: 11.0,
        }
    }

    #[test]
    fn renders_a_block_with_percent_next_point_reset_and_pace() {
        let output = render_block(
            Limit::FiveHour,
            &snapshot(53.4, Some(54), Some(9.2)),
            Some("1h 50m"),
        );

        assert!(output.contains("5-hour usage   53%"));
        assert!(output.contains("  Next point   54% in about 9 min"));
        assert!(output.contains("  Resets       in 1h 50m"));
        assert!(output.contains("  Pace         11 tokens per minute, last 30 min"));
    }

    #[test]
    fn omits_the_reset_line_for_a_rolling_window() {
        let output = render_block(Limit::FiveHour, &snapshot(40.0, Some(41), Some(5.0)), None);

        assert!(!output.contains("Resets"));
    }

    #[test]
    fn labels_the_weekly_block() {
        let output = render_block(Limit::Weekly, &snapshot(78.0, Some(79), Some(600.0)), None);

        assert!(output.starts_with("Weekly usage   78%"));
    }

    #[test]
    fn reports_a_reached_limit() {
        let output = render_block(Limit::Weekly, &snapshot(100.0, None, None), None);

        assert!(output.contains("Limit reached."));
    }

    #[test]
    fn formats_time_until_reset_in_the_largest_useful_units() {
        assert_eq!(format_remaining(Duration::minutes(45)), "45 min");
        assert_eq!(format_remaining(Duration::minutes(110)), "1h 50m");
        assert_eq!(format_remaining(Duration::hours(50)), "2d 2h");
    }

    #[test]
    fn parses_percentages_with_or_without_a_sign() {
        assert_eq!(parse_percent("40"), Ok(40.0));
        assert_eq!(parse_percent(" 40% "), Ok(40.0));
        assert_eq!(parse_percent("100"), Ok(100.0));
    }

    #[test]
    fn rejects_percentages_outside_zero_to_hundred_and_non_numbers() {
        assert!(parse_percent("0").is_err());
        assert!(parse_percent("150").is_err());
        assert!(parse_percent("lots").is_err());
    }

    #[test]
    fn a_limit_without_a_reset_time_is_refused_before_anything_is_saved() {
        let path = std::env::temp_dir().join("token-bar-cli-no-reset-never-written.json");

        let result = calibrate_percent(&path, Limit::Weekly, "78", None);

        assert!(result.is_err());
        assert!(!path.exists());
    }

    #[test]
    fn tokens_calibration_saves_the_five_hour_limit_and_accepts_commas() {
        let dir = std::env::temp_dir().join(format!("token-bar-cli-{}", std::process::id()));
        let path = dir.join("config.json");

        let message = calibrate_tokens(&path, "5,000,000").expect("valid limit");

        assert_eq!(message, "Limit set to 5000000 tokens.");
        assert_eq!(load_config(&path).limit_tokens, Some(5_000_000));
        assert_eq!(load_config(&path).weekly_limit_tokens, None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn tokens_calibration_rejects_non_numbers_and_zero() {
        let path = std::env::temp_dir().join("token-bar-cli-never-written.json");

        assert!(calibrate_tokens(&path, "lots").is_err());
        assert!(calibrate_tokens(&path, "0").is_err());
    }
}
