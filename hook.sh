#!/bin/bash
# Stop hook: drop one job in the voice queue and exit. All slow work (summary, speech,
# listening) happens in the single voice.py daemon, so nine sessions finishing at once
# line up instead of talking over each other.
#
# Does nothing unless `voice on` is running (flag file) and the session lives in an
# iTerm pane (the reply is typed back into that pane by its ITERM_SESSION_ID).
set -u
HOME_DIR="${CLAUDE_VOICE_HOME:-$HOME/.claude-voice}"
[ -f "$HOME_DIR/on" ] || exit 0
[ -n "${CLAUDE_VOICE_CHILD:-}" ] && exit 0      # the daemon's own `claude -p` summarizer
[ -n "${ITERM_SESSION_ID:-}" ] || exit 0

input="$(cat)"
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = "true" ] && exit 0
msg="$(printf '%s' "$input" | jq -r '.last_assistant_message // empty')"
[ -n "$msg" ] || exit 0

mkdir -p "$HOME_DIR/queue"
tmp="$(mktemp "$HOME_DIR/queue/.tmp.XXXXXX")"
printf '%s' "$input" | jq \
  --arg pane "${ITERM_SESSION_ID#*:}" --argjson ts "$(date +%s)" \
  '{ts:$ts, pane:$pane, session_id, cwd, transcript_path, message:.last_assistant_message}' > "$tmp"
mv "$tmp" "$HOME_DIR/queue/$(date +%s)-$$-$RANDOM.json"   # BSD date has no %N
exit 0
