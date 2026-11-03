#!/opt/homebrew/bin/python3.11
"""claude-voice: speak a summary of each finished Claude Code turn, listen for a reply.

One daemon, one session at a time. hook.sh queues a job when a session stops; this
process takes the oldest job, has Haiku turn the final message into a short spoken
summary, speaks it (local Kokoro, `say` as fallback), then opens the mic and transcribes
until you say the stop word. The transcript is typed into the pane that finished.

    voice on              run the daemon (foreground; Ctrl-C turns it off)
    voice say "text"      test the voice
    voice listen FILE.wav test listening + stop word on a recording
"""
import argparse
import atexit
import collections
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np



def ensure_path():
    """A window iTerm opens from a "command" profile gets launchd's bare PATH, so `claude`, `ffmpeg`
    and `whisper-cli` vanish (summary silently fell back to the raw message, then the job died).
    Put the usual homes of those tools first, whatever launched us."""
    want = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", str(Path.home() / ".local/bin")]
    have = [p for p in os.environ.get("PATH", "").split(":") if p]
    os.environ["PATH"] = ":".join(want + [p for p in have if p not in want])


ensure_path()
HOME = Path(os.environ.get("CLAUDE_VOICE_HOME", Path.home() / ".claude-voice"))
QUEUE = HOME / "queue"
CLAIMED = HOME / "claimed"      # a job lives here while it is being spoken and answered
FLAG = HOME / "on"
LOG = HOME / "voice.log"

STOP_WORD = "skadoosh"
WHISPER_MODEL = Path.home() / ".cache/whisper.cpp/ggml-small.en.bin"
VOICE, VOICE_SPEED = "am_adam", 1.05
JOB_MAX_AGE_S = 30 * 60      # a reply to a 30 minute old turn is a reply to nothing
THANKS, SKIPPED = "Thank you for your reply.", "Reply skipped."
IDLE_SKIP_S = 20             # nobody talking this long = skip this session, go to the next
MAX_REPLY_S = 180

RATE = 16000
FRAME = 480                  # 30 ms
START_FRAMES = 3             # voiced frames in a row that open a segment
END_SILENCE_FRAMES = 23      # ~0.7 s of quiet closes it (and sets stop-word latency)
MIN_SEGMENT_FRAMES = 12      # shorter than ~0.36 s is a click, not speech
PREROLL_FRAMES = 10

SUMMARY_PROMPT = (
    "You write what a voice assistant says out loud to a developer who runs nine Claude Code "
    "sessions at once and is not looking at the screen. Input: project name, the user's recent "
    "requests, and Claude's final message of this turn. Output 40 to 70 words of plain spoken "
    "English. First one sentence on what got done. Then anything broken, skipped or unverified. "
    "Last, exactly what is needed from the listener: a decision, an approval, an answer, or "
    "'nothing, it is finished'. No markdown, no code, no file paths, no bullet lists, no "
    "em-dashes, no greeting, no sign-off. Say numbers as a person would."
)


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    HOME.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


# ---------- stop word ----------

def split_at_stop_word(text):
    """(reply, found). Whisper spells it many ways, so match by similarity, and also
    across a split ("ska doosh"). Only a stop word that ends the utterance counts."""
    words = text.split()
    norm = [re.sub(r"[^a-z]", "", w.lower()) for w in words]

    def close(c):
        return bool(c) and difflib.SequenceMatcher(None, c, STOP_WORD).ratio() >= 0.75

    # It only counts as the LAST thing said. Segments end on a ~0.7 s pause, so a deliberate
    # "skadoosh" is always final, while "after I say skadoosh I want..." is a sentence.
    def stop_at_end(ws):
        """Words before a trailing stop word, or None."""
        nm = [re.sub(r"[^a-z]", "", w.lower()) for w in ws]
        if nm and len(nm[-1]) >= 6 and close(nm[-1]):     # "doosh" alone scores 0.77: length floor
            return ws[:-1]
        # Split spelling ("ska doosh"): only when both halves are short, otherwise
        # "ahead" + "skadoosh" glues into a near-match and eats the real last word.
        if len(nm) >= 2 and len(nm[-2]) <= 5 and len(nm[-1]) <= 6 and close(nm[-2] + nm[-1]):
            return ws[:-2]
        return None

    rest = stop_at_end(words)
    if rest is not None:
        while (more := stop_at_end(rest)) is not None:   # said it three times
            rest = more
        return " ".join(rest).strip(" ,;:-"), True
    return text.strip(), False


# ---------- listening ----------

class Segmenter:
    """Energy gate that cuts the mic stream into utterances (pauses of ~0.7 s)."""

    def __init__(self, noise_floor):
        self.thr = max(300.0, noise_floor * 4)
        self.pre = collections.deque(maxlen=PREROLL_FRAMES)
        self.buf, self.voiced, self.quiet, self.open = [], 0, 0, False

    def feed(self, frame):
        loud = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2))) > self.thr
        if not self.open:
            self.pre.append(frame)
            self.voiced = self.voiced + 1 if loud else 0
            if self.voiced >= START_FRAMES:
                self.open, self.quiet = True, 0
                self.buf = list(self.pre)
            return None
        self.buf.append(frame)
        self.quiet = 0 if loud else self.quiet + 1
        if self.quiet >= END_SILENCE_FRAMES:
            seg, self.buf, self.open, self.voiced = self.buf, [], False, 0
            self.pre.clear()
            return np.concatenate(seg) if len(seg) - self.quiet >= MIN_SEGMENT_FRAMES else None
        return None


def mic_frames():
    """30 ms int16 frames from the default mic. ffmpeg, not a Python audio lib: it is
    already installed and macOS asks for mic permission once, for the terminal."""
    p = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-i", ":0",
         "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            raw = p.stdout.read(FRAME * 2)
            if len(raw) < FRAME * 2:
                return
            yield np.frombuffer(raw, dtype=np.int16)
    finally:
        p.kill()


def file_frames(path):
    """Same stream from a 16 kHz mono wav, padded with silence (used by tests)."""
    with wave.open(str(path)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    pcm = np.concatenate([np.zeros(RATE // 2, np.int16), pcm, np.zeros(RATE * 3, np.int16)])
    for i in range(0, len(pcm) - FRAME + 1, FRAME):
        yield pcm[i:i + FRAME]


def whisper(seg):
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "seg.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
            w.writeframes(seg.tobytes())
        # --prompt biases the decoder toward the stop word, which it would otherwise
        # spell as "scadoosh", "skidoosh" or drop.
        out = subprocess.run(
            ["whisper-cli", "-m", str(WHISPER_MODEL), "-f", str(wav), "-l", "en", "-nt", "-np",
             "--prompt", "Skadoosh."],
            capture_output=True, text=True, timeout=60).stdout
    return re.sub(r"\[[^\]]*\]|\([^)]*\)", "", out).strip()   # drop [BLANK_AUDIO] etc.


def listen(frames, transcribe=whisper, idle_s=IDLE_SKIP_S, max_s=MAX_REPLY_S):
    """Collect speech until the stop word. Returns (reply_text, why) where why is one of
    stopword | idle | max | eof."""
    spoke, parts, seg = False, [], None
    for n, frame in enumerate(frames):
        if seg is None:
            if n < 10:       # first 0.3 s sets the noise floor
                seg_floor = [frame] if n == 0 else seg_floor + [frame]
                continue
            seg = Segmenter(float(np.median([np.sqrt(np.mean(f.astype(np.float64) ** 2)) for f in seg_floor])))
        utt = seg.feed(frame)
        if utt is not None:
            spoke = True
            text = transcribe(utt)
            log(f"heard: {text!r}")
            reply, found = split_at_stop_word(text)
            if reply:
                parts.append(reply)
            if found:
                return " ".join(parts), "stopword"
        elapsed = n * FRAME / RATE      # audio time, so a recording behaves like a live mic
        if not spoke and not seg.open and elapsed > idle_s:
            return "", "idle"
        if elapsed > max_s:
            return " ".join(parts), "max"
    return " ".join(parts), "eof"


# ---------- speaking ----------

class Speaker:
    def __init__(self):
        self.pipe = None
        self.cache, self.cache_dir = {}, Path(tempfile.mkdtemp(prefix="claude-voice-"))
        try:
            from kokoro import KPipeline
            self.pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
        except Exception as e:
            log(f"kokoro unavailable ({e}); using macOS say")

    def _render(self, text, path):
        import soundfile as sf
        chunks = []
        for _, _, audio in self.pipe(text, voice=VOICE, speed=VOICE_SPEED):
            chunks.append(audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio))
        sf.write(path, np.concatenate(chunks), 24000)

    def warm(self, *phrases):
        """Render fixed phrases once at startup; say(..., cache=True) then plays with no delay."""
        for text in phrases:
            if self.pipe is not None and text not in self.cache:
                path = str(self.cache_dir / f"{abs(hash(text))}.wav")
                self._render(text, path)
                self.cache[text] = path

    def say(self, text, cache=False):
        if cache and text in self.cache:
            subprocess.run(["afplay", self.cache[text]])
        elif self.pipe is None:
            subprocess.run(["say", "-r", "190", text])
        else:
            with tempfile.NamedTemporaryFile(suffix=".wav") as f:
                self._render(text, f.name)
                subprocess.run(["afplay", f.name])


def chime(name):
    subprocess.run(["afplay", f"/System/Library/Sounds/{name}.aiff"])


# ---------- jobs ----------

def recent_requests(transcript_path, n=2):
    """The user's last few prompts, for context. Best effort: the file format is not a contract."""
    out = []
    try:
        for line in Path(transcript_path).expanduser().read_text().splitlines():
            e = json.loads(line)
            if e.get("type") != "user":
                continue
            c = e.get("message", {}).get("content")
            if isinstance(c, list):
                c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
            if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
                out.append(c.strip()[:300])
    except Exception:
        pass
    return out[-n:]


STARTUP_PROMPT = (
    "You write what a voice assistant says out loud when a Claude Code session has just loaded its "
    "context and is waiting for instructions. Input: project name and the session's context report. "
    "Output 25 to 40 words of plain spoken English: the project name is added for you, so start with "
    "the one next action or open item from last time if the report names one, then ask what to work on. "
    "No markdown, no code, no file paths, no em-dashes, no greeting, no sign-off."
)


def starts_fresh(transcript_path):
    """True when the last real prompt of the session is /refresh, i.e. nobody has given it work yet.
    Skips skill-body entries and tool results, which are also stored as user entries."""
    try:
        lines = Path(transcript_path).expanduser().read_text().splitlines()
        for line in reversed(lines):
            e = json.loads(line)
            if e.get("type") != "user":
                continue
            c = e.get("message", {}).get("content")
            if isinstance(c, list):
                c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
            if not isinstance(c, str) or not c.strip() or c.startswith("Base directory for this skill"):
                continue
            return "<command-name>/refresh</command-name>" in c or c.strip() == "/refresh"
    except Exception:
        pass
    return False


def fallback_summary(msg):
    plain = re.sub(r"[`*#>|]|\[([^\]]*)\]\([^)]*\)", r"\1", msg)
    return " ".join(plain.split()[:60])


def summarize(job):
    project = Path(job["cwd"]).name or "unknown"
    fresh = starts_fresh(job["transcript_path"])
    if fresh:
        prompt = f"Project: {project}\n\nContext report:\n{job['message'][:6000]}"
    else:
        prompt = (f"Project: {project}\nUser's recent requests:\n- " +
                  "\n- ".join(recent_requests(job["transcript_path"]) or ["(unknown)"]) +
                  f"\n\nClaude's final message:\n{job['message'][:6000]}")
    env = {k: v for k, v in os.environ.items() if k not in ("ITERM_SESSION_ID", "TERM_SESSION_ID")}
    env["CLAUDE_VOICE_CHILD"] = "1"      # hook.sh ignores this process's own Stop event
    try:
        r = subprocess.run(
            ["claude", "-p", "--model", "haiku", "--no-session-persistence", "--disable-slash-commands",
             "--tools", "", "--strict-mcp-config",     # without it every MCP server boots: ~8 s slower
             "--system-prompt", STARTUP_PROMPT if fresh else SUMMARY_PROMPT],
            input=prompt, env=env, cwd="/tmp", capture_output=True, text=True, timeout=90)
        text = r.stdout.strip()
        if r.returncode == 0 and text:
            return f"{project}. {text}"
        log(f"summarizer failed rc={r.returncode}: {r.stderr.strip()[:200]}")
    except Exception as e:
        log(f"summarizer error: {e}")
    return f"{project}. {fallback_summary(job['message'])}"


def next_job():
    """Oldest live job. A newer job from the same session replaces older ones, and old jobs expire."""
    jobs = []
    for f in sorted(QUEUE.glob("*.json")):
        try:
            j = json.loads(f.read_text())
        except Exception:
            f.unlink(missing_ok=True)
            continue
        j["_file"] = f
        jobs.append(j)
    newest = {}
    for j in jobs:
        newest[j["session_id"]] = max(newest.get(j["session_id"], 0), j["ts"])
    live = []
    for j in jobs:
        if j["ts"] < newest[j["session_id"]] or time.time() - j["ts"] > JOB_MAX_AGE_S:
            j["_file"].unlink(missing_ok=True)
        else:
            live.append(j)
    return min(live, key=lambda j: j["ts"]) if live else None


APPLESCRIPT_FIND = '''
on findSession(uid)
  tell application "iTerm2"
    set n to count of windows
    repeat with i from 1 to n
      try
        repeat with t in tabs of window i
          repeat with s in sessions of t
            if (unique id of s) is uid then return s
          end repeat
        end repeat
      end try
    end repeat
  end tell
  return missing value
end findSession
'''
# Why the try: iTerm's window list changes while we scan it whenever cc opens panes or `voice up`
# opens its window; without it a reply was lost to "Can't get item 2 of every window (-1719)".


def pane_exists(pane):
    for _ in range(3):
        r = subprocess.run(["osascript", "-e", APPLESCRIPT_FIND + '\non run argv\n'
                            '  return (findSession(item 1 of argv) is not missing value) as text\nend run',
                            "--", pane], capture_output=True, text=True)
        if r.stdout.strip() == "true":
            return True
        time.sleep(0.3)
    return False


_WRITE = '''
on run argv
  tell application "iTerm2"
    set n to count of windows
    repeat with i from 1 to n
      try
        repeat with t in tabs of window i
          repeat with s in sessions of t
            if (unique id of s) is (item 1 of argv) then
              if (item 3 of argv) is "yes" then
                tell s to write text (item 2 of argv)
              else
                tell s to write text (item 2 of argv) newline NO
              end if
              return "ok"
            end if
          end repeat
        end repeat
      end try
    end repeat
  end tell
  return "gone"
end run'''


def _write(pane, text, newline):
    """Find the pane and type into it in ONE script. A pane reference returned from a helper is
    index-based, so if a window opens or closes between "find" and "write" (cc opening panes, voice up)
    the text lands in the wrong pane while still reporting success (reproduced: 10 of 15 lost)."""
    r = subprocess.run(["osascript", "-e", _WRITE, "--", pane, text, "yes" if newline else "no"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log(f"type failed: {r.stderr.strip()[:160]}")
    return r.returncode == 0 and r.stdout.strip() == "ok"


def type_into_pane(pane, text):
    """Type `text` into the pane, then press Enter as a separate keystroke.

    One `write text` (text + newline) lands as a single burst, which Claude Code treats as a
    paste: the Enter inside it becomes part of the paste and the reply just sits in the prompt
    box (reproduced with a 323 char reply; short ones slipped through). So: paste, pause, Enter.
    iTerm's AppleScript addresses panes by unique id (the UUID after the colon in
    ITERM_SESSION_ID), not by `session id`."""
    for _ in range(3):
        if _write(pane, text, newline=False):
            time.sleep(0.4)
            for _ in range(3):                  # the text is in the box now: keep trying to send it
                if _write(pane, "", newline=True):
                    return True
                time.sleep(0.3)
            return False
        time.sleep(0.5)
    return False


# ---------- spoken skills ----------

STRONG = {"slash", "skill", "command"}           # unambiguous: "slash dev verify"
WEAK = {"run", "perform", "use", "execute", "launch", "start", "invoke"}
FILLER = {"the", "a", "my", "this"}
DASHES = {"dash", "hyphen"}


def _norm(x):
    return re.sub(r"[^a-z0-9]", "", x.lower())


def skill_names(cwd):
    """Skill folder names: global plus the session's own project."""
    names = set()
    for base in (Path.home() / ".claude/skills", Path(cwd) / ".claude/skills"):
        if base.is_dir():
            names |= {d.name for d in base.iterdir() if d.is_dir() or d.is_symlink()}
    return names


def _english(word):
    try:
        with open("/usr/share/dict/words") as f:
            return word.lower() in {line.strip().lower() for line in f}
    except OSError:
        return True      # no word list: be careful, treat as English


def route_skill(text, names):
    """Turn "slash dev verify on the PR" / "perform DevVerify" into "/dev-verify on the PR".

    Skills need a leading "/", which cannot be said. "slash", "skill" and "command" always
    trigger. Plain verbs (run, use, perform...) trigger only for names that are not ordinary
    English ("use the dev branch" must stay a sentence): hyphenated or coined names like
    dev-verify and handoff qualify, "dev", "refresh" and "wake" need "slash"."""
    words = text.split()
    if not words or _norm(words[0]) not in STRONG | WEAK:
        return text
    strong = _norm(words[0]) in STRONG
    rest = words[1:]
    while rest and (_norm(rest[0]) in FILLER or _norm(rest[0]) in STRONG | WEAK):
        strong = strong or _norm(rest[0]) in STRONG
        rest = rest[1:]
    by_norm = {_norm(n): n for n in names}
    best = None                                   # (score, words used, name)
    for n in (3, 2, 1):
        cand_words = [w for w in rest[:n] if _norm(w) not in DASHES]
        cand = _norm("".join(cand_words))
        if len(rest[:n]) < n or not cand:
            continue
        for key, name in by_norm.items():
            score = difflib.SequenceMatcher(None, cand, key).ratio()
            if score >= 0.86 and (best is None or (score, n) > best[:2]):
                best = (score, n, name)
    if not best:
        return text
    name = best[2]
    if not strong and "-" not in name and _english(name):
        return text
    tail = rest[best[1]:]
    if tail and _norm(tail[0]) in STRONG:         # "run dev verify skill on x"
        tail = tail[1:]
    return " ".join([f"/{name}"] + tail)


SUMMARIES = {}      # job file name -> Future of its spoken summary
_pool = ThreadPoolExecutor(max_workers=2)


def prefetch_forever():
    """Start summarizing every waiting job as soon as it is queued, so job 2 is ready the
    moment you finish job 1. handle() takes the Future, so a job is never summarized twice."""
    while True:
        for f in sorted(QUEUE.glob("*.json")):
            if f.name not in SUMMARIES:
                try:
                    SUMMARIES[f.name] = _pool.submit(summarize, json.loads(f.read_text()))
                except Exception as e:
                    log(f"prefetch failed for {f.name}: {e!r}")
        time.sleep(1)


def handle(job, speaker):
    pane = job["pane"]
    if not pane_exists(pane):
        log(f"pane {pane[:8]} is gone, dropping job")
        return
    fut = SUMMARIES.pop(job["_file"].name, None) or _pool.submit(summarize, job)
    summary = fut.result()
    log(f"speak [{pane[:8]}]: {summary}")
    speaker.say(summary)
    chime("Tink")                               # mic is open
    reply, why = listen(mic_frames())
    log(f"listen ended: {why}, reply={reply!r}")
    project = Path(job["cwd"]).name or "that session"
    if reply:
        routed = route_skill(reply, skill_names(job["cwd"]))
        if routed != reply:
            log(f"skill routed: {reply!r} -> {routed!r}")
            reply = routed
        # Type while the thank-you plays: the confirmation is instant and the next job is not delayed.
        result = {}
        typer = threading.Thread(target=lambda: result.update(ok=type_into_pane(pane, reply)))
        typer.start()
        speaker.say(THANKS, cache=True)
        typer.join()
        log(f"typed {len(reply)} chars into pane {pane} ({project}): {'ok' if result['ok'] else 'FAILED'}")
        if not result["ok"]:
            speaker.say(f"Could not reach {project}.")
    else:
        speaker.say(SKIPPED, cache=True)        # skadoosh alone, or silence: next session


def requeue_claimed():
    """A restart or crash mid-job must not lose the job: put leftovers back in the queue."""
    CLAIMED.mkdir(parents=True, exist_ok=True)
    for f in CLAIMED.glob("*.json"):
        f.rename(QUEUE / f.name)
        log(f"requeued unfinished job {f.name}")


def single_instance():
    """Two daemons would both claim jobs and both open the mic. flock dies with the process."""
    import fcntl
    lock = open(HOME / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("voice is already running (only one daemon may hold the mic)")
    return lock


def run():
    HOME.mkdir(parents=True, exist_ok=True)
    QUEUE.mkdir(exist_ok=True)
    lock = single_instance()                     # kept open for the life of the process  # noqa: F841
    FLAG.touch()
    atexit.register(lambda: FLAG.unlink(missing_ok=True))
    requeue_claimed()
    speaker = Speaker()
    speaker.warm(THANKS, SKIPPED)
    threading.Thread(target=prefetch_forever, daemon=True).start()
    log("voice on. Say 'skadoosh' to finish a reply; skadoosh alone skips the session.")
    while True:
        job = next_job()
        if job is None:
            time.sleep(0.5)
            continue
        claimed = CLAIMED / job["_file"].name
        job["_file"].rename(claimed)             # claim it before the slow part, but keep it until done
        job["_file"] = Path(job["_file"].name)   # handle() only needs the name (summary cache key)
        try:
            handle(job, speaker)
            claimed.unlink(missing_ok=True)
        except Exception as e:
            log(f"job failed: {e!r}")
            claimed.unlink(missing_ok=True)


def up():
    """Make sure the one daemon is running; start it in its own iTerm window if not.

    Safe to call on every `cc`: a held lock means it is already up. The daemon needs a window of
    iTerm's own (not a launchd job) because the mic permission belongs to iTerm. The caller's pane
    is re-selected afterwards so the new window does not steal focus from the cc grid."""
    HOME.mkdir(parents=True, exist_ok=True)
    import fcntl
    probe = open(HOME / "lock", "a+")
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("voice already running")
        return
    probe.close()                                # free again, so the daemon can take it
    origin = os.environ.get("ITERM_SESSION_ID", "").split(":")[-1]
    script = '''
on run argv
  tell application "iTerm2"
    create window with default profile command (item 1 of argv)
    set n to count of windows
    repeat with i from 1 to n
      try
        set w to window i
        repeat with t in tabs of w
          repeat with s in sessions of t
            if (unique id of s) is (item 2 of argv) then
              tell w to select
              tell t to select
              tell s to select
              return
            end if
          end repeat
        end repeat
      end try
    end repeat
  end tell
end run'''
    cmd = shutil.which("voice") or f"{sys.executable} {Path(__file__).resolve()}"
    subprocess.run(["osascript", "-e", script, "--", f"{cmd} on", origin], capture_output=True)
    for _ in range(40):                          # the daemon takes the lock before loading Kokoro
        time.sleep(0.5)
        t = open(HOME / "lock", "a+")
        try:
            fcntl.flock(t, fcntl.LOCK_EX | fcntl.LOCK_NB)
            t.close()
        except BlockingIOError:
            print("voice started")
            return
    print("voice did not start; run `voice on` in a pane to see why")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("on")
    sub.add_parser("up")
    s = sub.add_parser("say"); s.add_argument("text")
    l = sub.add_parser("listen"); l.add_argument("wav")
    a = ap.parse_args()
    if a.cmd == "on":
        run()
    elif a.cmd == "up":
        up()
    elif a.cmd == "say":
        Speaker().say(a.text)
    else:
        print(listen(file_frames(a.wav), idle_s=5))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
