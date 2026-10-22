# claude-usage-tracker

See how much of your Claude subscription limits each Claude Code answer and session uses.

Claude Code's `/usage` shows how much of your **5-hour** and **weekly** limits you've used, but not what used it. This tracker splits those figures across your answers and sessions, inside Claude Code, while you work.

**Always visible, as a status line under the prompt**

```
Last answer 0.72% · Session 3.69% of 5h  │  5h 40% used (resets 19:50)  │  week 10% used
```

**After every answer**

```
Stop says: this answer: 0.72% of 5h, 0.18% of week · this session: 3.69% of 5h, 0.93% of week · now 40% / 10% · 336k tokens
```

**History, with `cc-usage`**

```
  5-hour window: 41% used · resets Thu 22 Oct 19:49
  Weekly:        11% used · resets Thu 29 Oct 02:59

  Last 3 answers
  time            5h    week  tokens  session                             prompt
  Thu 18:53    0.30%   0.08%    211k  claude cli per-task usage tracking  ok then yes i would like to build it, do i need to
  Thu 18:57    0.72%   0.19%    336k  claude cli per-task usage tracking  can it be shown per answer and per session always?
  Thu 18:57    0.15%   0.04%    116k  claude cli per-task usage tracking  lets change the "task" to "Session"

  Sessions, current 5-hour window
    3.82%    4 answers  claude cli per-task usage tracking
```

## Install

```sh
git clone https://github.com/hreinssondev/claude-usage-tracker
claude-usage-tracker/install.sh
```

Then restart any open Claude Code sessions. Running `install.sh` again updates an existing install.

The installer copies `tracker.py` to `~/.claude/usage-tracker/` and adds a Stop hook and a status line to `~/.claude/settings.json`, keeping a backup at `settings.json.bak-usage-tracker`. It also adds a `cc-usage` command to your shell. If you already have a status line, it asks before replacing it, and puts it back when you turn the tracker off.

**Requirements**
- macOS or Linux.
- Python 3.8 or newer. macOS ships with it.
- Claude Code signed in with a Claude subscription (Pro or Max). Accounts that use an API key have no 5-hour or weekly limits to measure.

## Commands

| Command | What it does |
|---|---|
| `cc-usage` | Current limits, last 15 answers, per-session totals |
| `cc-usage 50` | Same, with the last 50 answers |
| `cc-usage off` | Turn off; files and history stay |
| `cc-usage on` | Turn back on |
| `./install.sh --uninstall` | Turn off and remove; your history is first saved to your home folder |

Inside Claude Code you can run any of these with a leading `!`, e.g. `! cc-usage`.

## How it works

1. **Every request gets a weight.** Claude Code writes every API request, with its token counts, to `~/.claude/projects/**/*.jsonl`. The weight is the request's API price: model, input, output, cache-write and cache-read tokens. A cache read counts far less than new input.
2. **Window totals come from Anthropic.** The tracker asks for your exact 5-hour and weekly utilisation from the same endpoint `/usage` uses.
3. **Each answer gets its share.** A window's % is divided across all requests in that window by weight. Answer % = its weight × (window % ÷ total weight in the window). Requests from every session count, so parallel sessions are split correctly, and subagent requests count toward the answer that started them.
4. **Calibration.** The utilisation only moves in whole percents, so early in a window (under 8%) the tracker uses the scale from earlier windows.

## Cost and privacy

- **Uses no tokens.** Everything is a local script. The per-answer message is shown to you only and never reaches the model.
- **Fast.** The hook takes about 0.1 s after each answer, and the status line about 0.05 s; it reads a small file and makes no network call.
- **One network call:** after an answer, at most once every 45 s, to `api.anthropic.com/api/oauth/usage`, using Claude Code's own login token. The token is read from the macOS Keychain or `~/.claude/.credentials.json` and is never stored or sent anywhere else.
- **Local data:** your history (`ledger.jsonl`) stays in `~/.claude/usage-tracker/`. It records prompt previews (first 200 characters), session titles and token counts.

## Accuracy

- **Window totals:** exact, straight from Anthropic.
- **Splitting them by API price:** a close approximation of how the limits weigh models and token types. Anthropic doesn't publish the exact formula.
- **Claude on the web and desktop apps:** chats there count against the same limits but leave no local transcripts. When you use them, CLI answers look slightly more expensive than they are.
- **`CLAUDE_CONFIG_DIR`:** respected for all paths. On macOS, a custom config dir may store the login under a different Keychain entry, in which case the window figures don't load.

## Disclaimer

Not affiliated with Anthropic. It relies on Claude Code's local transcript format and an undocumented usage endpoint, either of which can change without notice. If something breaks, the hook fails silently and never interrupts Claude Code.

## License

MIT
