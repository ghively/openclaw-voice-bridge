# Voice Bridge v3 - Final Code Review

**Date:** 2026-03-31
**Reviewer:** Claude (Sonnet 4.6)
**Status:** ❌ **FAIL - DO NOT DEPLOY**

---

## Executive Summary

This implementation has critical bugs that prevent basic functionality. The `transcribe_audio()` function references undefined variables and will crash on every transcription attempt. Multiple race conditions exist around disconnect/reconnect scenarios. Multi-user audio support is fundamentally broken due to frame discarding in `get_user_frame()`.

**Recommendation:** Blocker issues must be fixed before any testing or deployment.

---

## Detailed Ratings (1-10)

### 1. Bugs & Logic Errors: **5/10** ❌ FAIL

#### Critical Bugs:
- **Lines 367-377 (main.py)**: `transcribe_audio()` references undefined `audio_frames` variable
  - Function receives `audio_bytes: bytes` but checks `if not audio_frames:`
  - Tries to `np.concatenate(audio_frames)` - NameError guaranteed
  - Audio normalization logic flawed for bytes input

#### Logic Errors:
- **audio_sink.py `get_user_frame()` (lines 142-158)**: Discards frames from other users
  - Scans shared queue and drops non-matching frames
  - Causes data loss in multi-user scenarios
  - No per-user queuing as TODO comment suggests

- **Line 309**: Arbitrary `silence_frames += 25` on timeout
  - Doesn't align with actual 20ms frame timing (500ms / 20ms = 25)
  - Should be calculated: `int(0.5 / 0.02)` not hardcoded

- **`pending_audio` (line 122)**: Dead code
  - Defined as Dict[Deque] but never populated
  - Only used in cleanup on line 586
  - Indicates incomplete implementation

- **Line 248**: `voice_client.guild` may be None
  - No null check before accessing `get_member()`

---

### 2. Discord API Correctness: **7/10** ✅ PASS

#### Correct Usage:
- ✅ Uses `discord.ext.voice_recv.VoiceRecvClient` properly
- ✅ `listen(sink)` and `stop_listening()` correctly ordered
- ✅ `wants_opus()` returns False for PCM (correct approach)
- ✅ VoiceData.pcm attribute accessed correctly
- ✅ Intents configured properly (message_content, voice_states, guilds, messages)

#### Minor Issues:
- ⚠️ **Line 573**: `voice_client.channel == after.channel` comparison
  - `voice_client.channel` may not update after bot moves
  - Should use `voice_client.channel.id` comparison

- ⚠️ **Lines 590-591**: Bot move handler incomplete
  - Logs move but doesn't reinitialize sink
  - May need to restart `listen(sink)` after channel move

- ⚠️ **Missing `voice_client.listening` check**: Assumes listening is active

---

### 3. Race Conditions & Concurrency: **5/10** ❌ FAIL

#### Critical Issues:
- **Lines 667-673**: Disconnect sequence is unsafe
  ```
  voice_client.stop_listening()  # Stops sink
  processing_users.discard(user_id)  # Clears set
  await voice_client.disconnect()  # But tasks still running!
  ```
  - `audio_monitor_loop` and `process_user_audio` tasks still active
  - May access None'd `sink` or `voice_client`
  - No task cancellation before disconnect

- **Lines 721-725**: Task cancellation doesn't wait
  ```python
  for task in list(background_tasks):
      task.cancel()  # Cancel requested
  await asyncio.sleep(0.5)  # Hope they finish? Not a proper wait
  ```

- **audio_sink.py `write()` vs `evict_user()`**: Cross-thread race
  - `write()` called from Discord's audio thread
  - `evict_user()` called from async context
  - Both modify `_buffers` with lock, but...

- **Line 233**: TOCTOU on `sink is not None`
  - Check and use are not atomic
  - Could be set to None between check and `sink.get_frame()`

- **Lines 189-192**: Iterating while modifying Set
  ```python
  for path in files:  # Iterating
      cleanup_temp_file(path)  # Modifies _temp_files
  ```
  - `_temp_files_lock` held but still problematic
  - Should copy to list first (already done, but fragile)

- **No backpressure**: `audio_queue` is unbounded
  - Could grow infinitely if processing is slow
  - Memory exhaustion under load

#### Good Practices:
- ✅ Line 252: `processing_users.add()` BEFORE `create_task()` - prevents TOCTOU
- ✅ Line 125: `_tts_lock` for TTS serialization
- ✅ Uses `ThreadPoolExecutor` for blocking operations

---

### 4. Error Handling: **6/10** ⚠️ MARGINAL

#### Missing/Weak:
- **Line 477**: Temp file cleanup only on error
  ```python
  after=lambda e: cleanup_temp_file(file_path) if e else None
  ```
  - Should cleanup on success too
  - After callback runs in thread, may not complete

- **Line 739**: Executor shutdown doesn't wait
  ```python
  executor.shutdown(wait=False)
  ```
  - Pending temp file cleanup may be lost
  - Should use `wait=True` or track completion

- **audio_sink.py lines 170-174**: Queue cleanup no exception handling
  ```python
  while not self.audio_queue.empty():
      self.audio_queue.get_nowait()  # Could raise
  ```

- **`transcribe_audio`**: No handling for:
  - Empty/invalid audio bytes
  - Corrupted WAV data
  - Model failures

- **No retry logic** for:
  - Discord disconnects
  - API rate limits
  - Network failures

#### Good:
- ✅ Most functions have try/except with logging
- ✅ `exc_info=True` for detailed tracebacks
- ✅ Signal handlers registered (SIGINT, SIGTERM)

---

### 5. Memory Management: **6/10** ⚠️ MARGINAL

#### Issues:
- **Line 122**: `pending_audio` holds references but never used
  - Dead code consuming memory
  - Should be removed or implemented

- **Lines 170-174**: Queue cleanup could infinite loop
  - If queue being filled concurrently
  - No timeout or max iteration limit

- **Line 281**: `audio_buffer` grows unbounded
  - If VAD never detects speech end
  - No max size limit

- **No backpressure** on `audio_queue`
  - Could grow indefinitely if processing slow
  - Should use `maxsize` on Queue

- **Line 106**: Thread pool may be undersized
  - `max_workers=4` for STT + TTS operations
  - May bottleneck under concurrent users

#### Good:
- ✅ Line 119: `monitored_messages` deque(maxlen=1000) - bounded
- ✅ Line 129: `_temp_files` tracked for cleanup
- ✅ Line 207: Tasks auto-removed from `background_tasks` via callback

---

### 6. Security: **7/10** ✅ PASS

#### Good:
- ✅ No SQL injection (no database)
- ✅ Bot token from environment variable
- ✅ Line 432: Input validation (MAX_TTS_INPUT_LENGTH=2000)
- ✅ Line 499-503: TTS rate limiting (3s cooldown)
- ✅ Line 548: Skips own messages

#### Issues:
- ⚠️ **Line 197**: `tempfile.mkstemp()` validation
  - TEMP_DIR controlled but no ownership check
  - TOCTOU between mkdir and file creation

- ⚠️ **Line 476**: FFmpeg input from temp file
  - Acceptable (controlled path) but worth noting

- ⚠️ **No command rate limiting**
  - Could spam join/leave commands
  - Should add per-user command cooldown

- ⚠️ **Line 506**: BOT_USER_ID not validated
  - No check that ID is authorized
  - Could allow any bot through TTS

- ⚠️ **Line 763**: TARGET_TEXT_CHANNEL_ID not validated
  - No check that bot has access
  - Will fail silently if wrong

---

### 7. Edge Cases: **5/10** ❌ FAIL

#### Missing/Weak:
- **Empty/invalid audio**: `transcribe_audio` will crash with NameError
- **User joins/leaves rapidly**: Timeout handling arbitrary (line 309)
- **Sink becomes None during processing**: Only checked at loop start (line 275)
- **Multiple users speaking**: `get_user_frame()` discards other users' frames
- **Voice connection lost**: No reconnection logic, tasks orphaned
- **Empty messages**: Line 429 checks `if not text` but after strip
- **Channel deletion**: No handling, will crash on next voice state update
- **Bot kicked**: `on_voice_state_update` not called, tasks continue with stale refs
- **Frame size mismatch**: Line 317 treats wrong-size frames as non-speech (should error)
- **Zero users in channel**: Monitor loop continues but no frames
- **Discord API rate limits**: No handling of 429 responses
- **Model loading failure**: Exits (line 541) but no cleanup

---

## Critical Issues Summary

### Must Fix Before Deployment:

1. **`transcribe_audio()` function broken** (main.py:367-377)
   - References undefined `audio_frames` variable
   - Will crash on every transcription attempt
   - **Fix**: Rewrite to process `audio_bytes` parameter

2. **Multi-user audio loss** (audio_sink.py:142-158)
   - `get_user_frame()` discards frames from other users
   - **Fix**: Implement per-user queues or different architecture

3. **Race condition on disconnect** (main.py:667-673)
   - Tasks still running when resources None'd
   - **Fix**: Cancel tasks before setting globals to None

4. **No reconnection handling**
   - Any network issue breaks bot permanently
   - **Fix**: Implement reconnect logic with exponential backoff

5. **Dead code** (main.py:122)
   - `pending_audio` never used
   - **Fix**: Remove or implement properly

---

## Recommended Actions

### Immediate (Blockers):
1. Fix `transcribe_audio()` to process `audio_bytes` correctly
2. Implement per-user audio queues to prevent frame loss
3. Add task cancellation before disconnect
4. Add reconnection logic for voice client

### High Priority:
5. Add backpressure to audio_queue (maxsize)
6. Fix temp file cleanup in `after` callback
7. Add command rate limiting
8. Validate environment variables on startup

### Medium Priority:
9. Remove or implement `pending_audio`
10. Add proper task waiting on shutdown
11. Improve error handling in queue cleanup
12. Add channel deletion/kick handling

---

## Code Quality Notes

### Good Patterns:
- Clean separation of concerns (main.py vs audio_sink.py)
- Proper use of asyncio for concurrent operations
- ThreadPoolExecutor for blocking operations
- Comprehensive logging throughout

### Needs Improvement:
- Global variable usage makes testing difficult
- Missing type hints in some functions
- Some functions are too long (e.g., `process_user_audio`)
- No unit tests visible

---

## Conclusion

This implementation shows good architectural understanding but has critical bugs that prevent basic functionality. The `transcribe_audio` function alone blocks all transcription. Multi-user support is broken. Race conditions around disconnect could cause crashes.

**Verdict:** ❌ **FAIL - Do NOT deploy**

**Estimated Fix Time:** 4-6 hours for critical issues, 2-3 days for full production readiness.

---

**Reviewed by:** Claude (Sonnet 4.6)
**Review Date:** 2026-03-31
**Files Reviewed:** main.py, audio_sink.py, requirements.txt, .env.example, PLAN.md
