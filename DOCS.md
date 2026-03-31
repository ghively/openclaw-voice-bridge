# OpenClaw Voice Bridge Bot Technical Documentation

## Overview

This project is a Discord voice bridge bot built on `discord.py` plus `discord-ext-voice-recv`. Its job is to:

1. Join a Discord voice channel.
2. Receive live PCM audio from speaking users.
3. Segment speech with WebRTC VAD.
4. Transcribe speech locally with Faster-Whisper.
5. Post the resulting text into a configured Discord text channel.
6. Read text messages back into the voice channel with Piper TTS.

The implementation is concentrated in two files:

- [`main.py`](/Users/ghively/Projects/openclaw-voice-bridge/main.py)
- [`audio_sink.py`](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py)

## Architecture Overview

### End-to-end flow

The intended runtime pipeline is:

1. A user speaks in a Discord voice channel.
2. Discord delivers voice packets to the bot's receive client.
3. `discord-ext-voice-recv` decodes Opus to PCM because `VoiceBridgeSink.wants_opus()` returns `False`.
4. `VoiceBridgeSink.write()` receives decoded stereo PCM, converts it to mono, slices it into 20 ms frames, and queues those frames for downstream processing.
5. `audio_monitor_loop()` polls the sink for queued frames and starts a dedicated `process_user_audio()` task the first time it sees a given speaker.
6. `process_user_audio()` pulls frames for one user, runs WebRTC VAD on each 20 ms frame, accumulates speech plus trailing silence, and ends an utterance when silence or the maximum utterance length threshold is reached.
7. `transcribe_audio()` converts the buffered mono PCM to normalized `float32`, resamples it from 48 kHz to 16 kHz with `scipy.signal.resample_poly`, and calls Faster-Whisper in the thread pool.
8. The transcription is posted into `TARGET_TEXT_CHANNEL_ID`.
9. Separately, `on_message()` watches incoming Discord messages while the bot is connected to voice and spawns `process_tts()` tasks.
10. `process_tts()` rate-limits per author, filters most bot accounts, synthesizes a temporary WAV file with Piper, and plays it into the active voice channel through `discord.FFmpegPCMAudio`.

### Actual current behavior

The current code matches most of the architecture above, but there is an important sink implementation issue in [`audio_sink.py:106`](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py#L106):

- `write()` pushes frames into `self.audio_queue`, but `VoiceBridgeSink` defines `_user_queues` and does not initialize `audio_queue`.
- `get_frame()` and `get_user_frame()` both read from `_user_queues`, but `write()` never populates those queues.

As written, the per-user queue pipeline described by the comments is not fully wired up in code. This should be treated as a known issue in the current version.

## Component Breakdown

### `VoiceBridgeSink`

Defined in [`audio_sink.py`](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py).

Responsibilities:

- Inherits from `voice_recv.AudioSink`.
- Requests decoded PCM instead of raw Opus by returning `False` from `wants_opus()`.
- Accepts `voice_recv.VoiceData` in `write()`.
- Converts Discord PCM from stereo to mono with NumPy.
- Buffers partial user audio until a full 20 ms frame exists.
- Exposes frame-polling methods:
  - `get_frame(timeout)` for "next speaker" detection.
  - `get_user_frame(user_id, timeout)` for per-user consumption.
- Clears in-memory buffers on `cleanup()` and `evict_user()`.

Internal state:

- `_buffers: Dict[int, bytearray]`
  Holds partial mono PCM per user.
- `_user_queues: Dict[int, queue.Queue]`
  Intended to hold isolated frame queues per user.
- `_lock`
  Protects queue dictionary access.
- `_buffer_lock`
  Protects byte accumulation and frame extraction.

### `DiscordVoiceBridge`

There is no class literally named `DiscordVoiceBridge` in the current codebase. The bridge is implemented procedurally in [`main.py`](/Users/ghively/Projects/openclaw-voice-bridge/main.py) using global state plus Discord event handlers and commands.

The effective bridge responsibilities are split across:

- Global connection state:
  - `voice_client`
  - `sink`
  - `_connected`
  - `_leaving`
- Background orchestration:
  - `audio_monitor_loop()`
  - `process_user_audio()`
  - `process_tts()`
- Discord integration:
  - `on_ready()`
  - `on_message()`
  - `on_voice_state_update()`
  - `!join`
  - `!leave`
  - `!status`

### VAD

Implemented in [`process_user_audio()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L265).

Behavior:

- Creates a `webrtcvad.Vad` instance per speaking task.
- Uses `VAD_AGGRESSIVENESS` from the environment.
- Operates on 20 ms mono PCM frames at 48 kHz.
- Starts buffering when `vad.is_speech()` first returns `True`.
- Includes trailing silence frames in the utterance buffer for smoother transcription boundaries.
- Ends an utterance when:
  - silence reaches `VAD_SILENCE_DURATION`, or
  - no frames arrive for 500 ms long enough to exceed the same silence threshold, or
  - speech reaches `MAX_UTTERANCE_DURATION`.
- Skips transcription if the final buffered utterance is shorter than 0.5 seconds.

### STT

Implemented across:

- [`load_stt_model()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L136)
- [`transcribe_audio()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L361)

Behavior:

- Loads Faster-Whisper on startup in `on_ready()`.
- Uses `.env` settings for model size, device, and compute type.
- Converts PCM `int16` to normalized `float32`.
- Resamples 48 kHz input to 16 kHz for Whisper.
- Calls `stt_model.transcribe()` with:
  - `language="en"`
  - `beam_size=5`
  - `vad_filter=False`
- Concatenates all segment texts into a single message.
- Posts the result to the configured text channel with `bot.fetch_channel()`.

### TTS

Implemented across:

- [`load_tts_model()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L150)
- [`process_tts()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L494)
- [`speak_text()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L418)
- [`generate_tts_audio()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L450)
- [`play_audio()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L476)

Behavior:

- Loads a Piper voice model on startup from `PIPER_MODEL_PATH`.
- Watches incoming Discord messages when the bot is connected to voice.
- Applies a per-user cooldown of 3 seconds.
- Skips bot authors unless the message author ID matches `BOT_USER_ID`.
- Truncates TTS input to 2000 characters.
- Generates mono WAV audio to a tracked temporary file.
- Plays the file through FFmpeg into the connected voice channel.
- Serializes playback with `_tts_lock`, so only one TTS generation/playback sequence runs at a time.

Important scope note:

- The current implementation does not restrict TTS to `TARGET_TEXT_CHANNEL_ID`.
- Any message visible to the bot can trigger TTS while the bot is connected, subject to the bot filtering and cooldown rules in [`process_tts()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L494).

## Audio Pipeline Details

### Input format from Discord

According to the sink code comments in [`audio_sink.py:75`](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py#L75), decoded input is assumed to be:

- 48,000 Hz sample rate
- Stereo
- 16-bit signed PCM
- Little-endian

### Stereo to mono conversion

`VoiceBridgeSink.write()` converts stereo to mono by:

1. Interpreting the incoming bytes as `np.int16`.
2. Dropping the last sample if the sample count is odd.
3. Reshaping to `(-1, 2)` to form left/right pairs.
4. Averaging both channels with `mean(axis=1)`.
5. Casting the result back to `int16`.

This is a simple arithmetic downmix:

- Input layout: `[L, R, L, R, ...]`
- Output layout: `[avg(L, R), avg(L, R), ...]`

### Frame sizing

The sink defines:

- `SAMPLE_RATE = 48000`
- `FRAME_DURATION_MS = 20`
- `FRAME_SIZE = 960` samples
- `BYTES_PER_SAMPLE = 2`
- `FRAME_BYTES = 1920`

Interpretation:

- One 20 ms mono frame at 48 kHz contains 960 samples.
- At 16 bits per sample, each frame is 1920 bytes.
- This matches WebRTC VAD's accepted frame duration requirements.

### VAD-stage buffering

`process_user_audio()` expects complete 20 ms mono frames from the sink.

It keeps:

- `audio_buffer`
  The utterance under construction.
- `speech_frames`
  Count of speech frames accepted by VAD.
- `silence_frames`
  Count of trailing or timeout-derived silence frames.

Threshold calculations:

- Silence threshold:
  `int(VAD_SILENCE_DURATION * 1000 / 20)`
- Max utterance threshold:
  `int(MAX_UTTERANCE_DURATION * 1000 / 20)`
- Minimum transcription length:
  `0.5 * 48000 * 2 = 48000` bytes

### Resampling for Whisper

Whisper expects 16 kHz audio. The bridge converts 48 kHz mono PCM to 16 kHz with:

- `target_rate = 16000`
- `gcd = np.gcd(48000, 16000) = 16000`
- `up = 1`
- `down = 3`
- `scipy.signal.resample_poly(audio_array, 1, 3)`

So the effective resampling ratio is 48 kHz -> 16 kHz by decimation with polyphase filtering.

### TTS output path

Piper synthesis produces one or more `AudioChunk` objects. The code:

- Concatenates all `audio_int16_array` payloads.
- Uses the sample rate reported by the first chunk.
- Writes a mono 16-bit WAV file.
- Hands that WAV to `discord.FFmpegPCMAudio` for playback.

The code does not resample Piper output manually; FFmpeg is relied on during playback.

## Concurrency Model

### Event loop plus worker pool

The project mixes `asyncio` with blocking libraries by using:

- The main Discord event loop for bot I/O and orchestration.
- A `ThreadPoolExecutor(max_workers=4)` for CPU-bound or blocking work.

The executor is used for:

- `sink.get_frame()`
- `sink.get_user_frame()`
- model loading
- Faster-Whisper transcription
- Piper synthesis

### Task model

The main background async tasks are:

- One `audio_monitor_loop()` task after `!join`
- Zero or one `process_user_audio()` task per actively tracked speaker
- One `process_tts()` task per incoming Discord message while connected

`background_tasks` stores task references so they can be cancelled during shutdown.

### Per-user isolation

The intended design in [`audio_sink.py`](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py) is per-user queue isolation:

- each Discord user has a dedicated `queue.Queue`
- the monitor loop detects active speakers
- one async task consumes frames for that specific user

Why this matters:

- overlapping speakers should not share a single byte buffer
- VAD decisions remain local to one user
- utterance boundaries stay independent

Current implementation caveat:

- the queueing methods and the write path are inconsistent, so the isolation design is partially implemented but not completely connected in code

### Synchronization primitives

The code uses:

- `threading.Lock` for temp-file tracking and sink-internal structures
- `asyncio.Lock` for TTS serialization
- `queue.Queue` as the intended thread-safe boundary between sink callbacks and async processing
- `set` collections such as `processing_users` and `background_tasks` for in-process coordination

### Backpressure behavior

There is no explicit queue size limit, circuit breaker, or speaker fairness policy in the current code. If transcription or synthesis falls behind:

- TTS work serializes behind `_tts_lock`
- monitor and per-user tasks continue to schedule work
- memory use can grow with buffered audio and accumulated temp files if cleanup is blocked by failures

## Discord API Usage

### Core libraries

- `discord.py`
- `discord-ext-voice-recv`

### Intents

Configured in [`main.py:87-91`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L87):

- `message_content = True`
- `voice_states = True`
- `guilds = True`
- `messages = True`

### Voice receive

The bot joins voice with:

- `target_channel.connect(cls=voice_recv.VoiceRecvClient, self_mute=False, self_deaf=False)`

After connecting:

- a `VoiceBridgeSink` instance is created
- `voice_client.listen(sink)` starts receive processing

On leave/shutdown:

- `voice_client.stop_listening()` is called if active
- the voice client is disconnected
- sink state is cleaned up

### Relevant `discord-ext-voice-recv` objects

- `voice_recv.VoiceRecvClient`
  Used instead of a plain `discord.VoiceClient` so the bot can receive voice.
- `voice_recv.AudioSink`
  Base class for custom sink implementation.
- `voice_recv.VoiceData`
  Delivered to `VoiceBridgeSink.write()`, with decoded PCM available at `.pcm`.

### Message handling

`on_message()`:

- ignores only the bot's own messages
- appends message IDs to `monitored_messages`
- schedules TTS processing whenever the bot is connected to voice
- then calls `bot.process_commands(message)`

Because TTS scheduling happens before command processing, command messages can also be eligible for TTS while connected.

### Voice state handling

`on_voice_state_update()` currently:

- logs when users join the bot's current voice channel
- logs when users leave it
- removes departing users from `processing_users`
- logs if the bot is moved to another channel

It does not currently force sink-level eviction for a departing user.

## Configuration Reference

The code reads configuration directly from environment variables at import time in [`main.py:44-69`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L44).

### Required variables

#### `DISCORD_BOT_TOKEN`

- Purpose: Discord bot token used by `bot.run()`
- Required: yes
- Default: none
- Failure mode: the process exits in `main()` if missing

#### `TARGET_TEXT_CHANNEL_ID`

- Purpose: Discord channel ID where transcriptions are posted
- Required: yes
- Default: `0`
- Type: integer
- Failure mode: the process exits in `main()` if unset or zero

#### `PIPER_MODEL_PATH`

- Purpose: path to the Piper `.onnx` voice model file
- Required: yes
- Default: none
- Failure mode: the process exits in `main()` if unset or the file does not exist

### Optional variables

#### `BOT_USER_ID`

- Purpose: allow exactly one bot account to pass the TTS bot-author filter
- Required: no
- Default: `0`
- Type: integer
- Effect: messages from bot users are skipped unless `message.author.id == BOT_USER_ID`

#### `WHISPER_MODEL_SIZE`

- Purpose: Faster-Whisper model size
- Required: no
- Default: `base`

#### `WHISPER_DEVICE`

- Purpose: Faster-Whisper execution device
- Required: no
- Default: `cpu`
- Typical values: `cpu`, `cuda`

#### `WHISPER_COMPUTE_TYPE`

- Purpose: Faster-Whisper compute precision / quantization setting
- Required: no
- Default: `int8`
- Typical values depend on installed backend and hardware

#### `VAD_SILENCE_DURATION`

- Purpose: amount of silence after speech before an utterance is finalized
- Required: no
- Default: `1.5`
- Units: seconds

#### `VAD_AGGRESSIVENESS`

- Purpose: WebRTC VAD aggressiveness level
- Required: no
- Default: `3`
- Valid range for WebRTC VAD: `0` to `3`

### Hard-coded runtime settings

These are not currently configurable through `.env`:

- `SAMPLE_RATE = 48000`
- `CHANNELS = 1`
- `TTS_COOLDOWN = 3.0`
- `MAX_UTTERANCE_DURATION = 30.0`
- `MAX_TTS_INPUT_LENGTH = 2000`
- temp directory = `/tmp/openclaw_voice_bridge`
- thread pool size = `4`

### Configuration values mentioned in `README.md` but not used by code

The current code does not read or use:

- `PIPER_CONFIG_PATH`
- `SAMPLE_RATE` from `.env`
- `CHANNELS` from `.env`

If these appear in local setup notes, treat them as documentation drift unless the code is updated.

## Commands Reference

### `!join`

Defined in [`main.py:601`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L601).

Behavior:

- Requires the invoking user to already be in a voice channel.
- Rejects the request if the bot is already connected.
- Connects using `voice_recv.VoiceRecvClient`.
- Constructs a new `VoiceBridgeSink`.
- Starts `voice_client.listen(sink)`.
- Launches `audio_monitor_loop()`.

Failure behavior:

- Sends an error message back to Discord.
- Resets `voice_client` to `None`.
- Calls `sink.cleanup()` before nulling it.

Implementation note:

- In the exception path, `sink.cleanup()` is called without guarding against `sink` being `None`. If connection fails before sink creation, this can raise a secondary error.

### `!leave`

Defined in [`main.py:659`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L659).

Behavior:

- Returns a message if the bot is not connected.
- Sets `_leaving = True`.
- Calls `stop_listening()` if receive mode is active.
- Removes all currently tracked user IDs from `processing_users`.
- Disconnects from the voice channel.
- Calls `sink.cleanup()`.
- Clears connection state.

### `!status`

Defined in [`main.py:697`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L697).

Reports:

- connection state
- listening state
- number of processing users
- background task count
- temp file count
- current voice channel name if connected

## Startup and Shutdown Lifecycle

### Startup

1. `.env` is loaded via `load_dotenv()`.
2. `main()` validates required configuration.
3. temp-file cleanup is registered with `atexit`.
4. signal handlers for `SIGINT` and `SIGTERM` are registered.
5. `bot.run(DISCORD_BOT_TOKEN)` starts the Discord client.
6. `on_ready()` checks for `ffmpeg` in `PATH`.
7. `on_ready()` loads Faster-Whisper and Piper in the executor.

### Shutdown

`shutdown()`:

- cancels background tasks
- waits briefly
- stops listening if needed
- disconnects voice
- deletes tracked temp files
- shuts down the executor with `wait=False`

Signal handling:

- `signal_handler()` schedules `shutdown()` on the active event loop

## Security Considerations

### Local model execution

STT and TTS run locally, which is good for privacy compared to cloud APIs. Voice content is still handled in memory and written temporarily to disk for TTS playback.

### Token handling

- `DISCORD_BOT_TOKEN` is loaded from environment variables.
- It should never be committed to source control.
- Use a dedicated bot token, not a user token.

### Message scope

The bot currently speaks messages from any visible text channel while connected, not just the bridge channel. This has privacy and abuse implications:

- unintended channels can trigger speech output
- moderators may not expect cross-channel voice playback
- command messages or sensitive bot output may be spoken aloud

If channel isolation matters, `process_tts()` or `on_message()` should explicitly filter by channel ID.

### Mention and content replay risks

The bridge does not sanitize or redact:

- mentions
- URLs
- secrets pasted into chat
- offensive or malicious text

If the target bot or users produce raw content, the bridge can speak it aloud verbatim.

### Filesystem considerations

- TTS output is written to `/tmp/openclaw_voice_bridge`
- cleanup is best-effort, not transactional
- crashes can leave temporary WAV files behind until the next cleanup pass

### Permissions

The bot should be granted only the Discord permissions it needs:

- connect to voice
- speak
- read messages
- send messages
- read message history
- message content intent if required by your bot scope

Avoid broad administrative permissions.

## Troubleshooting

### The bot starts but voice transcription never happens

Likely causes:

- the sink queue pipeline is incomplete in the current implementation
- `VoiceBridgeSink.write()` queues to `self.audio_queue`, while consumers read `_user_queues`

What to check:

- logs for errors originating from [`audio_sink.py:106`](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py#L106)
- whether the installed `discord-ext-voice-recv` base class happens to provide `audio_queue`
- whether per-user queues are being created anywhere

### `ffmpeg not found`

Cause:

- `check_ffmpeg()` exits if `ffmpeg` is missing from `PATH`

Fix:

- install FFmpeg
- verify `ffmpeg -version` works in the same shell environment used to start the bot

### Bot joins voice but does not speak messages

Check:

- `PIPER_MODEL_PATH` points to an existing `.onnx` file
- the bot is still connected and `voice_client.is_connected()` is true
- messages are not being rate-limited by the 3-second per-user cooldown
- the message author is not a bot excluded by `BOT_USER_ID`

### Bot exits during startup

Common causes:

- missing `DISCORD_BOT_TOKEN`
- missing or zero `TARGET_TEXT_CHANNEL_ID`
- invalid `PIPER_MODEL_PATH`
- Faster-Whisper or Piper model load failure in `on_ready()`

### No messages appear in the target text channel

Check:

- `TARGET_TEXT_CHANNEL_ID` is correct
- the bot can access that channel
- the bot has permission to send messages there
- logs for `discord.NotFound` or send exceptions in [`transcribe_audio()`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L361)

### Speech is cut off too early

Potential reasons:

- `VAD_AGGRESSIVENESS=3` is the strictest setting
- `VAD_SILENCE_DURATION=1.5` may be too short for your speaking style

Try:

- lowering aggressiveness to `2` or `1`
- increasing silence duration

### Long messages are truncated when spoken

Expected behavior:

- TTS input is truncated to 2000 characters in [`main.py:426`](/Users/ghively/Projects/openclaw-voice-bridge/main.py#L426)

### Commands are unexpectedly read aloud

Cause:

- `on_message()` schedules TTS before command handling
- there is no filter excluding command-prefixed content

### Temporary files accumulate

Check:

- whether playback errors prevent normal cleanup
- whether the process is crashing before `atexit` or shutdown cleanup runs
- the count shown by `!status`

## Performance Characteristics

### Latency expectations

The project does not publish formal benchmarks, but the latency contributors are clear from the code path:

1. VAD endpointing delay:
   typically up to `VAD_SILENCE_DURATION`, default 1.5 s
2. executor scheduling delay:
   depends on thread pool contention
3. 48 kHz -> 16 kHz resampling cost:
   usually modest relative to transcription
4. Faster-Whisper inference time:
   dominant STT cost, highly hardware- and model-dependent
5. channel send latency to Discord
6. Piper synthesis time
7. FFmpeg startup and playback start latency

Practical implication:

- STT latency is usually at least "utterance duration plus endpointing delay plus transcription time"
- TTS playback is serialized, so bursts of chat messages will queue behind the lock

### Resource usage

CPU:

- NumPy downmixing
- SciPy resampling
- WebRTC VAD
- Faster-Whisper on CPU if `WHISPER_DEVICE=cpu`
- Piper synthesis
- FFmpeg decoding/streaming

Memory:

- loaded Whisper model
- loaded Piper model
- per-user frame buffers
- per-utterance bytearrays
- temporary WAV tracking set

Disk:

- temporary WAV files under `/tmp/openclaw_voice_bridge`

### Scaling characteristics

The code is designed for small-scale single-process use:

- one active voice connection
- one monitor loop
- executor capped at 4 workers
- no distributed queue or horizontal scaling

More simultaneous speakers or heavy TTS traffic will increase:

- queue contention
- transcription backlog
- temp file churn
- event-loop scheduling pressure

## Limitations and Known Issues

### Queue wiring bug in `VoiceBridgeSink`

The most significant current issue is the mismatch between:

- `write()` using `self.audio_queue`
- `get_frame()` and `get_user_frame()` reading `_user_queues`

This means the documented per-user queue model is not fully realized in the checked-in code.

### TTS is not channel-scoped

While connected to voice, the bot processes messages from any channel it can read, not only the target bridge channel.

### Single voice connection model

Global variables assume one active voice connection for the entire process. Multi-guild or multi-channel bridging is not implemented.

### Hard-coded English transcription

`transcribe_audio()` forces `language="en"`. Multilingual or auto-detect workflows are not implemented.

### Hard-coded STT/TTS limits

The following are fixed in code rather than configured externally:

- 3-second TTS cooldown
- 30-second maximum utterance duration
- 2000-character TTS input cap
- 4-worker executor

### Incomplete cleanup on member leave

`on_voice_state_update()` removes a user from `processing_users`, but does not call `sink.evict_user()`. Per-user sink state can outlive the voice member's presence.

### `monitored_messages` is currently unused

Message IDs are tracked in a bounded deque, but that collection does not drive any behavior elsewhere in the code.

### Error handling edge case in `!join`

If connection setup fails before `sink` is assigned, the exception handler calls `sink.cleanup()` without checking for `None`.

### No automated test suite

The repository currently contains source files plus a README and requirements file, but no tests.

## Dependency Summary

From [`requirements.txt`](/Users/ghively/Projects/openclaw-voice-bridge/requirements.txt):

- `discord.py>=2.3.2`
- `discord-ext-voice-recv>=0.5.2`
- `faster-whisper>=1.0.0`
- `piper-tts>=1.4.0`
- `webrtcvad>=2.0.10`
- `python-dotenv>=1.0.0`
- `numpy>=1.24.0`
- `scipy>=1.11.0`

## Suggested Reading Order for Contributors

If you are new to the codebase, read in this order:

1. [`main.py`](/Users/ghively/Projects/openclaw-voice-bridge/main.py)
2. [`audio_sink.py`](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py)
3. [`README.md`](/Users/ghively/Projects/openclaw-voice-bridge/README.md)
4. [`CONTRIBUTING.md`](/Users/ghively/Projects/openclaw-voice-bridge/CONTRIBUTING.md)
