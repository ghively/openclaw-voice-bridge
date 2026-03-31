# Voice Bridge v3 — Final Build Plan

## Architecture Decision
**Use discord.py + discord-ext-voice-recv** (NOT Pycord)

Pycord's recording is designed for "record then save to file" — not real-time streaming.
`discord-ext-voice-recv` provides per-packet audio receiving, exactly what we need.

Reference bot that does this successfully: https://github.com/nthnulmr/DiscordLiveTranscriptionBot

## Agent Responsibilities

### Claude Code — Core Bot & Concurrency
- main.py: bot setup, commands, message handling, TTS pipeline
- asyncio.Lock for TTS serialization
- Background task tracking and cleanup
- _leaving flag for leave/reconnect race
- Error handling throughout
- !join, !leave, !status commands

### Codex — Discord API & Edge Cases
- Voice connection lifecycle (connect, disconnect, reconnect, move)
- on_voice_state_update handling
- VoiceClient vs VoiceProtocol correctness
- Per-user audio isolation
- Edge cases: channel deletion, user leaving mid-speech, bot kicked
- fetch_channel vs get_channel

### Gemini — Audio Processing Pipeline
- audio_sink.py: real-time audio receive using discord-ext-voice-recv
- Stereo-to-mono conversion
- Frame buffering to 20ms chunks for webrtcvad
- VoiceActivityDetector with proper 20ms frames
- AudioBuffer with bytearray
- Thread-safe audio queue (queue.Queue -> asyncio bridge)

### Gregory — Orchestration & Integration
- Write the plan, coordinate agents
- Integrate all pieces
- Install dependencies
- Configure .env
- Final testing

## Shared Requirements
- STT: Faster-Whisper (base, int8, CPU) via run_in_executor
- TTS: Piper TTS via run_in_executor
- VAD: webrtcvad at 20ms frames
- on_message: skip only self, allow BOT_USER_ID
- processing_users: add BEFORE create_task (not inside)
- monitored_messages: deque(maxlen=1000)
- pending_audio: per-user deque for overlapping utterances (while loop, not recursive)
- Temp files: thread-safe tracking, cleanup in exception paths
- TTS rate limiting: 3s cooldown
- Max utterance: 30s force transcription
- TTS input: max 2000 chars
- Requirements: discord.py>=2.3.2, discord-ext-voice-recv, faster-whisper, piper-tts==1.2.0, webrtcvad, python-dotenv, numpy, scipy

## Build Order
1. Claude Code builds main.py (core bot)
2. Gemini builds audio_sink.py (audio pipeline)
3. Gregory integrates and tests
4. All 4 agents review (Round 1)
5. Fix any issues
6. All 4 agents review (Round 2)
7. Done
