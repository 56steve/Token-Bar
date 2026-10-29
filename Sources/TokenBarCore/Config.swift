import Foundation

/// User settings. The limit is an estimate that the user calibrates, because Anthropic does not publish it.
public struct TokenBarConfig: Codable, Equatable, Sendable {
    public var limitTokens: Int?
    public var windowHours: Double

    public init(limitTokens: Int? = nil, windowHours: Double = 5) {
        self.limitTokens = limitTokens
        self.windowHours = windowHours
    }
}

public enum ConfigStore {
    /// Returns the default config when the file is missing or unreadable.
    public static func load(from url: URL) -> TokenBarConfig {
        guard let data = try? Data(contentsOf: url),
              let config = try? JSONDecoder().decode(TokenBarConfig.self, from: data)
        else {
            return TokenBarConfig()
        }
        return config
    }

    public static func save(_ config: TokenBarConfig, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(config).write(to: url, options: .atomic)
    }
}
