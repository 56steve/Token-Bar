# token-bar

See how close you are to your Claude usage limit, and when the next whole percent lands.

token-bar is a macOS menu bar app. It reads the session logs that Claude Code already writes to `~/.claude/projects/` and works out your usage locally. No account, no network calls, and nothing leaves your machine.

Status: early development. Not released yet.

## Build and test

Requires macOS 13 or later and a Swift 6 toolchain (Xcode or the command-line tools).

```bash
swift build
swift test
swift run token-bar
```

Anthropic doesn't publish plan limits, so the app asks you for your limit in tokens once. The count is an estimate and can be recalibrated.

## License

MIT. See [LICENSE](LICENSE).
