# token-bar

Ask your coding agent how close you are to your usage limit.

token-bar is a plugin for **Claude Code** and **Codex**. It reads the usage numbers each tool already keeps on your machine, and tells you:

- **where you stand**: the percent used for each limit window
- **when the next point lands**: an estimate of how long until the percent goes up by one, based on how fast you've been working
- **when it resets**: a countdown to the next reset

It makes no network calls and needs no account.

[![MIT license](https://img.shields.io/badge/license-MIT-3ddc97)](LICENSE)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-d97757)
![Codex plugin](https://img.shields.io/badge/Codex-plugin-10a37f)

## Install

### Claude Code

Run these two commands inside Claude Code:

```
/plugin marketplace add 56steve/Token-Bar
/plugin install token-bar --marketplace 56steve/Token-Bar
```

Then run `/token-bar:token`.

The first time, it asks one question: do you want live numbers?

- **Yes.** It adds a status line to your Claude settings, so Claude reports your real percentages to token-bar. After that you never type a number.
- **No.** It asks once for the percent Claude shows you and when it resets, then estimates from there.

It only changes the one status line setting, and it never replaces a status line you already have.

Each limit prints as a bar, with the values filled in from your own usage:

```
<window>  <bar>  <percent>% → <next percent>% in ~<minutes>
          resets in <time>

Source: <where the numbers came from>
```

### Codex

Run these two commands in a terminal:

```bash
codex plugin marketplace add 56steve/Token-Bar
codex plugin add token-bar@token-bar
```

Then ask Codex: **show my Codex usage limits**.

There is nothing to set up. Codex already writes your limits into its own session logs, and token-bar reads them. The answer shows each window as a bar with its percent, an estimate of when it next goes up, when it resets, and how old the reading is.

## Supported tools

| Tool | Status | Where the numbers come from |
| :- | :- | :- |
| Claude Code | Supported | Claude's own percentages through the status line, or an estimate from your session logs |
| Codex | Supported | The limits Codex records in its own session logs |
| Gemini CLI | Not supported | Its files record tokens but not your limit, so any number would be a guess |
| Cursor | Not supported | Its usage lives on Cursor's servers, not on your machine |

## Good to know

- **Live Claude numbers need a Claude.ai Pro or Max account.** Claude only reports your limits to the status line on those accounts. API-key users get the estimate instead.
- **Readings update while you use the tool.** token-bar shows how old each reading is, and it skips the next-point estimate when a reading is old.
- **Codex reports whole percents,** so its next-point estimate is approximate.
- **Needs Python 3.** macOS includes it. Windows users may need to install it first.

## FAQ

**Where does my data go?**
Nowhere. token-bar reads each tool's own local session files and writes only its own small settings file. It never sends anything out.

**Why does Claude Code ask me to say yes once?**
Claude Code only hands your real limits to the status line, and a plugin can't turn the status line on by itself. It lives in your own settings, so token-bar asks before changing it.

**I already have a status line. Can I still get live numbers?**
Yes. token-bar leaves yours alone and prints one line you can add to your own status line script. That line passes the same data to token-bar.

**How accurate is it?**
The percents are the ones the tools report. The next-point and reset times come from those percents and your recent pace, so treat them as estimates. Without live numbers, the Claude estimate depends on the last percent you gave it.

## Terminal version

This repo also has a Rust command-line tool for the Claude numbers:

```bash
cargo install --git https://github.com/56steve/Token-Bar token-bar
token-bar
```

## Not affiliated with Anthropic or OpenAI

token-bar is an independent open-source project. It is not made by, endorsed by, or affiliated with Anthropic or OpenAI. Claude, Claude Code, and Codex are their makers' products.

## Contributing

Issues and pull requests are welcome, especially readers for other tools. Each tool is one small Python file that finds the tool's usage records and prints them:

- [`plugins/token-bar/scripts/token_bar.py`](plugins/token-bar/scripts/token_bar.py) for Claude Code
- [`plugins/token-bar-codex/skills/token-bar/scripts/codex_usage.py`](plugins/token-bar-codex/skills/token-bar/scripts/codex_usage.py) for Codex

Run the tests with:

```bash
cd plugins/token-bar && python3 -m unittest discover -s tests
cd plugins/token-bar-codex && python3 -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
