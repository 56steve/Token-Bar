# token-bar plugin for Claude Code

Adds a `/token` command that shows your Claude usage against your limit, and when the next whole percent lands.

The plugin runs the `token-bar` CLI from this repo, so install that first.

## Commands

- `/token`: show usage now.
- `/token --json`: the same, as JSON.
- `/token calibrate <percent>`: set your limit from the percentage Claude shows you, such as `/token calibrate 40`.

## Try it locally

From the repo root:

```bash
cargo install --path crates/token-bar-cli
claude --plugin-dir plugins/token-bar
```

Then, inside Claude Code, run `/token`.
