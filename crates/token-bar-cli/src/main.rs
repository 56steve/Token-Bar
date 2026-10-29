use std::path::Path;
use std::process::ExitCode;

use chrono::{Duration, Utc};
use token_bar_core::{
    default_config_path, default_logs_root, load_config, save_config, scan_records, Estimator,
    Snapshot,
};

const PACE_WINDOW_MINUTES: i64 = 30;

const USAGE: &str = "\
Usage:
  token-bar                  Show usage against your limit
  token-bar --json           Same, as JSON for scripts and plugins
  token-bar calibrate <n>    Set your plan's limit, in tokens
  token-bar help             Show this help";

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
        [] => status(&config_path, false),
        ["--json"] => status(&config_path, true),
        ["calibrate", tokens] => calibrate(&config_path, tokens),
        ["help" | "--help" | "-h"] => Ok(USAGE.to_string()),
        _ => Err(format!("unknown command\n\n{USAGE}")),
    }
}

fn status(config_path: &Path, json: bool) -> Result<String, String> {
    let config = load_config(config_path);
    let limit = config
        .limit_tokens
        .ok_or("no limit set yet. Run: token-bar calibrate <tokens>")?;
    let window = Duration::milliseconds((config.window_hours * 3_600_000.0) as i64);
    let estimator = Estimator::new(limit, window, Duration::minutes(PACE_WINDOW_MINUTES))
        .ok_or("the limit must be above zero")?;
    let root = default_logs_root().ok_or("could not find your home folder")?;

    let now = Utc::now();
    let records = scan_records(&root, now - window);
    let snapshot = estimator.snapshot(&records, now);

    if json {
        serde_json::to_string_pretty(&snapshot).map_err(|error| error.to_string())
    } else {
        Ok(render(&snapshot, config.window_hours, records.is_empty()))
    }
}

fn calibrate(config_path: &Path, tokens: &str) -> Result<String, String> {
    let limit: u64 = tokens
        .replace(',', "")
        .parse()
        .map_err(|_| format!("'{tokens}' is not a whole number of tokens"))?;
    if limit == 0 {
        return Err("the limit must be above zero".to_string());
    }

    let mut config = load_config(config_path);
    config.limit_tokens = Some(limit);
    save_config(config_path, &config).map_err(|error| format!("could not save config: {error}"))?;
    Ok(format!("Limit set to {limit} tokens."))
}

fn render(snapshot: &Snapshot, window_hours: f64, no_records: bool) -> String {
    let mut lines = vec![format!(
        "Claude usage   {}%  of your {window_hours}-hour window",
        snapshot.percent.floor() as u64
    )];

    match (
        snapshot.next_whole_percent,
        snapshot.minutes_to_next_percent,
    ) {
        (_, _) if snapshot.percent >= 100.0 => lines.push("Limit reached.".to_string()),
        (Some(next), Some(minutes)) => {
            lines.push(format!(
                "Next point    {next}% in about {} min",
                minutes.round() as u64
            ));
        }
        (Some(next), None) => {
            lines.push(format!(
                "Next point    {next}%, no recent activity to estimate from"
            ));
        }
        (None, _) => {}
    }

    lines.push(format!(
        "Pace          {} tokens per minute, last {PACE_WINDOW_MINUTES} min",
        snapshot.tokens_per_minute.round() as u64
    ));

    if no_records {
        lines.push(format!(
            "No Claude Code usage found in the last {window_hours} hours."
        ));
    }
    lines.join("\n")
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
    fn renders_percent_next_point_and_pace() {
        let output = render(&snapshot(53.4, Some(54), Some(9.2)), 5.0, false);

        assert!(output.contains("Claude usage   53%  of your 5-hour window"));
        assert!(output.contains("Next point    54% in about 9 min"));
        assert!(output.contains("Pace          11 tokens per minute, last 30 min"));
    }

    #[test]
    fn says_when_there_is_no_recent_activity() {
        let output = render(&snapshot(50.0, Some(51), None), 5.0, false);

        assert!(output.contains("no recent activity to estimate from"));
    }

    #[test]
    fn reports_a_reached_limit() {
        let output = render(&snapshot(100.0, None, None), 5.0, false);

        assert!(output.contains("Limit reached."));
    }

    #[test]
    fn explains_when_no_logs_were_found() {
        let output = render(&snapshot(0.0, Some(1), None), 5.0, true);

        assert!(output.contains("No Claude Code usage found in the last 5 hours."));
    }

    #[test]
    fn calibrate_saves_the_limit_and_accepts_commas() {
        let dir = std::env::temp_dir().join(format!("token-bar-cli-{}", std::process::id()));
        let path = dir.join("config.json");

        let message = calibrate(&path, "5,000,000").expect("valid limit");

        assert_eq!(message, "Limit set to 5000000 tokens.");
        assert_eq!(load_config(&path).limit_tokens, Some(5_000_000));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn calibrate_rejects_non_numbers_and_zero() {
        let path = std::env::temp_dir().join("token-bar-cli-never-written.json");

        assert!(calibrate(&path, "lots").is_err());
        assert!(calibrate(&path, "0").is_err());
    }
}
