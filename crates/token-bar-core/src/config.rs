use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// User settings. Each limit is an estimate the user calibrates, because Anthropic does not publish it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Config {
    /// Limit for the rolling window of `window_hours`, such as the 5-hour limit.
    pub limit_tokens: Option<u64>,
    pub window_hours: f64,
    /// Limit for the weekly window.
    pub weekly_limit_tokens: Option<u64>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            limit_tokens: None,
            window_hours: 5.0,
            weekly_limit_tokens: None,
        }
    }
}

/// Returns the default config when the file is missing or unreadable.
pub fn load_config(path: &Path) -> Config {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

pub fn save_config(path: &Path, config: &Config) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let text = serde_json::to_string_pretty(config)?;
    fs::write(path, text)
}

/// `<config dir>/token-bar/config.json`, such as `~/Library/Application Support` on macOS.
pub fn default_config_path() -> Option<PathBuf> {
    dirs::config_dir().map(|dir| dir.join("token-bar").join("config.json"))
}

/// `~/.claude/projects`, where Claude Code writes its session logs.
pub fn default_logs_root() -> Option<PathBuf> {
    dirs::home_dir().map(|home| home.join(".claude").join("projects"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_file(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("token-bar-config-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        dir.join("config.json")
    }

    #[test]
    fn missing_file_gives_defaults() {
        let path = temp_file("missing");

        assert_eq!(load_config(&path), Config::default());
    }

    #[test]
    fn saved_limit_round_trips() {
        let path = temp_file("roundtrip");
        let config = Config {
            limit_tokens: Some(5_000_000),
            window_hours: 5.0,
            weekly_limit_tokens: Some(40_000_000),
        };

        save_config(&path, &config).expect("save config");

        assert_eq!(load_config(&path), config);
        let _ = fs::remove_dir_all(path.parent().expect("has parent"));
    }

    #[test]
    fn corrupt_file_gives_defaults() {
        let path = temp_file("corrupt");
        fs::create_dir_all(path.parent().expect("has parent")).expect("create dir");
        fs::write(&path, "{ not json").expect("write");

        assert_eq!(load_config(&path), Config::default());
        let _ = fs::remove_dir_all(path.parent().expect("has parent"));
    }
}
