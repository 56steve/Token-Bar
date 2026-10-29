---
name: token
description: Show your Claude usage against your limit, and when the next whole percent lands. Use when the user runs /token or asks how close they are to their Claude limit.
argument-hint: [calibrate <percent>] [--json]
allowed-tools: Bash(token-bar:*)
---

Run the token-bar CLI and show its output as printed.

Arguments from the user: $ARGUMENTS

- With no arguments, run `token-bar`.
- With `--json`, run `token-bar --json`.
- With `calibrate <percent>`, run `token-bar calibrate <percent>`. Use the percent the user gives, such as 40. This sets their limit, so say what it saved.

If the output says `no limit set yet`, tell the user to run `/token calibrate <percent>` with the percentage Claude shows them right now.

If the shell reports `token-bar: command not found`, tell the user it isn't installed yet and point them to the install steps in the token-bar README. Don't try to install it yourself.
