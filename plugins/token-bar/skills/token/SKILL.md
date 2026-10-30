---
name: token
description: Show your Claude usage against your 5-hour and weekly limits, when the next whole percent lands, and what is using it up. Use when the user runs /token-bar:token or asks how close they are to their Claude limit or what is eating it.
argument-hint: [status | breakdown | calibrate | statusline]
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" *)
---

Show the user their Claude usage. The script does all the work; you only run it and talk to the user.

The script is at `${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py`. Run it as:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" status

1. Run `status` and show its output inside a code block, exactly as printed. The bars only line up in a code block. Don't add numbers of your own.

2. If the output has a `Live numbers: off` line, offer live numbers first. This is the best way to get accurate numbers, because Claude reports them itself and the user types nothing. Ask once:

   "Want automatic, accurate numbers? I can add a status line to your Claude settings. It only changes that one setting, and I won't replace a status line you already have. After that, run this command again and it shows your live usage."

   Only if they say yes, run:

       python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" enable-statusline

   If it says the status line was set, Claude Code runs the status line right away, so run `status` again immediately. If it now has a `Live numbers: on` line, show it as printed and move on to step 6. If live numbers are still off, tell the user Claude only reports usage after its next reply, so they should run `/token-bar:token` once more after that. If it says a different status line was kept, tell the user you left their existing status line alone, show them the line the script printed so they can add it to their own status line script if they want live numbers, and go to step 3. Don't offer this again in the same session.

3. Use manual numbers only if the user said no to live numbers, if they already have a different status line, or if live numbers are still off after they ran the command a second time. Live numbers need a Claude.ai Pro or Max account, so API-key users always end up here. Then, if the output starts with `Setup needed`, ask in chat, one window at a time. For the 5-hour window, ask: "What percent does Claude show for your 5-hour limit right now, and when does it reset? For example: 40, and 2:20 PM." Then run:

       python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" calibrate five_hour <percent> "<reset time>"

   Do the same for the weekly window with `weekly` and a time such as `Sun 5:30 AM`. Show the script's result, then run `status` again. If the user skips a window, move on and don't ask again in this session.

4. If the user passes an argument, follow it: `calibrate` means step 3, `statusline` means step 2, `breakdown` means step 5, `shortcut` means step 6, and `status` or no argument means step 1.

5. For `breakdown`, or when the user asks what is using up their limit, run the script with `breakdown` (add `weekly` for the weekly window) and show its output inside a code block, exactly as printed:

       python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" breakdown

6. If the output has a `Short command: off` line, offer the short command once, after the steps above are done. Plugin commands always carry the plugin's name, so the user has to type `/token-bar:token`. Ask:

   "Want a shorter command? I can add `/token` so you don't have to type `/token-bar:token`. It adds one skill to your own skills folder, and I won't replace one you wrote."

   Only if they say yes, run:

       python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" install-shortcut

   Show the script's result. If it says the command was added but `/token` isn't listed yet, tell the user to restart Claude Code. Don't offer this again in the same session.

If the shell reports `python3: command not found`, tell the user Python 3 is needed and don't try to install it yourself.
