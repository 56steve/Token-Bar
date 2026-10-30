---
name: token-bar
description: Show your Codex usage limits, when they reset, and what is using them up. Use when the user asks how close they are to their Codex limit, how much of their Codex usage is left, when their Codex limit resets, or which project or model uses the most.
---

# token-bar for Codex

Show the user their Codex usage limits. Codex records its own rate limits in its session logs, so there is nothing to ask the user and nothing to set up.

1. Run the script that sits next to this file, in the `scripts` folder:

       python3 scripts/codex_usage.py

   If that path doesn't exist, find the script with `find ~/.codex ~/.agents -name codex_usage.py` and run it with `python3`.

2. Show its output inside a code block, exactly as printed. The bars only line up in a code block. Don't add numbers of your own.

3. If the output says a window has reset since the reading, tell the user the reading is old and that their usage is probably lower now. Using Codex once will refresh it.

4. If the output says no Codex usage was found, tell the user to use Codex once and ask again.

5. If the user asks what is using up their Codex limit, or which project or model uses the most, run the same script with `breakdown` and show its output inside a code block, exactly as printed:

       python3 scripts/codex_usage.py breakdown

If `python3` isn't found, tell the user Python 3 is needed. Don't try to install it yourself.
