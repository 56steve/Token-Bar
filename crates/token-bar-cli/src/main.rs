use std::io::{self, BufRead, IsTerminal, Write};
use std::path::Path;
use std::process::ExitCode;

use chrono::{Duration, Utc};
use serde_json::{Map, Value};
use token_bar_core::{
    default_config_path, default_logs_root, limit_from_percent, load_config, save_config,
    scan_records, Config, Estimator, Snapshot, UsageRecord,
};

const PACE_WINDOW_MINUTES: i64 = 30;
const WEEK_HOURS: f64 = 7.0 * 24.0;

/// Below this percent the estimate rests on little usage, so it is marked as rough.
const ROUGH_ESTIMATE_BELOW_PERCENT: f64 = 10.0;

const USAGE: &str = "\
Usage:
  token-bar                    Show usage against both limits
  token-bar --json             Same, as JSON for scripts and plugins
  token-bar calibrate          Set both limits from the percents Claude shows you
  token-bar calibrate <n>      Set the 5-hour limit from the percent shown, such as 40
  token-bar calibrate --weekly <n>
                               Set the weekly limit from the percent shown, such as 78
  token-bar calibrate --tokens <n>
                               Set the 5-hour limit directly, in tokens
  token-bar help               Show this help";

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

    fn set(self, config: &mut Config, limit: u64) {
        match self {
            Limit::FiveHour => config.limit_tokens = Some(limit),
            Limit::Weekly => config.weekly_limit_tokens = Some(limit),
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
        ["calibrate", "--weekly", percent] => {
            calibrate_percent(&config_path, Limit::Weekly, percent)
        }
        ["calibrate", "--tokens", tokens] => calibrate_tokens(&config_path, tokens),
        ["calibrate", percent] => calibrate_percent(&config_path, Limit::FiveHour, percent),
        ["help" | "--help" | "-h"] => Ok(USAGE.to_string()),
        _ => Err(format!("unknown command\n\n{USAGE}")),
    }
}

/// On first run in a terminal, asks for the percents once. Scripts and pipes never get a prompt.
fn status_or_setup(config_path: &Path, json: bool) -> Result<String, String> {
    let config = load_config(config_path);
    let needs_setup = LIMITS
        .iter()
        .all(|limit| limit.configured(&config).is_none());
    if needs_setup && !json && io::stdin().is_terminal() {
        let setup = calibrate_interactive(config_path)?;
        if LIMITS
            .iter()
            .all(|limit| limit.configured(&load_config(config_path)).is_none())
        {
            return Ok(setup);
        }
        return Ok(format!("{setup}\n\n{}", status(config_path, false)?));
    }
    status(config_path, json)
}

fn status(config_path: &Path, json: bool) -> Result<String, String> {
    let config = load_config(config_path);
    let root = default_logs_root().ok_or("could not find your home folder")?;
    let now = Utc::now();

    let longest = LIMITS
        .iter()
        .filter(|limit| limit.configured(&config).is_some())
        .map(|limit| limit.hours(&config))
        .fold(0.0_f64, f64::max);
    if longest == 0.0 {
        return Err("no limit set yet. Run: token-bar calibrate".to_string());
    }
    let records = scan_records(&root, now - hours_to_duration(longest));

    let mut snapshots: Vec<(Limit, Snapshot)> = Vec::new();
    for limit in LIMITS {
        let Some(snapshot) = snapshot_for(&config, limit, &records, now)? else {
            continue;
        };
        snapshots.push((limit, snapshot));
    }

    if json {
        let mut object = Map::new();
        for (limit, snapshot) in &snapshots {
            let value = serde_json::to_value(snapshot).map_err(|error| error.to_string())?;
            object.insert(limit.key().to_string(), value);
        }
        return serde_json::to_string_pretty(&Value::Object(object))
            .map_err(|error| error.to_string());
    }

    let mut blocks: Vec<String> = snapshots
        .iter()
        .map(|(limit, snapshot)| render_block(*limit, snapshot))
        .collect();
    if records.is_empty() {
        blocks.push(format!(
            "No Claude Code usage found in the last {longest} hours."
        ));
    }
    Ok(blocks.join("\n\n"))
}

fn snapshot_for(
    config: &Config,
    limit: Limit,
    records: &[UsageRecord],
    now: chrono::DateTime<Utc>,
) -> Result<Option<Snapshot>, String> {
    let Some(limit_tokens) = limit.configured(config) else {
        return Ok(None);
    };
    let estimator = Estimator::new(
        limit_tokens,
        hours_to_duration(limit.hours(config)),
        Duration::minutes(PACE_WINDOW_MINUTES),
    )
    .ok_or("the limit must be above zero")?;
    Ok(Some(estimator.snapshot(records, now)))
}

fn render_block(limit: Limit, snapshot: &Snapshot) -> String {
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

    lines.push(format!(
        "  Pace         {} tokens per minute, last {PACE_WINDOW_MINUTES} min",
        snapshot.tokens_per_minute.round() as u64
    ));
    lines.join("\n")
}

/// Asks for both percents Claude shows. Pressing Enter skips a question, and it is asked again next time.
fn calibrate_interactive(config_path: &Path) -> Result<String, String> {
    println!("Set up token-bar once. Check the usage percentages Claude shows you right now.");
    let five_hour = ask("5-hour percent (for example 40), or press Enter to skip: ")?;
    let weekly = ask("Weekly percent (for example 78), or press Enter to skip: ")?;

    let mut messages = Vec::new();
    if let Some(percent) = five_hour {
        messages.push(calibrate_percent(config_path, Limit::FiveHour, &percent)?);
    }
    if let Some(percent) = weekly {
        messages.push(calibrate_percent(config_path, Limit::Weekly, &percent)?);
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

fn calibrate_percent(config_path: &Path, limit: Limit, text: &str) -> Result<String, String> {
    let percent = parse_percent(text)?;
    let mut config = load_config(config_path);
    let root = default_logs_root().ok_or("could not find your home folder")?;
    let now = Utc::now();
    let window = hours_to_duration(limit.hours(&config));
    let used: u64 = scan_records(&root, now - window)
        .iter()
        .map(|record| record.tokens)
        .sum();
    let limit_tokens = limit_from_percent(used, percent).ok_or(
        "no Claude Code usage in the window yet, so there is nothing to calibrate against. Use Claude a little, then try again.",
    )?;

    limit.set(&mut config, limit_tokens);
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
    Limit::FiveHour.set(&mut config, limit);
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
    fn renders_a_block_with_percent_next_point_and_pace() {
        let output = render_block(Limit::FiveHour, &snapshot(53.4, Some(54), Some(9.2)));

        assert!(output.contains("5-hour usage   53%"));
        assert!(output.contains("  Next point   54% in about 9 min"));
        assert!(output.contains("  Pace         11 tokens per minute, last 30 min"));
    }

    #[test]
    fn labels_the_weekly_block() {
        let output = render_block(Limit::Weekly, &snapshot(78.0, Some(79), Some(600.0)));

        assert!(output.starts_with("Weekly usage   78%"));
    }

    #[test]
    fn says_when_there_is_no_recent_activity() {
        let output = render_block(Limit::FiveHour, &snapshot(50.0, Some(51), None));

        assert!(output.contains("no recent activity to estimate from"));
    }

    #[test]
    fn reports_a_reached_limit() {
        let output = render_block(Limit::Weekly, &snapshot(100.0, None, None));

        assert!(output.contains("Limit reached."));
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
