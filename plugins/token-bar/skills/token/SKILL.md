---
name: token
description: Show your Claude usage against your 5-hour and weekly limits, and when the next whole percent lands. Use when the user runs /token-bar:token or asks how close they are to their Claude limit.
argument-hint: [status | calibrate | statusline]
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" *)
---

Show the user their Claude usage. The script does all the work; you only run it and talk to the user.

The script is at `${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py`. Run it as:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" status

1. Run `status` and show its output exactly as printed. Don't add numbers of your own.

2. If the output ends with `Live numbers: off`, offer live numbers first. This is the best way to get accurate numbers, because Claude reports them itself and the user types nothing. Ask once:

   "Want automatic, accurate numbers? I can add a status line to your Claude settings. It only changes that one setting, and I won't replace a status line you already have. After that, run this command again and it shows your live usage."

   Only if they say yes, run:

       python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" enable-statusline

   If it says the status line was set, tell the user to run `/token-bar:token` once more. Claude only reports usage after its next reply, and the status line saves it then. If it says a different status line was kept, tell the user you left their existing status line alone, and go to step 3. Don't offer this again in the same session.

3. Use manual numbers only if the user said no to live numbers, if they already have a different status line, or if live numbers are still off after they ran the command a second time. Live numbers need a Claude.ai Pro or Max account, so API-key users always end up here. Then, if the output starts with `Setup needed`, ask in chat, one window at a time. For the 5-hour window, ask: "What percent does Claude show for your 5-hour limit right now, and when does it reset? For example: 40, and 2:20 PM." Then run:

       python3 "${CLAUDE_PLUGIN_ROOT}/scripts/token_bar.py" calibrate five_hour <percent> "<reset time>"

   Do the same for the weekly window with `weekly` and a time such as `Sun 5:30 AM`. Show the script's result, then run `status` again. If the user skips a window, move on and don't ask again in this session.

4. If the user passes an argument, follow it: `calibrate` means step 3, `statusline` means step 2, and `status` or no argument means step 1.

If the shell reports `python3: command not found`, tell the user Python 3 is needed and don't try to install it yourself.
