# claude-voice

Talk to nine Claude Code sessions, one at a time. When a session finishes a turn, a voice
tells you what it did and what it needs, then listens for your reply until you say
**skadoosh**. The reply is typed into the pane that asked.

```
session stops --Stop hook--> queue/*.json --voice.py--> Haiku summary --> Kokoro speaks
                                                                              |
pane gets your reply  <--AppleScript write text--  whisper-cli  <--  mic until "skadoosh"
```

## Use

```
voice on        # in its own pane. Ctrl-C turns it off
voice say "hi"  # check the voice
```

- **One at a time.** hook.sh only queues. One daemon speaks, listens, types, then takes the next job.
- **skadoosh** ends your reply, but only as the last word of what you say (a pause after it); saying it mid-sentence is just words. After it the voice says "Thank you for your reply." at once (pre-rendered, plays while the text is typed in), or "Reply skipped." if you said nothing. Say it alone to skip a session. 20 s of silence also skips.
- A restart or crash mid-job does not lose it: the job stays in `claimed/` until done and is requeued on start. A second `voice on` is refused (flock), because two daemons would both open the mic.
- A newer turn from the same session replaces its older queued one; jobs older than 30 min are dropped.
- While you talk to job 1, jobs 2 and up are already being summarized.

## Parts

| File | Job |
|------|-----|
| `hook.sh` | Stop hook (installed in `~/.claude/settings.json`). Inert unless `voice on` is running. |
| `voice.py` | Daemon: summary (`claude -p --model haiku`), speech (Kokoro `am_adam`, `say` fallback), listening (ffmpeg mic + energy gate + `whisper-cli small.en`), delivery (iTerm AppleScript). |
| `tests/` | `python3.11 -m unittest discover -s tests`. Uses `say` as a fake speaker; opens and closes a scratch iTerm window. |

## Traps

- **iTerm panes are found by `unique id`**, the UUID after the colon in `$ITERM_SESSION_ID`. `session id "..."` errors with -1728.
- **`claude -p` boots every MCP server** (+8 s) unless `--strict-mcp-config`. `--bare` needs an API key, so it is not an option on a login.
- **The summarizer runs hooks too.** `CLAUDE_VOICE_CHILD=1` makes hook.sh ignore its Stop event, or it would queue itself forever.
- **Stop word spelling.** Whisper spells it many ways; `split_at_stop_word` matches by similarity, needs length >= 6 for a single word ("doosh" scores 0.77) and only glues two words when both are short ("ahead"+"skadoosh" would eat a real word).
- **Mic permission** is granted to the terminal app that runs `voice on` (iTerm). Run it from iTerm.
- Timeouts count audio time, not wall time, so recordings and the live mic behave the same.

## Not verified

Live mic with a real voice in a noisy room (threshold = 4x measured floor, min 300 RMS); nine sessions finishing at once on a real day; Kokoro first-load time on a cold start (~15 s).
