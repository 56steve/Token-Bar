import Foundation

/// Reads Claude Code session logs under a root folder, such as `~/.claude/projects`.
public struct LogScanner: Sendable {
    public let root: URL

    public init(root: URL) {
        self.root = root
    }

    /// Returns one record per request since `cutoff`. Claude Code writes the same request
    /// several times as it streams, so records are de-duplicated by `requestId`.
    public func records(since cutoff: Date) -> [UsageRecord] {
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else {
            return []
        }

        var byRequest: [String: UsageRecord] = [:]
        for case let url as URL in enumerator where url.pathExtension == "jsonl" {
            guard isModified(url, since: cutoff), let text = try? String(contentsOf: url, encoding: .utf8) else {
                continue
            }
            for line in text.split(separator: "\n") {
                guard
                    let record = UsageRecordParser.parse(line: String(line)),
                    record.timestamp >= cutoff
                else {
                    continue
                }
                if let existing = byRequest[record.requestId], existing.tokens >= record.tokens {
                    continue
                }
                byRequest[record.requestId] = record
            }
        }
        return Array(byRequest.values)
    }

    private func isModified(_ url: URL, since cutoff: Date) -> Bool {
        let modified = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        return (modified ?? .distantPast) >= cutoff
    }
}
