use std::io::{self, BufRead, IsTerminal, Write};
use std::path::Path;
use std::process::ExitCode;

use chrono::{Duration, Utc};
use token_bar_core::{
    default_config_path, default_logs_root, limit_from_percent, load_config, save_config,
    scan_records, Config, Estimator, Snapshot,
};

const PACE_WINDOW_MINUTES: i64 = 30;

/// Below this percent the estimate rests on little usage, so it is marked as rough.
const ROUGH_ESTIMATE_BELOW_PERCENT: f64 = 10.0;

const USAGE: &str = "\
Usage:
  token-bar                    Show usage against your limit
  token-bar --json             Same, as JSON for scripts and plugins
  token-bar calibrate          Set your limit from the percent Claude shows you
  token-bar calibrate <n>      Same, with the percent as an argument, such as 40
  token-bar calibrate --tokens <n>
                               Set the limit directly, in tokens
  token-bar help               Show this help";

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
        ["calibrate", percent] => calibrate_percent(&config_path, percent),
        ["help" | "--help" | "-h"] => Ok(USAGE.to_string()),
        _ => Err(format!("unknown command\n\n{USAGE}")),
    }
}

/// On first run in a terminal, asks for the percent once. Scripts and pipes never get a prompt.
fn status_or_setup(config_path: &Path, json: bool) -> Result<String, String> {
    let needs_setup = load_config(config_path).limit_tokens.is_none();
    if needs_setup && !json && io::stdin().is_terminal() {
        let setup = calibrate_interactive(config_path)?;
        if load_config(config_path).limit_tokens.is_none() {
            return Ok(setup);
        }
        return Ok(format!("{setup}\n\n{}", status(config_path, false)?));
    }
    status(config_path, json)
}

fn status(config_path: &Path, json: bool) -> Result<String, String> {
    let config = load_config(config_path);
    let limit = config
        .limit_tokens
        .ok_or("no limit set yet. Run: token-bar calibrate")?;
    let window = window_for(&config);
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

/// Asks for the percent Claude shows. Pressing Enter skips, and the question is asked again next time.
fn calibrate_interactive(config_path: &Path) -> Result<String, String> {
    println!("Set up token-bar once. Check the usage percentage Claude shows you right now.");
    print!("Percent shown by Claude (for example 40), or press Enter to skip: ");
    io::stdout().flush().map_err(|error| error.to_string())?;

    let mut answer = String::new();
    io::stdin()
        .lock()
        .read_line(&mut answer)
        .map_err(|error| error.to_string())?;
    if answer.trim().is_empty() {
        return Ok("Skipped. Run token-bar calibrate when you're ready.".to_string());
    }
    calibrate_percent(config_path, answer.trim())
}

fn calibrate_percent(config_path: &Path, text: &str) -> Result<String, String> {
    let percent = parse_percent(text)?;
    let config = load_config(config_path);
    let used = used_tokens_in_window(&config)?;
    let limit = limit_from_percent(used, percent).ok_or(
        "no Claude Code usage in the window yet, so there is nothing to calibrate against. Use Claude a little, then try again.",
    )?;

    save_limit(config_path, limit)?;
    let mut message = format!(
        "Calibrated. Your limit is about {limit} tokens per {}-hour window.",
        config.window_hours
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
    save_limit(config_path, limit)?;
    Ok(format!("Limit set to {limit} tokens."))
}

fn save_limit(config_path: &Path, limit: u64) -> Result<(), String> {
    let mut config = load_config(config_path);
    config.limit_tokens = Some(limit);
    save_config(config_path, &config).map_err(|error| format!("could not save config: {error}"))
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

fn window_for(config: &Config) -> Duration {
    Duration::milliseconds((config.window_hours * 3_600_000.0) as i64)
}

fn used_tokens_in_window(config: &Config) -> Result<u64, String> {
    let root = default_logs_root().ok_or("could not find your home folder")?;
    let now = Utc::now();
    let records = scan_records(&root, now - window_for(config));
    Ok(records.iter().map(|record| record.tokens).sum())
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
    fn tokens_calibration_saves_the_limit_and_accepts_commas() {
        let dir = std::env::temp_dir().join(format!("token-bar-cli-{}", std::process::id()));
        let path = dir.join("config.json");

        let message = calibrate_tokens(&path, "5,000,000").expect("valid limit");

        assert_eq!(message, "Limit set to 5000000 tokens.");
        assert_eq!(load_config(&path).limit_tokens, Some(5_000_000));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn tokens_calibration_rejects_non_numbers_and_zero() {
        let path = std::env::temp_dir().join("token-bar-cli-never-written.json");

        assert!(calibrate_tokens(&path, "lots").is_err());
        assert!(calibrate_tokens(&path, "0").is_err());
    }
}
