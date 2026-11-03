"""Run: /opt/homebrew/bin/python3.11 -m unittest discover -s tests -v

The e2e tests use macOS `say` as the "speaker", so they need no human and no mic, but they
do run the real whisper-cli and (for the pane test) a real iTerm window.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import voice  # noqa: E402


def speech_wav(text, path):
    aiff = str(path) + ".aiff"
    subprocess.run(["say", "-v", "Samantha", "-o", aiff, text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-ac", "1", "-ar", "16000", str(path)], check=True)


class StopWord(unittest.TestCase):
    def test_variants_stop(self):
        for spelled in ["skadoosh", "Skadoosh.", "scadoosh", "skedoosh", "ska doosh", "SKADOOSH!"]:
            reply, found = voice.split_at_stop_word(f"yes go ahead {spelled}")
            self.assertTrue(found, spelled)
            self.assertEqual(reply, "yes go ahead", spelled)

    def test_stop_word_mid_sentence_is_just_words(self):
        text = "after I say skadoosh I want a confirmation"
        self.assertEqual(voice.split_at_stop_word(text), (text, False))

    def test_normal_speech_never_stops(self):
        for s in ["merge the shadow branch", "yes please schedule it", "kids do shoes", "ship it to prod",
                  "scale the doo sheet", "approve the push and run the tests"]:
            self.assertFalse(voice.split_at_stop_word(s)[1], s)

    def test_only_stop_word_is_an_empty_reply(self):
        self.assertEqual(voice.split_at_stop_word("Skadoosh."), ("", True))


class Queue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        voice.QUEUE = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, sid, ts):
        (voice.QUEUE / name).write_text(json.dumps({"session_id": sid, "ts": ts, "pane": "p", "message": "m"}))

    def test_fifo_and_newest_per_session_wins(self):
        now = int(time.time())
        self.put("1.json", "a", now - 50)
        self.put("2.json", "b", now - 40)
        self.put("3.json", "a", now - 10)        # replaces a's older turn
        got = [voice.next_job()["session_id"]]
        voice.next_job()["_file"].unlink()
        self.assertEqual(got, ["b"])
        self.assertFalse((voice.QUEUE / "1.json").exists())

    def test_expired_jobs_dropped(self):
        self.put("1.json", "a", int(time.time()) - voice.JOB_MAX_AGE_S - 5)
        self.assertIsNone(voice.next_job())


@unittest.skipUnless(voice.WHISPER_MODEL.exists(), "whisper model missing")
class Listen(unittest.TestCase):
    def run_listen(self, text):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "s.wav"
            speech_wav(text, wav)
            return voice.listen(voice.file_frames(wav), idle_s=4)

    def test_reply_then_stop_word(self):
        reply, why = self.run_listen("Yes, go ahead and merge it. Skadoosh.")
        self.assertEqual(why, "stopword")
        self.assertIn("merge", reply.lower())
        self.assertNotIn("skadoosh", reply.lower())

    def test_two_sentences_with_a_pause_before_stop_word(self):
        reply, why = self.run_listen("Approve the plan. [[slnc 1200]] Also run the tests. [[slnc 1200]] Skadoosh.")
        self.assertEqual(why, "stopword")
        self.assertIn("approve", reply.lower())
        self.assertIn("tests", reply.lower())

    def test_stop_word_alone_skips(self):
        self.assertEqual(self.run_listen("Skadoosh."), ("", "stopword"))

    def test_silence_is_idle(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "s.wav"
            import wave
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 16000)
            # file ends after ~4.5 s of silence; idle gate is 1 s here
            self.assertEqual(voice.listen(voice.file_frames(wav), idle_s=1), ("", "idle"))


@unittest.skipUnless(os.environ.get("ITERM_SESSION_ID"), "needs to run inside iTerm")
class Pane(unittest.TestCase):
    def test_type_into_a_scratch_pane(self):
        """Open a throwaway iTerm window running `cat > file`, type into it by unique id, read the file."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "got.txt"
            uid = subprocess.run(["osascript", "-e", f'''
tell application "iTerm2"
  set w to (create window with default profile command "/bin/sh -c 'cat > {out}'")
  return unique id of current session of w
end tell'''], capture_output=True, text=True).stdout.strip()
            try:
                self.assertTrue(uid)
                self.assertTrue(voice.pane_exists(uid))
                self.assertTrue(voice.type_into_pane(uid, 'yes "go" ahead, it\'s fine'))
                time.sleep(1)
            finally:
                subprocess.run(["osascript", "-e", f'''
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if (unique id of s) is "{uid}" then close w
      end repeat
    end repeat
  end repeat
end tell'''])
            self.assertEqual(out.read_text().strip(), 'yes "go" ahead, it\'s fine')
            self.assertFalse(voice.pane_exists(uid))


@unittest.skipUnless(os.environ.get("ITERM_SESSION_ID") and voice.WHISPER_MODEL.exists(), "needs iTerm + whisper")
class EndToEnd(unittest.TestCase):
    def test_job_to_spoken_summary_to_typed_reply(self):
        """handle(): real Haiku summary, stub voice, fake mic saying the reply, real iTerm pane."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "got.txt"
            wav = Path(d) / "reply.wav"
            speech_wav("Yes, ship it. Skadoosh.", wav)
            uid = subprocess.run(["osascript", "-e", f'''
tell application "iTerm2" to return unique id of current session of (create window with default profile command "/bin/sh -c 'cat > {out}'")'''],
                                 capture_output=True, text=True).stdout.strip()
            spoken = []
            class Stub:
                def say(self, text): spoken.append(text)
            real_mic, real_chime = voice.mic_frames, voice.chime
            voice.mic_frames = lambda: voice.file_frames(wav)
            voice.chime = lambda name: None
            try:
                voice.handle({"pane": uid, "cwd": "/Users/x/jobi", "transcript_path": "/nope", "_file": Path("job.json"),
                              "message": "Fixed the cron typo and all 14 tests pass. Should I push to main?"}, Stub())
                time.sleep(1)
            finally:
                voice.mic_frames, voice.chime = real_mic, real_chime
                subprocess.run(["osascript", "-e", f'''
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if (unique id of s) is "{uid}" then close w
      end repeat
    end repeat
  end repeat
end tell'''])
            self.assertTrue(spoken and spoken[0].lower().startswith("jobi."), spoken)
            self.assertIn("push", spoken[0].lower())
            self.assertEqual(out.read_text().strip().lower().rstrip("."), "yes, ship it")


if __name__ == "__main__":
    unittest.main()
