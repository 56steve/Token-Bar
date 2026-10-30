# token-bar

**See how close you are to your Claude usage limit, right inside Claude Code.**

Run `/token-bar:token` and see your 5-hour and weekly usage, plus when the next whole percent lands. Your numbers stay on your machine.

```
5-hour usage   21%
  Next point   22% in about 23 min
  Resets       in 1h 1m

Weekly usage   78%
  Next point   79% in about 47 min
  Resets       in 2d 16h
```

## Install

Inside Claude Code, run these two commands:

```
/plugin marketplace add 56steve/Token-Bar
/plugin install token-bar --marketplace 56steve/Token-Bar
```

That's it. Nothing to download, nothing to build, and no account to create.

## Use

Run `/token-bar:token` in Claude Code. The first time, it asks one question: **do you want live numbers?**

- **Yes (recommended).** It adds a status line to your Claude settings. Claude reports your real 5-hour and weekly percentages to it, so you never type a number. Run `/token-bar:token` once more and it shows your live usage.
- **No.** It asks for the percentage Claude shows for each window, and when it resets, in chat. After that, it estimates your usage.

It only changes the one status line setting, and it never replaces a status line you already have.

Why one yes is needed: Claude Code only gives your real percentages to the status line, and a plugin can't turn that on by itself. It has to be in your own settings, so token-bar asks first.

## How it works

- Claude Code writes a local log of your messages. token-bar reads those logs to count your tokens. It counts each request once, even when Claude streams it in several pieces.
- Your limits reset on fixed schedules, so token-bar counts each window from its last reset. You give it the reset time once.
- With the live status line on, token-bar uses Claude's own percentages. Without it, token-bar estimates from the logs and the limit you set.

## Privacy

- Everything runs locally. token-bar makes no network calls and has no account.
- It reads `~/.claude/projects/` and its own settings in `~/.claude/token-bar/`. It never sends them anywhere.
- It only edits your Claude settings when you say yes, and it never replaces a status line you already have.

## What to expect

- **Live numbers need Claude.ai.** The status line only receives Claude's limits on Claude.ai subscription accounts. API-key users use the estimate instead.
- **Live numbers update when Claude Code is open.** token-bar shows how old each reading is.
- **The estimate is only as good as your last reading.** Calibrate again if it drifts from Claude's meter.
- **Requires Python 3.** macOS includes it. Windows users may need to install it first.

## Terminal version

This repo also has a Rust command-line tool, `token-bar`, for people who want the same numbers in a terminal:

```bash
cargo install --git https://github.com/56steve/Token-Bar token-bar
token-bar
```

## Not affiliated with Anthropic

token-bar is an independent open-source project. It is not made by, endorsed by, or affiliated with Anthropic. Claude and Claude Code are Anthropic's products.

## Contributing

Issues and pull requests are welcome. The plugin's logic lives in [`plugins/token-bar/scripts/token_bar.py`](plugins/token-bar/scripts/token_bar.py), and its tests can be run with:

```bash
cd plugins/token-bar && python3 -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
