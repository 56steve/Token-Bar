import Foundation
import Testing
@testable import TokenBarCore

@Suite("LogScanner")
struct LogScannerTests {
    private let now = Date()
    private let formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    /// Creates an empty temporary root. Call `cleanup` when the test ends.
    private func makeRoot() throws -> (root: URL, cleanup: () -> Void) {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("token-bar-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return (root, { try? FileManager.default.removeItem(at: root) })
    }

    private func assistantLine(requestId: String, minutesAgo: Double, input: Int, output: Int) -> String {
        let timestamp = formatter.string(from: now.addingTimeInterval(-minutesAgo * 60))
        return #"{"type":"assistant","timestamp":"\#(timestamp)","requestId":"\#(requestId)","message":{"usage":{"input_tokens":\#(input),"output_tokens":\#(output)}}}"#
    }

    @Test("counts each request once even when it is repeated across streamed lines")
    func deduplicatesByRequestId() throws {
        let (root, cleanup) = try makeRoot()
        defer { cleanup() }
        let lines = [
            assistantLine(requestId: "req_a", minutesAgo: 20, input: 10, output: 5),
            assistantLine(requestId: "req_a", minutesAgo: 19, input: 10, output: 5),
            assistantLine(requestId: "req_b", minutesAgo: 10, input: 3, output: 4),
        ].joined(separator: "\n")
        try lines.write(to: root.appendingPathComponent("session.jsonl"), atomically: true, encoding: .utf8)

        let records = LogScanner(root: root).records(since: now.addingTimeInterval(-3600))

        #expect(records.count == 2)
        #expect(records.map(\.tokens).reduce(0, +) == 15 + 7)
    }

    @Test("skips records older than the cutoff")
    func skipsOldRecords() throws {
        let (root, cleanup) = try makeRoot()
        defer { cleanup() }
        let line = assistantLine(requestId: "req_old", minutesAgo: 6 * 60, input: 100, output: 0)
        try line.write(to: root.appendingPathComponent("old.jsonl"), atomically: true, encoding: .utf8)

        #expect(LogScanner(root: root).records(since: now.addingTimeInterval(-3600)).isEmpty)
    }
}
