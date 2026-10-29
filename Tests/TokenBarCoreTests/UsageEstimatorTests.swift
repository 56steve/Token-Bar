import Foundation
import Testing
@testable import TokenBarCore

@Suite("UsageEstimator")
struct UsageEstimatorTests {
    private let now = ISO8601DateFormatter().date(from: "2026-10-29T12:00:00Z")!

    @Test("reports percent used and time to the next whole percent")
    func reportsPercentAndTimeToNextPoint() throws {
        let estimator = try #require(UsageEstimator(limitTokens: 1_000, windowLength: 5 * 3600, paceWindow: 1800))
        // 530 tokens used in the window, so 53%. 330 tokens in the last 30 minutes.
        let records = [
            UsageRecord(requestId: "old", timestamp: now.addingTimeInterval(-3 * 3600), tokens: 200),
            UsageRecord(requestId: "recent", timestamp: now.addingTimeInterval(-600), tokens: 330),
        ]

        let snapshot = estimator.snapshot(records: records, now: now)

        #expect(snapshot.usedTokens == 530)
        #expect(abs(snapshot.percent - 53) < 0.0001)
        #expect(snapshot.nextWholePercent == 54)
        // 10 more tokens reach 54%, at 11 tokens per minute.
        let minutes = try #require(snapshot.minutesToNextPercent)
        #expect(abs(minutes - 10 / (330.0 / 30.0)) < 0.0001)
    }

    @Test("ignores records outside the window")
    func ignoresExpiredRecords() throws {
        let estimator = try #require(UsageEstimator(limitTokens: 1_000, windowLength: 5 * 3600, paceWindow: 1800))
        let records = [UsageRecord(requestId: "expired", timestamp: now.addingTimeInterval(-6 * 3600), tokens: 900)]

        let snapshot = estimator.snapshot(records: records, now: now)

        #expect(snapshot.usedTokens == 0)
        #expect(snapshot.minutesToNextPercent == nil)
    }

    @Test("gives no time estimate without recent activity")
    func noEstimateWithoutRecentPace() throws {
        let estimator = try #require(UsageEstimator(limitTokens: 1_000, windowLength: 5 * 3600, paceWindow: 1800))
        let records = [UsageRecord(requestId: "earlier", timestamp: now.addingTimeInterval(-2 * 3600), tokens: 500)]

        let snapshot = estimator.snapshot(records: records, now: now)

        #expect(abs(snapshot.percent - 50) < 0.0001)
        #expect(snapshot.minutesToNextPercent == nil)
    }

    @Test("rejects a non-positive limit")
    func rejectsNonPositiveLimit() {
        #expect(UsageEstimator(limitTokens: 0, windowLength: 3600, paceWindow: 1800) == nil)
    }
}
