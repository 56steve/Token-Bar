import Foundation
import Testing
@testable import TokenBarCore

@Suite("UsageRecordParser")
struct UsageRecordParserTests {
    @Test("counts input, output and cache creation, but not cache reads")
    func parsesAssistantUsage() throws {
        let line = """
        {"type":"assistant","timestamp":"2026-09-25T11:31:16.905Z","requestId":"req_1","message":{"usage":{"input_tokens":2,"cache_creation_input_tokens":63611,"cache_read_input_tokens":39099,"output_tokens":2208}}}
        """

        let record = try #require(UsageRecordParser.parse(line: line))

        #expect(record.requestId == "req_1")
        #expect(record.tokens == 2 + 63611 + 2208)
    }

    @Test("ignores non-assistant lines")
    func ignoresUserLines() {
        let line = """
        {"type":"user","timestamp":"2026-09-25T11:31:16.905Z","requestId":"req_2","message":{"usage":{"input_tokens":5}}}
        """

        #expect(UsageRecordParser.parse(line: line) == nil)
    }

    @Test("ignores malformed JSON")
    func ignoresMalformedJSON() {
        #expect(UsageRecordParser.parse(line: "{not json") == nil)
    }

    @Test("ignores assistant lines without usage")
    func ignoresLinesWithoutUsage() {
        let line = """
        {"type":"assistant","timestamp":"2026-09-25T11:31:16.905Z","requestId":"req_3","message":{}}
        """

        #expect(UsageRecordParser.parse(line: line) == nil)
    }
}
