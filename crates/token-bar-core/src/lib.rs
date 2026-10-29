//! Reads Claude Code session logs and estimates how close you are to your usage limit.

mod config;
mod estimate;
mod live;
mod record;
mod scan;
mod window;

pub use config::{default_config_path, default_logs_root, load_config, save_config, Config};
pub use estimate::{limit_from_percent, Estimator, Snapshot};
pub use live::{default_live_path, load_live, parse_live, LiveLimits, LiveWindow};
pub use record::{parse_line, UsageRecord};
pub use scan::scan_records;
pub use window::{next_reset, window_start};
