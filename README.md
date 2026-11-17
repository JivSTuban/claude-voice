# claude-voice

Talk to nine Claude Code sessions, one at a time. When a session finishes a turn, a voice
tells you what it did and what it needs, then listens for your reply until you say
**skadoosh**. The reply is typed into the pane that asked.

```
session stops --Stop hook--> queue/*.json --voice.py--> Haiku summary --> Kokoro speaks
                                                                              |
pane gets your reply  <--AppleScript write text--  whisper-cli  <--  mic until "skadoosh"
```

## Setup

macOS with iTerm2 only: replies are typed into the pane with AppleScript, and every Claude Code session
you want spoken for must run in an iTerm pane.

### One paste

Open Claude Code in iTerm on the Mac you want this on and paste this. It asks you one question (the word that
ends your reply), then does the rest of the steps below:

```text
Set up claude-voice on this Mac for me. Read https://github.com/JivSTuban/claude-voice (its README, Setup section) and follow it.

First, before you install or change anything, ask me exactly one question and wait for my answer: "What word should end my spoken reply? One to three words, at least four letters, something you would never end a sentence with. Say 'default' to keep skadoosh." If I give a word, use it in the last step.

Then, without asking me anything else:
1. Check what is already installed (iTerm2, Python 3.10 to 3.12, ffmpeg, jq, whisper-cli, espeak-ng, the claude CLI, ~/.cache/whisper.cpp/ggml-small.en.bin) and install only what is missing with Homebrew. If Homebrew itself is missing, stop and tell me; do not use sudo.
2. Create a venv at ~/.claude-voice/venv with a Python 3.10 to 3.12 and pip install numpy kokoro soundfile. Say that PyTorch is a large download before you start it.
3. If the whisper model is missing, download ggml-small.en.bin (487 MB) from https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin into ~/.cache/whisper.cpp/. Say its size before you start it.
4. Clone the repo to ~/claude-voice and symlink ~/claude-voice/bin/voice into ~/.local/bin, making sure ~/.local/bin is on my PATH.
5. Back up ~/.claude/settings.json, then merge this Stop hook into it without removing any hook already there: command /Users/<me>/claude-voice/hook.sh (use my real home path).
6. Install the i-have-adhd plugin so the on-screen replies are shaped like the voice: claude plugin marketplace add ayghri/i-have-adhd, then claude plugin install i-have-adhd@i-have-adhd, then touch ~/.claude/.i-have-adhd-always.
7. Run voice stop-word "<my word>" (skip this if I said default), then run voice stop-word and show me its output.

Do not run voice on (it takes the microphone and must be started by me from an iTerm pane). Do not print or store any secret. When you finish, give me: what you installed, what you checked and how, what you could not check, and the two things only I can do: allow microphone access for iTerm when macOS asks, and run voice on in an iTerm pane, then finish a turn in another iTerm pane running Claude Code. Also tell me how to undo the always-on step (rm ~/.claude/.i-have-adhd-always).
```

Prefer to do it by hand, or want to see each step? Here they are.

**1. Tools**

```
brew install --cask iterm2
brew install python@3.11 ffmpeg jq whisper-cpp espeak-ng
```

`whisper-cpp` provides `whisper-cli`; `espeak-ng` is Kokoro's fallback for words it does not know. You also
need the [`claude` CLI](https://docs.claude.com/en/docs/claude-code) logged in, since the summaries come from
`claude -p --model haiku`.

**2. Python packages.** Kokoro needs Python 3.10 to 3.12 (3.13 is not supported). Use a venv in the
place the launcher looks first:

```
python3.11 -m venv ~/.claude-voice/venv
~/.claude-voice/venv/bin/pip install numpy kokoro soundfile
```

`kokoro` pulls in PyTorch, so this is a large download, and the first `voice say` fetches the Kokoro
weights from Hugging Face. Skip it and the voice falls back to macOS `say`. To use another Python, set
`CLAUDE_VOICE_PYTHON` instead.

**3. Speech model** (487 MB)

```
mkdir -p ~/.cache/whisper.cpp
curl -L -o ~/.cache/whisper.cpp/ggml-small.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
```

**4. Get it and put `voice` on your PATH**

```
git clone https://github.com/JivSTuban/claude-voice ~/claude-voice
mkdir -p ~/.local/bin && ln -s ~/claude-voice/bin/voice ~/.local/bin/voice   # ~/.local/bin must be on PATH
```

**5. Add the Stop hook** to `~/.claude/settings.json` (merge it with any hooks you already have), then
restart your Claude Code sessions:

```json
{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/Users/YOU/claude-voice/hook.sh"}]}]}}
```

**6. Run it** from an iTerm pane: `voice say "hi"` to hear the voice, then `voice on`. macOS asks for
microphone access for iTerm the first time; allow it, and allow any prompt to let iTerm control iTerm.
Finish a turn in any other iTerm pane running Claude Code and it will speak.

**7. Shape what the sessions put on screen too.** The voice and the screen should read the same way, so the
setup includes the [i-have-adhd](https://github.com/ayghri/i-have-adhd) skill (MIT): action first, numbered
steps, one next action at the end, no preamble or closers. It does the on-screen half; `SUMMARY_PROMPT` in
`voice.py` already applies the same rules to what is spoken, adapted for the ear, so the voice does not depend
on this step.

```
claude plugin marketplace add ayghri/i-have-adhd
claude plugin install i-have-adhd@i-have-adhd
touch ~/.claude/.i-have-adhd-always     # on in every new session; without it, type /i-have-adhd per session
```

Restart your sessions. Undo: `rm ~/.claude/.i-have-adhd-always`, or say "stop adhd mode" in a session. Always-on
loads the rules into every session you start, including scripted `claude -p` runs that read your user
settings, so skip the `touch` if you rely on a fixed output format there.

Also worth having: [change the ending](#change-the-ending), and the 3x3 grid this was built for,
[cc-iterm](https://github.com/JivSTuban/cc-iterm).

## Give your Claude access to it

Copy this into Claude Code (any directory). It installs a small skill so your Claude knows how to run, check, debug and customize claude-voice, and how to write turns that read well aloud:

```text
Install the claude-voice skill for me, using shell commands (not the file-edit tool).
1. Run: ls ~/.claude/skills/claude-voice/SKILL.md. If that file already exists, show me how it differs from the new one and keep mine unless I say otherwise.
2. Run: mkdir -p ~/.claude/skills/claude-voice
3. Run: curl -fsSL -o ~/.claude/skills/claude-voice/SKILL.md https://raw.githubusercontent.com/JivSTuban/claude-voice/main/skills/claude-voice/SKILL.md
4. Read the saved file and tell me in three lines what it now lets you do.
5. Do not run voice on or voice up, and do not edit my settings.json.
Then tell me to start a new Claude Code session, because skills load when a session starts.
```

Already cloned the repo? Link it instead: `ln -s ~/claude-voice/skills/claude-voice ~/.claude/skills/claude-voice`.

Other agents (Codex, Cursor and so on): paste the body of [`skills/claude-voice/SKILL.md`](skills/claude-voice/SKILL.md) into your `AGENTS.md`. The skill format is Claude Code's, so that route is untested.

## Use

```
voice on        # in its own pane. Ctrl-C turns it off
voice say "hi"  # check the voice
```

- **Change the ending.** The word that ends your reply defaults to `skadoosh`; see below.
- **One at a time.** hook.sh only queues. One daemon speaks, listens, types, then takes the next job.
- **skadoosh** ends your reply, but only as the last word of what you say (a pause after it); saying it mid-sentence is just words. After it the voice says "Thank you for your reply." at once (pre-rendered, plays while the text is typed in), or "Reply skipped." if you said nothing. Say it alone to skip a session. 20 s of silence also skips.
- A restart or crash mid-job does not lose it: the job stays in `claimed/` until done and is requeued on start. A second `voice on` is refused (flock), because two daemons would both open the mic.
- A newer turn from the same session replaces its older queued one; jobs older than 30 min are dropped.
- While you talk to job 1, jobs 2 and up are already being summarized.

## Change the ending

```
voice stop-word                 # show the current one and where it comes from
voice stop-word over and out    # set it (1 to 3 words, letters only, 4+ letters in all)
voice stop-word --reset         # back to skadoosh
```

It is saved in `~/.claude-voice/stop-word` and applies from the next reply, no restart. The env var
`CLAUDE_VOICE_STOP_WORD` wins over the file (handy for one session). A value that cannot work (too short,
too many words) is ignored and the default is used.

Everything that makes `skadoosh` safe still applies to your word: it only counts as the **last** thing
you say, so "when I say over and out you stop" stays a sentence, and the spoken "thank you" / "skipped"
replies work the same. Matching is by similarity because whisper spells words many ways: a word of 7+
letters tolerates a slip or a split ("ska doosh"), a shorter one has to match exactly. So pick something
distinctive you would not end a sentence with, two syllables or more. Test a candidate on a recording
before you trust it: `voice listen reply.wav`.

## How the summary is written

Speech is heard once and cannot be skimmed, so the summarizer prompt follows the output rules of
[ayghri/i-have-adhd](https://github.com/ayghri/i-have-adhd) (MIT), adapted for the ear: it names the kind of
need first ("Decision needed on the rollout." or "Nothing needed."), then says what now works, then only what
is broken or unverified, stated flatly with cause and fix, and ends on one question with its choices ("Say A
or B?"). 30 to 60 words, one ask, at most three choices with the recommended one first, no markdown, paths,
filler or sign-off. The prompt is `SUMMARY_PROMPT` in `voice.py`; edit it to taste.

Checked on 4 made-up turns (an approval, a clean finish, a failing test, an 8-item review) against the old
prompt: every output kept to 50 to 59 words with the need first and the question last, and none had markdown or
file paths. That is a spot check by reading, not a blind evaluation.

## Start with cc

`voice up` starts the daemon in its own iTerm window unless one already holds the lock (the mic
permission belongs to iTerm, so a launchd job will not do). `~/.zshrc` wraps `cc` so every launch
runs it; the wrapper is personal and stays out of the public cc-iterm repo. A session that just ran
`/refresh` gets a 25 to 40 word "next action from last time, what should I work on?" instead of the
turn summary, so a fresh 3x3 grid greets you one session at a time.

## Skills by voice

Skills need a leading `/`, which cannot be said. A reply that **starts** with a trigger word is turned
into the slash command before it is typed:

| You say | Typed |
|---------|-------|
| "slash dev verify" | `/dev-verify` |
| "skill social post about cc-iterm" | `/social-post about cc-iterm` |
| "run the dev review on this diff" | `/dev-review on this diff` |
| "run handoff" | `/handoff` |

`slash`, `skill`, `command` always work. Plain verbs (run, use, perform, execute...) only work for
names that are not ordinary English, so "use the dev branch" stays a sentence; for `dev`, `refresh`,
`wake` etc. say "slash". A skill named mid-sentence is not rewritten (a slash command only works at the
start of a message anyway); say it in words and the session picks the skill itself. Names come from
`~/.claude/skills` and the session's `.claude/skills`; plugin skills (`plugin:skill`) are not routed yet.

## Parts

| File | Job |
|------|-----|
| `hook.sh` | Stop hook (installed in `~/.claude/settings.json`). Inert unless `voice on` is running. |
| `voice.py` | Daemon: summary (`claude -p --model haiku`), speech (Kokoro `am_adam`, `say` fallback), listening (ffmpeg mic + energy gate + `whisper-cli small.en`), delivery (iTerm AppleScript). |
| `tests/` | `python3.11 -m unittest discover -s tests`. Uses `say` as a fake speaker; opens and closes a scratch iTerm window. |

## Traps

- **A reply typed in one burst sits unsent.** Claude Code reads a long burst (323 chars reproduced it) as a paste and the Enter inside it is swallowed. `type_into_pane` pastes with `newline NO`, waits 0.4 s, then sends Enter alone.
- **Find and type in ONE AppleScript.** A pane reference handed back from a helper is index-based: if a window opens or closes before the write, the text lands in another pane and still reports success (stress test: 10 of 15 lost). `_write` finds and types atomically; Enter is its own find-and-type step; both retry.
- **iTerm panes are found by `unique id`**, the UUID after the colon in `$ITERM_SESSION_ID`. `session id "..."` errors with -1728.
- **`claude -p` boots every MCP server** (+8 s) unless `--strict-mcp-config`. `--bare` needs an API key, so it is not an option on a login.
- **The summarizer runs hooks too.** `CLAUDE_VOICE_CHILD=1` makes hook.sh ignore its Stop event, or it would queue itself forever.
- **Stop word spelling.** Whisper spells it many ways; `split_at_stop_word` matches by similarity, needs length >= 6 for a single word ("doosh" scores 0.77) and only glues two words when both are short ("ahead"+"skadoosh" would eat a real word).
- **Mic permission** is granted to the terminal app that runs `voice on` (iTerm). Run it from iTerm.
- Timeouts count audio time, not wall time, so recordings and the live mic behave the same.

## Not verified

The "Give your Claude access" install prompt was run once headless in a throwaway home directory with its shell commands pre-approved (an interactive session will ask you to approve them); other agents reading `SKILL.md` as `AGENTS.md` is untested;
The Setup steps on a clean machine (the package names, the 487 MB model URL and Kokoro's Python range were checked against Homebrew, Hugging Face and PyPI, and the launcher's Python lookup was tested, but nobody has run steps 1 to 7 start to finish on a fresh Mac; the one-paste prompt above was written from these steps and not yet run on a fresh Mac; the macOS permission prompts are described from how the pieces work, not seen; the `claude plugin` subcommands in step 7 exist in the CLI and the always-on hook was read, but the plugin was not installed or run);
A custom stop word with a real voice (the matching is unit-tested on text, and "over and out", "abracadabra" and "done" each ended a reply in a macOS `say` recording through whisper small.en, but a human voice and accent are untested); live mic with a real voice in a noisy room (threshold = 4x measured floor, min 300 RMS); nine sessions finishing at once on a real day; Kokoro first-load time on a cold start (~15 s).
