# Discord Voice Bridge v3 Review

Verdict: FAIL

PASS rule: all categories must be >= 7/10. This build does not meet that bar.

## Findings

1. Critical: STT is currently broken at runtime in `transcribe_audio`. The function accepts `audio_bytes` but then references `audio_frames`, which is never defined, so every transcription attempt raises `NameError` before Whisper runs. This prevents the core voice-to-text path from working at all. [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L363) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L367) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L374)

2. Critical: concurrent speakers can destroy each other’s audio. `audio_monitor_loop()` pulls frames from the shared queue to detect a speaker, then each per-user task calls `get_user_frame()`, which scans the same shared queue and explicitly discards frames from other users. Under overlap, one user’s worker consumes and drops another user’s frames, causing missed speech, truncated utterances, and corrupted VAD behavior. This is a fundamental queue design bug, not a tuning issue. [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L224) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L267) [audio_sink.py](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py#L130) [audio_sink.py](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py#L153)

3. High: successful TTS playback leaks temp files. `play_audio()` only calls `cleanup_temp_file()` from the `after` callback when an error object is present; on successful playback, the generated WAV remains tracked and left on disk until process exit. Repeated TTS use will accumulate files in `/tmp/openclaw_voice_bridge`. [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L471) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L477)

4. Medium: leaving voice does not clean the sink’s internal buffers or queue. `leave_voice()` nulls `sink` without calling `sink.cleanup()`, so buffered PCM and queued frames can survive until GC. That is avoidable memory retention and stale-state risk during reconnects. [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L678) [audio_sink.py](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py#L160)

5. Medium: shutdown handling is not robust. The signal handler schedules `shutdown()` but does not stop the bot loop or wait for shutdown completion, so process termination can race cleanup, disconnect, and temp-file removal. `sys.exit(1)` inside `on_ready()` also hard-exits from an async event path. [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L524) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L539) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L713) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L744)

6. Medium: channel scope for TTS is too broad. `on_message()` enqueues TTS for any message in any text channel while connected to voice, not just a configured bridge channel. That can create noisy or surprising playback behavior if the bot exists in multiple guild channels. [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L544) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L555)

7. Low: `pending_audio`, `queue`, and `Any` are dead code/imports. They suggest an abandoned buffering design and increase the chance that future changes will target the wrong state object. [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L21) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L29) [main.py](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L121)

## Scores

- Bugs: 3/10
- API: 6/10
- Concurrency: 3/10
- Error handling: 5/10
- Memory: 6/10
- Security: 7/10
- Edge cases: 4/10

## Rationale

- Bugs `3/10`: the main STT path is non-functional because `transcribe_audio()` references nonexistent state.
- API `6/10`: Discord and dotenv usage is mostly reasonable, but message-to-TTS scope and shutdown lifecycle around discord.py are under-specified.
- Concurrency `3/10`: the shared queue plus per-user scavenging/discard pattern is unsafe for overlapping speakers.
- Error handling `5/10`: exceptions are logged, but several paths only fail late or race cleanup.
- Memory `6/10`: no obvious catastrophic leak, but temp files and sink buffers are not cleaned reliably.
- Security `7/10`: no obvious token exposure or command injection path in the reviewed files; the main risk is operational misuse rather than a direct vulnerability.
- Edge cases `4/10`: reconnects, overlapping talkers, broad channel capture, and shutdown all have unhandled behavior.

## Notes On Reviewed Files

- `.env.example` parses correctly with `python-dotenv`; the inline comments are acceptable.
- `requirements.txt` is minimal and coherent for the intended stack, but it does not offset the runtime defects above.
