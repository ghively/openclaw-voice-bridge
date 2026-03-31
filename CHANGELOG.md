# Changelog

All notable changes to this project are documented here.

## v3.0.0

Complete rewrite around `discord.py` plus `discord-ext-voice-recv`.

Highlights:

- replaced earlier voice handling with `voice_recv.VoiceRecvClient`
- introduced a custom `VoiceBridgeSink` for receive-side PCM processing
- moved to a real-time receive pipeline built around 20 ms frames
- added WebRTC VAD-based utterance segmentation
- added the intended per-user queue isolation model for overlapping speakers
- transcribed buffered speech locally with Faster-Whisper
- posted transcriptions into a configured Discord text channel
- synthesized text replies with Piper TTS
- played generated speech back into Discord voice using FFmpeg
- added temp-file tracking and shutdown cleanup
- added `!status` command for runtime visibility

Notes:

- this version is the first to target `discord.py` explicitly rather than Pycord
- the checked-in sink implementation currently has a queueing mismatch that should be treated as a follow-up fix

## v2.0.0

Pycord rewrite.

Summary:

- attempted to rebuild the project on Pycord
- proved to be the wrong library choice for the receive-side voice requirements of this bot
- ultimately abandoned in favor of the `discord.py` plus `discord-ext-voice-recv` approach used in v3

## v1.0.0

Initial prototype built on `discord.py`.

Summary:

- established the original Discord bot structure
- validated the basic "voice to text to voice" bridge concept
- served as the foundation for later rewrites
