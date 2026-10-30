# token-bar

**See how close you are to your AI coding usage limits, right inside your coding agent.**

Works with **Claude Code** and **Codex**. Ask for your usage and see where you stand and when each limit resets. Your numbers stay on your machine.

```
5-hour usage   21%
  Next point   22% in about 23 min
  Resets       in 1h 1m

Weekly usage   78%
  Next point   79% in about 47 min
  Resets       in 2d 16h
```

## Supported tools

| Tool | Status | How it gets your numbers |
| :- | :- | :- |
| **Claude Code** | Supported | Claude's own percentages through the status line, or an estimate from your session logs |
| **Codex** | Supported | The limits Codex already records in its session logs. Nothing to set up |
| Gemini CLI | Not yet | Its local files don't show usage limits, so there's nothing to read |
| Cursor | Not yet | Its local files don't show usage limits, so there's nothing to read |

More tools will be added when they expose their limits in a way token-bar can read. Pull requests are welcome.

## Claude Code

Inside Claude Code, run these two commands:

```
/plugin marketplace add 56steve/Token-Bar
/plugin install token-bar --marketplace 56steve/Token-Bar
```

Nothing to download, nothing to build, and no account to create. Then run `/token-bar:token`.

The first time, it asks one question: **do you want live numbers?**

- **Yes (recommended).** It adds a status line to your Claude settings. Claude reports your real 5-hour and weekly percentages to it, so you never type a number. Run `/token-bar:token` once more and it shows your live usage.
- **No.** It asks for the percentage Claude shows for each window, and when it resets, in chat. After that, it estimates your usage.

It only changes the one status line setting, and it never replaces a status line you already have. Claude Code only gives your real percentages to the status line, and a plugin can't turn that on by itself, so token-bar asks first.

## Codex

Add the marketplace from this repo, then install the plugin:

```bash
codex plugin marketplace add 56steve/Token-Bar
codex plugin add token-bar@token-bar
```

Then ask Codex something like "show my Codex usage limits". There's nothing to set up, because Codex already writes your rate limits into its own session logs under `~/.codex/sessions/`, and token-bar reads them.

The reading is from your last Codex session, so it shows how old it is. If a limit window has reset since then, token-bar says so.

> **Status:** tested in the Codex built into the ChatGPT app (Codex 0.164). Both install commands work from GitHub, the plugin installs and enables, and when asked "show my Codex usage limits", Codex runs the skill and shows your real limits. If it doesn't for you, open an issue.

## How it works

- **Claude Code** writes a local log of your messages. token-bar reads those logs to count your tokens, counting each request once even when Claude streams it in several pieces. Your limits reset on fixed schedules, so it counts each window from its last reset. With the live status line on, it uses Claude's own percentages instead of an estimate.
- **Codex** writes your rate limits into its session logs. token-bar reads the newest reading.

## Privacy

- Everything runs locally. token-bar makes no network calls and has no account.
- It reads only each tool's own local session files, and its own settings in `~/.claude/token-bar/`. It never sends them anywhere.
- It only edits your Claude settings when you say yes, and it never replaces a status line you already have.

## What to expect

- **Live Claude numbers need a Claude.ai Pro or Max account.** The status line only receives Claude's limits on those accounts. API-key users use the estimate instead.
- **Readings update while the tool is in use.** token-bar shows how old each reading is.
- **The Claude estimate is only as good as your last reading.** Calibrate again if it drifts from Claude's meter.
- **Requires Python 3.** macOS includes it. Windows users may need to install it first.

## Terminal version

This repo also has a Rust command-line tool, `token-bar`, for people who want the Claude numbers in a terminal:

```bash
cargo install --git https://github.com/56steve/Token-Bar token-bar
token-bar
```

## Not affiliated with Anthropic or OpenAI

token-bar is an independent open-source project. It is not made by, endorsed by, or affiliated with Anthropic or OpenAI. Claude, Claude Code, and Codex are their makers' products.

## Contributing

Issues and pull requests are welcome, especially readers for other tools. The logic lives in:

- [`plugins/token-bar/scripts/token_bar.py`](plugins/token-bar/scripts/token_bar.py) for Claude Code
- [`plugins/token-bar-codex/skills/token-bar/scripts/codex_usage.py`](plugins/token-bar-codex/skills/token-bar/scripts/codex_usage.py) for Codex

Run the tests with:

```bash
cd plugins/token-bar && python3 -m unittest discover -s tests
cd plugins/token-bar-codex && python3 -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
