# Contributing to token-bar

Thanks for helping. token-bar answers one question inside your coding agent: how close am I to my usage limit? Bug reports, ideas, docs fixes, and support for more tools are all welcome.

## The one rule

**token-bar never sends anything out.** It reads files the tools already keep on your machine and writes only its own small settings files. No network calls, no accounts, no analytics. A change that adds any of that won't be merged, even as an option. If you find that token-bar does send data somewhere, that is a bug: please open an issue.

## Ways to help

- **Report a bug.** Open an issue with what you ran, what you expected, and what you got. Useful details: your tool and version, whether you use a terminal or the desktop app, your plan (Pro, Max, Ultra, API key), and the output of `python3 plugins/token-bar/scripts/token_bar.py status` with anything private removed.
- **Suggest an idea.** Say what you were trying to find out, not only the feature you want. "I want to know if I'll last until Friday" is easier to design for than "add a flag".
- **Add a tool.** The most wanted contribution. See [Adding a tool](#adding-a-tool) below.
- **Improve the docs.** If something confused you, it will confuse others.

## How the repo is laid out

| Path | What it is |
| :- | :- |
| `plugins/token-bar/` | The Claude Code plugin: `scripts/token_bar.py` does the work, `skills/token/SKILL.md` tells Claude how to run it, `hooks/` holds the heads-up hook, `tests/` holds its tests |
| `plugins/token-bar-codex/` | The Codex plugin: `skills/token-bar/scripts/codex_usage.py` reads Codex's session logs |
| `crates/` | A Rust command-line tool for the Claude numbers (`token-bar-core` and `token-bar-cli`) |
| `.claude-plugin/`, `.agents/plugins/` | The marketplace files Claude Code and Codex read when you add this repo |
| `assets/` | Screenshots used in the README |

The scripts use the Python standard library only, so people can install the plugin without installing anything else. Please keep it that way.

## Setting up

You need Python 3. You only need Rust if you work on the command-line tool.

```bash
git clone https://github.com/56steve/Token-Bar.git
cd Token-Bar
```

Run the tests before and after your change:

```bash
cd plugins/token-bar && python3 -m unittest discover -s tests
cd plugins/token-bar-codex && python3 -m unittest discover -s tests
```

If you touch the Rust code:

```bash
cargo test
cargo clippy
```

We keep the Python lint-clean with [ruff](https://docs.astral.sh/ruff/): `ruff check plugins`.

### Trying your change as the real plugin

Running the script directly tests the script, but not the skill that Claude follows. To check the whole flow, load your working copy as the plugin:

```bash
claude --plugin-dir plugins/token-bar
```

or add the repo folder as a marketplace and install from it:

```bash
claude plugin marketplace add /path/to/Token-Bar
claude plugin install token-bar@token-bar --scope user
```

A marketplace named `token-bar` replaces one you already added with that name. To go back to the published version, remove it and add `56steve/Token-Bar` again. Restart Claude Code after either method, because it loads skills when a session starts.

## Making a change

1. **Branch from `development`** and keep the change small and focused. One idea per pull request is much easier to review.
2. **Write a test first when you can.** Every behavior has a test in `tests/`, written with `unittest`. A bug fix should come with a test that fails without it.
3. **Open the pull request against `development`.** `main` is what the plugin install follows, so it only changes when a release is merged.
4. **Say what changed and why** in the description, and include before and after output if the output changed.

### Style

- **Plain words.** The output is for people glancing at it mid-task. Short sentences, no jargon. "Using it too fast" beats "pace exceeds linear budget".
- **Show what matters, hide the rest.** The first answer is a short summary, and details open on request. New output usually belongs behind a section, not in the summary.
- **Draw bars with full-height characters (`█` and `░`).** Partial blocks like `▁ ▂ ▃ ▅ ▇` render at the same height in some fonts, including in the Claude desktop app, so they can't show a difference. Check anything visual in a real terminal and in the app.
- **Estimates must say they are estimates.** If a number can be a few points off, the output should say so and say how to correct it.
- **Don't guess at someone else's format.** If a tool's log format is unclear, read real logs and write the test from what you find.
- **When output changes,** update the README and any screenshots. The README screenshots come from sample data, never from real usage.

## Adding a tool

Each tool is one small reader that finds the tool's own usage records and prints them. The Codex reader at `plugins/token-bar-codex/skills/token-bar/scripts/codex_usage.py` is the shortest example to copy.

A reader should:

1. Find the tool's usage records on disk, and do nothing else with them. No network, no writing outside token-bar's own settings.
2. Work out, for each limit window the tool has: a label, the percent used, and when it resets.
3. Print them in the same shape as the existing tools: a bar, the percent, when the next point lands, and when it resets, plus how old the reading is.
4. Say clearly when it can't find anything, and why, instead of printing zeros.
5. Come with tests that build small fake log files, so the tests need no real account.
6. Add a skill file that tells the agent how to run the reader, and a marketplace entry so people can install it.

Then add the tool to the "Supported tools" table in the README.

Some tools can't be supported. If a tool doesn't keep its limits on your machine, there is nothing to read, and a guessed number would mislead people. Gemini CLI is an example: it fetches your quota live and never saves it. If you hit that, say so in the issue instead of estimating.

## For maintainers: releasing

- Bump `version` in `plugins/token-bar/.claude-plugin/plugin.json` (and the Codex plugin's file for Codex changes). Claude Code updates plugins by version, so without a bump, installed copies won't update.
- Open a pull request from `development` to `main`, and merge it once the tests pass.
- Create a GitHub release tagged with the version, with notes that say what changed and how to update:

  ```
  /plugin marketplace update token-bar
  /plugin update token-bar@token-bar
  ```

## Be kind

Be patient and respectful with everyone, especially people filing their first issue or pull request.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
