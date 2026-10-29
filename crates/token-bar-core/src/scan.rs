use std::collections::HashMap;
use std::fs;
use std::path::Path;

use chrono::{DateTime, Utc};

use crate::record::{parse_line, UsageRecord};

/// Returns one record per request logged since `cutoff`, found anywhere under `root`.
///
/// Claude Code writes the same request several times as it streams, so records are
/// de-duplicated by request id and the largest count is kept. Files not modified since
/// `cutoff` are skipped without being read.
pub fn scan_records(root: &Path, cutoff: DateTime<Utc>) -> Vec<UsageRecord> {
    let mut by_request: HashMap<String, UsageRecord> = HashMap::new();
    collect(root, cutoff, &mut by_request);
    by_request.into_values().collect()
}

fn collect(dir: &Path, cutoff: DateTime<Utc>, by_request: &mut HashMap<String, UsageRecord>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect(&path, cutoff, by_request);
        } else if path.extension().is_some_and(|ext| ext == "jsonl")
            && is_modified_since(&path, cutoff)
        {
            read_file(&path, cutoff, by_request);
        }
    }
}

fn is_modified_since(path: &Path, cutoff: DateTime<Utc>) -> bool {
    fs::metadata(path)
        .and_then(|meta| meta.modified())
        .map(|modified| DateTime::<Utc>::from(modified) >= cutoff)
        .unwrap_or(false)
}

fn read_file(path: &Path, cutoff: DateTime<Utc>, by_request: &mut HashMap<String, UsageRecord>) {
    let Ok(text) = fs::read_to_string(path) else {
        return;
    };
    for line in text.lines() {
        let Some(record) = parse_line(line) else {
            continue;
        };
        if record.timestamp < cutoff {
            continue;
        }
        match by_request.get(&record.request_id) {
            Some(existing) if existing.tokens >= record.tokens => {}
            _ => {
                by_request.insert(record.request_id.clone(), record);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;
    use std::io::Write;

    fn line(request_id: &str, minutes_ago: i64, input: u64, output: u64) -> String {
        let timestamp = (Utc::now() - Duration::minutes(minutes_ago)).to_rfc3339();
        format!(
            r#"{{"type":"assistant","timestamp":"{timestamp}","requestId":"{request_id}","message":{{"usage":{{"input_tokens":{input},"output_tokens":{output}}}}}}}"#
        )
    }

    fn temp_root(name: &str) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("token-bar-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("project")).expect("create temp project dir");
        root
    }

    #[test]
    fn counts_each_request_once_even_when_streamed_repeatedly() {
        let root = temp_root("dedupe");
        let mut file =
            fs::File::create(root.join("project").join("session.jsonl")).expect("create log");
        writeln!(file, "{}", line("req_a", 20, 10, 5)).expect("write");
        writeln!(file, "{}", line("req_a", 19, 10, 5)).expect("write");
        writeln!(file, "{}", line("req_b", 10, 3, 4)).expect("write");

        let mut records = scan_records(&root, Utc::now() - Duration::hours(1));
        records.sort_by(|a, b| a.request_id.cmp(&b.request_id));

        assert_eq!(records.len(), 2);
        assert_eq!(records[0].tokens, 15);
        assert_eq!(records[1].tokens, 7);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn skips_records_before_the_cutoff() {
        let root = temp_root("cutoff");
        let mut file =
            fs::File::create(root.join("project").join("old.jsonl")).expect("create log");
        writeln!(file, "{}", line("req_old", 6 * 60, 100, 0)).expect("write");

        let records = scan_records(&root, Utc::now() - Duration::hours(1));

        assert!(records.is_empty());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn missing_root_gives_no_records() {
        let root = std::env::temp_dir().join("token-bar-does-not-exist-xyz");

        assert!(scan_records(&root, Utc::now()).is_empty());
    }
}
