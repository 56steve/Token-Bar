# token-bar plugin for Claude Code

Shows your Claude usage against your 5-hour and weekly limits, and when the next whole percent lands. Type `/token` in Claude Code.

## Install

Inside Claude Code:

```
/plugin marketplace add 56steve/Token-Bar
/plugin install token-bar --marketplace 56steve/Token-Bar
```

Nothing else is needed. The plugin uses Python 3, which macOS includes.

## Use

- `/token` shows both windows. The first time, it asks for your percent and reset time for each window in chat.
- If you agree, `/token` can also add a status line that shows Claude's own numbers, so you don't have to enter them. Send one message in Claude Code after that.

The skill is `token-bar:token`. Claude Code namespaces plugin commands by plugin name.

## Local development

From the repo root:

```bash
claude --plugin-dir plugins/token-bar
```

Run the script's tests:

```bash
cd plugins/token-bar && python3 -m unittest discover -s tests
```
