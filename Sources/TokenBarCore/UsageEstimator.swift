import Foundation

public struct UsageSnapshot: Equatable, Sendable {
    public let usedTokens: Int
    public let percent: Double
    /// The next whole percent above the current usage, such as 54 when at 53.4%.
    public let nextWholePercent: Int?
    /// Minutes until usage reaches `nextWholePercent` at the recent pace. Nil when there is no recent activity.
    public let minutesToNextPercent: Double?
    public let tokensPerMinute: Double
}

/// Works out usage against a limit, and how long until the next whole percent at the recent pace.
public struct UsageEstimator: Sendable {
    public let limitTokens: Int
    public let windowLength: TimeInterval
    public let paceWindow: TimeInterval

    /// Returns nil when the limit or the time spans are not positive.
    public init?(limitTokens: Int, windowLength: TimeInterval, paceWindow: TimeInterval) {
        guard limitTokens > 0, windowLength > 0, paceWindow > 0 else {
            return nil
        }
        self.limitTokens = limitTokens
        self.windowLength = windowLength
        self.paceWindow = paceWindow
    }

    public func snapshot(records: [UsageRecord], now: Date) -> UsageSnapshot {
        let windowStart = now.addingTimeInterval(-windowLength)
        let inWindow = records.filter { $0.timestamp >= windowStart && $0.timestamp <= now }
        let used = inWindow.reduce(0) { $0 + $1.tokens }
        let percent = Double(used) / Double(limitTokens) * 100

        let paceStart = now.addingTimeInterval(-paceWindow)
        let recent = inWindow.filter { $0.timestamp >= paceStart }.reduce(0) { $0 + $1.tokens }
        let tokensPerMinute = Double(recent) / (paceWindow / 60)

        guard percent < 100 else {
            return UsageSnapshot(usedTokens: used, percent: percent, nextWholePercent: nil,
                                 minutesToNextPercent: nil, tokensPerMinute: tokensPerMinute)
        }

        let nextWholePercent = Int(percent.rounded(.down)) + 1
        let tokensAtNextPercent = Double(nextWholePercent) / 100 * Double(limitTokens)
        let tokensRemaining = tokensAtNextPercent - Double(used)
        let minutes: Double? = tokensPerMinute > 0 ? tokensRemaining / tokensPerMinute : nil

        return UsageSnapshot(usedTokens: used, percent: percent, nextWholePercent: nextWholePercent,
                             minutesToNextPercent: minutes, tokensPerMinute: tokensPerMinute)
    }
}
