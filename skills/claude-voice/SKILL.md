---
name: claude-voice
description: Use when the user asks to set up, start, stop, check, debug or customize claude-voice (spoken summaries of finished Claude Code turns, voice replies typed back into the iTerm pane), or to change the word that ends a spoken reply. Also use when writing a final message that will be read aloud by it.
---

# claude-voice

claude-voice speaks a short summary each time a Claude Code session finishes a turn, listens for the user's reply until their stop word (default `skadoosh`), and types that reply into the iTerm pane that asked. One daemon, one session at a time. macOS and iTerm2 only.

Repo: https://github.com/JivSTuban/claude-voice (its README has the full setup).

## Where things live

| Thing | Where |
|---|---|
| Daemon state | `~/.claude-voice/` (override with `CLAUDE_VOICE_HOME`) |
| Daemon running? | `~/.claude-voice/on` exists |
| Log | `~/.claude-voice/voice.log` |
| Queued / in-progress jobs | `~/.claude-voice/queue/`, `~/.claude-voice/claimed/` |
| Stop word | `~/.claude-voice/stop-word`, or env `CLAUDE_VOICE_STOP_WORD` (the env var wins) |
| Python venv (optional) | `~/.claude-voice/venv`, or env `CLAUDE_VOICE_PYTHON` |
| The Stop hook | `hook.sh` in the repo, registered under `hooks.Stop` in `~/.claude/settings.json` |

## What you may run, and what you must not

Fine to run:
- `voice stop-word` shows the current stop word and where it came from.
- `voice stop-word over and out` sets it (1 to 3 words, letters only, 4 or more letters in total). `voice stop-word --reset` restores `skadoosh`. It applies from the next reply, no restart.
- `voice say "text"` speaks out loud. Tell the user before you do.
- `voice listen file.wav` tests the stop word on a recording.
- Reading the log and listing `~/.claude-voice/`.

Never run `voice on` or `voice up` yourself. They open the microphone, and macOS grants that permission to iTerm, so the user must start them from an iTerm pane. Back up `~/.claude/settings.json` and merge into it; never overwrite existing hooks.

## Check that it is healthy

1. `ls ~/.claude-voice/on` exists, or the daemon is not running (the user starts it with `voice on` in an iTerm pane).
2. `grep hook.sh ~/.claude/settings.json` shows the Stop hook.
3. The session being spoken for runs in an iTerm pane (the hook needs `ITERM_SESSION_ID`).
4. `tail -40 ~/.claude-voice/voice.log` shows `speak`, `heard` and `typed ... ok` lines for a recent turn.

## Known problems and fixes

- **Reply typed but not sent:** a long burst is read as a paste and the Enter is swallowed. The daemon already pastes, waits 0.4 s, then sends Enter alone. If it recurs, check the log for the target pane.
- **Reply went to the wrong pane:** panes must be found and typed into in one AppleScript. Update to the latest repo.
- **No summary spoken:** `claude`, `ffmpeg` or `whisper-cli` is not on PATH in the daemon's window, or the summarizer failed (look for `summarizer failed` in the log).
- **Nothing hears the user:** microphone permission belongs to iTerm; run `voice on` from iTerm.
- **Stop word not recognized:** pick something distinctive of 2 or more syllables that the user would never end a sentence with, then test it on a recording with `voice listen file.wav`. Matching is by similarity: 7 or more letters tolerate a slip, shorter words must match exactly. The word only counts as the last thing said.

## Write turns that read well aloud

The daemon has a small model turn your last message into 30 to 60 spoken words: the kind of need first, what now works, anything broken, then one question. Help it:
- End every turn with the single thing you need from the user, with the choices named and your recommendation first.
- State what now works in concrete terms, and say plainly what you did not verify.
- Avoid long lists, file paths and code blocks in the closing message; put them earlier in the turn.
