import Foundation

/// One billed request from a Claude Code session log.
public struct UsageRecord: Equatable, Sendable {
    public let requestId: String
    public let timestamp: Date
    public let tokens: Int

    public init(requestId: String, timestamp: Date, tokens: Int) {
        self.requestId = requestId
        self.timestamp = timestamp
        self.tokens = tokens
    }
}

public enum UsageRecordParser {
    /// Parses one JSONL line. Returns nil for lines that are not assistant usage entries.
    /// Cache reads are excluded from the token count. They are cheap and do not count toward the limit as much.
    public static func parse(line: String) -> UsageRecord? {
        guard
            let data = line.data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            object["type"] as? String == "assistant",
            let requestId = object["requestId"] as? String,
            let timestampText = object["timestamp"] as? String,
            let timestamp = parseTimestamp(timestampText),
            let message = object["message"] as? [String: Any],
            let usage = message["usage"] as? [String: Any]
        else {
            return nil
        }

        let tokens = intValue(usage["input_tokens"])
            + intValue(usage["output_tokens"])
            + intValue(usage["cache_creation_input_tokens"])

        return UsageRecord(requestId: requestId, timestamp: timestamp, tokens: tokens)
    }

    private static func parseTimestamp(_ text: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: text)
    }

    private static func intValue(_ value: Any?) -> Int {
        (value as? NSNumber)?.intValue ?? 0
    }
}
