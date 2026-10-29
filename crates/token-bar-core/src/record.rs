use chrono::{DateTime, Utc};
use serde::Deserialize;

/// One billed request from a Claude Code session log.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UsageRecord {
    pub request_id: String,
    pub timestamp: DateTime<Utc>,
    pub tokens: u64,
}

#[derive(Deserialize)]
struct RawLine {
    #[serde(rename = "type")]
    kind: Option<String>,
    timestamp: Option<String>,
    #[serde(rename = "requestId")]
    request_id: Option<String>,
    message: Option<RawMessage>,
}

#[derive(Deserialize)]
struct RawMessage {
    usage: Option<RawUsage>,
}

#[derive(Deserialize)]
struct RawUsage {
    input_tokens: Option<u64>,
    output_tokens: Option<u64>,
    cache_creation_input_tokens: Option<u64>,
}

/// Parses one JSONL line. Returns `None` for anything that is not an assistant usage entry.
///
/// Cache reads are left out of the count. They are cheap and count against the limit far less than other tokens.
pub fn parse_line(line: &str) -> Option<UsageRecord> {
    let raw: RawLine = serde_json::from_str(line).ok()?;
    if raw.kind.as_deref() != Some("assistant") {
        return None;
    }
    let usage = raw.message?.usage?;
    let timestamp = DateTime::parse_from_rfc3339(&raw.timestamp?)
        .ok()?
        .with_timezone(&Utc);
    let tokens = usage.input_tokens.unwrap_or(0)
        + usage.output_tokens.unwrap_or(0)
        + usage.cache_creation_input_tokens.unwrap_or(0);

    Some(UsageRecord {
        request_id: raw.request_id?,
        timestamp,
        tokens,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_input_output_and_cache_creation_but_not_cache_reads() {
        let line = r#"{"type":"assistant","timestamp":"2026-09-25T11:31:16.905Z","requestId":"req_1","message":{"usage":{"input_tokens":2,"cache_creation_input_tokens":63611,"cache_read_input_tokens":39099,"output_tokens":2208}}}"#;

        let record = parse_line(line).expect("assistant usage line should parse");

        assert_eq!(record.request_id, "req_1");
        assert_eq!(record.tokens, 2 + 63611 + 2208);
    }

    #[test]
    fn ignores_non_assistant_lines() {
        let line = r#"{"type":"user","timestamp":"2026-09-25T11:31:16.905Z","requestId":"req_2","message":{"usage":{"input_tokens":5}}}"#;

        assert!(parse_line(line).is_none());
    }

    #[test]
    fn ignores_malformed_json() {
        assert!(parse_line("{not json").is_none());
    }

    #[test]
    fn ignores_assistant_lines_without_usage() {
        let line = r#"{"type":"assistant","timestamp":"2026-09-25T11:31:16.905Z","requestId":"req_3","message":{}}"#;

        assert!(parse_line(line).is_none());
    }

    #[test]
    fn treats_missing_token_fields_as_zero() {
        let line = r#"{"type":"assistant","timestamp":"2026-09-25T11:31:16.905Z","requestId":"req_4","message":{"usage":{"output_tokens":7}}}"#;

        assert_eq!(parse_line(line).map(|r| r.tokens), Some(7));
    }
}
