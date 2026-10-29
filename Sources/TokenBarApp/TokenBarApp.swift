import AppKit
import SwiftUI
import TokenBarCore

@main
struct TokenBarApp: App {
    @StateObject private var model = UsageModel()

    var body: some Scene {
        MenuBarExtra {
            UsagePanel(model: model)
        } label: {
            Text(model.menuTitle)
        }
        .menuBarExtraStyle(.window)
    }
}

@MainActor
final class UsageModel: ObservableObject {
    private static let refreshInterval: TimeInterval = 60
    private static let paceWindow: TimeInterval = 30 * 60

    @Published private(set) var snapshot: UsageSnapshot?
    @Published private(set) var config: TokenBarConfig
    @Published var limitInput: String

    private let configURL: URL
    private let logsRoot: URL

    init() {
        let support = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("token-bar", isDirectory: true)
        configURL = support.appendingPathComponent("config.json")
        logsRoot = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude/projects", isDirectory: true)

        let loaded = ConfigStore.load(from: configURL)
        config = loaded
        limitInput = loaded.limitTokens.map(String.init) ?? ""

        refresh()
        Timer.scheduledTimer(withTimeInterval: Self.refreshInterval, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    var menuTitle: String {
        guard let snapshot else { return "Set limit" }
        let percent = "\(Int(snapshot.percent.rounded(.down)))%"
        guard let minutes = snapshot.minutesToNextPercent else { return percent }
        return "\(percent) · \(Int(minutes.rounded()))m"
    }

    func saveLimit() {
        guard let limit = Int(limitInput.trimmingCharacters(in: .whitespaces)), limit > 0 else {
            return
        }
        config.limitTokens = limit
        try? ConfigStore.save(config, to: configURL)
        refresh()
    }

    func refresh() {
        guard let limit = config.limitTokens,
              let estimator = UsageEstimator(limitTokens: limit,
                                             windowLength: config.windowHours * 3600,
                                             paceWindow: Self.paceWindow)
        else {
            snapshot = nil
            return
        }

        let root = logsRoot
        let cutoff = Date().addingTimeInterval(-config.windowHours * 3600)
        Task {
            let records = await Task.detached(priority: .utility) {
                LogScanner(root: root).records(since: cutoff)
            }.value
            snapshot = estimator.snapshot(records: records, now: Date())
        }
    }
}

struct UsagePanel: View {
    @ObservedObject var model: UsageModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let snapshot = model.snapshot {
                Text("Claude usage").font(.headline)
                Text("\(Int(snapshot.percent.rounded(.down)))%")
                    .font(.system(size: 32, weight: .medium))
                ProgressView(value: min(snapshot.percent, 100), total: 100)
                if let next = snapshot.nextWholePercent, let minutes = snapshot.minutesToNextPercent {
                    LabeledContent("Next point (\(next)%)", value: "in about \(Int(minutes.rounded())) min")
                } else {
                    Text("No recent activity, so no estimate yet.").foregroundStyle(.secondary)
                }
                LabeledContent("Pace, last 30 min", value: String(format: "%.0f tokens/min", snapshot.tokensPerMinute))
            } else {
                Text("Set your plan's limit").font(.headline)
                Text("Anthropic doesn't publish the limit, so enter the token count you see when you hit it.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Divider()

            HStack {
                TextField("Limit in tokens", text: $model.limitInput)
                    .textFieldStyle(.roundedBorder)
                Button("Save") { model.saveLimit() }
            }

            Button("Quit") { NSApplication.shared.terminate(nil) }
        }
        .padding(16)
        .frame(width: 320)
    }
}
