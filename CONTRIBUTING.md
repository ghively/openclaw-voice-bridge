# Contributing

## Development Setup

### Prerequisites

- Python 3.10 or newer
- FFmpeg available on `PATH`
- a Discord bot application with the required intents and permissions
- a Piper voice model file
- enough local CPU or GPU resources to run Faster-Whisper

### Clone and install

```bash
git clone <repo-url>
cd openclaw-voice-bridge
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure environment

Create a local `.env` with the variables the current code actually reads:

```env
DISCORD_BOT_TOKEN=
TARGET_TEXT_CHANNEL_ID=
BOT_USER_ID=0
WHISPER_MODEL_SIZE=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
PIPER_MODEL_PATH=/absolute/path/to/voice.onnx
VAD_SILENCE_DURATION=1.5
VAD_AGGRESSIVENESS=3
```

### Run the bot

```bash
python main.py
```

## Code Style

The repository is small and does not currently enforce formatting or linting through tooling, so contributors should keep style consistent with the existing code.

Guidelines:

- follow existing Python style in [`main.py`](/Users/ghively/Projects/openclaw-voice-bridge/main.py) and [`audio_sink.py`](/Users/ghively/Projects/openclaw-voice-bridge/audio_sink.py)
- prefer clear, explicit names over compact abstractions
- keep Discord event flow easy to trace
- document behavior changes in comments only where the code is otherwise non-obvious
- preserve the current logging style with actionable messages
- avoid introducing hidden background state without documenting it

When making behavioral changes, update:

- [`DOCS.md`](/Users/ghively/Projects/openclaw-voice-bridge/DOCS.md)
- [`README.md`](/Users/ghively/Projects/openclaw-voice-bridge/README.md)
- [`CHANGELOG.md`](/Users/ghively/Projects/openclaw-voice-bridge/CHANGELOG.md) when appropriate

## Testing

There is no automated test suite in the current repository. Contributions should therefore include careful manual validation.

Recommended manual test plan:

1. Start the bot and confirm `on_ready()` loads both models successfully.
2. Run `!join` from a user already present in a voice channel.
3. Speak a short utterance and verify transcription reaches `TARGET_TEXT_CHANNEL_ID`.
4. Send a normal text message and verify TTS playback occurs in voice.
5. Confirm per-user cooldown behavior by sending rapid consecutive messages.
6. Run `!status` and verify runtime counters look reasonable.
7. Run `!leave` and confirm the bot disconnects cleanly.
8. Stop the process and confirm temp files are cleaned up.

When changing the sink or concurrency model, explicitly test:

- multiple speaking users
- users joining and leaving mid-utterance
- long utterances near the 30-second cap
- startup behavior with missing dependencies or invalid paths

If you add tests, prefer small focused tests around:

- sink frame slicing
- stereo-to-mono conversion
- VAD endpointing thresholds
- config parsing
- temp-file lifecycle

## Pull Request Process

### Before opening a PR

- keep the branch focused on one change set
- re-read the affected execution path end to end
- run the manual validation relevant to your change
- update documentation if behavior, configuration, or limitations changed

### PR expectations

Include:

- a concise problem statement
- the implementation approach
- manual test evidence
- any remaining limitations or follow-up work

If the change affects runtime behavior, mention:

- whether `.env` requirements changed
- whether Discord permissions/intents changed
- whether the audio pipeline changed
- whether new failure modes were introduced

### Review standard

Changes should be easy to reason about in terms of:

- voice receive lifecycle
- per-user isolation
- event-loop versus thread-pool boundaries
- cleanup on disconnect and shutdown
- user-visible behavior in Discord

Bug fixes that affect bridge correctness should usually include a documentation update in the same PR.
