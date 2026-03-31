# 🎙️ OpenClaw Voice Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3.2+-7289da.svg)](https://github.com/Rapptz/discord.py)
[![Faster-Whisper](https://img.shields.io/badge/STT-Faster--Whisper-green.svg)](https://github.com/SYSTRAN/faster-whisper)
[![Piper TTS](https://img.shields.io/badge/TTS-Piper-orange.svg)](https://github.com/rhasspy/piper)

A high-performance, real-time Discord voice bridge that seamlessly connects spoken word to text and back again. Built with a focus on accessibility and low-latency interaction, OpenClaw Voice Bridge transforms your Discord voice channels into inclusive, multi-modal communication hubs.

---

## 🏗️ Architecture

```text
       ┌──────────────┐          ┌───────────────────┐          ┌──────────────┐
       │   Discord    │ ◄─────── │  Piper TTS Engine │ ◄─────── │  Text Msg    │
       │ Voice Stream │          └───────────────────┘          │  Monitoring  │
       └──────┬───────┘                                         └──────────────┘
              │ PCM Audio (48kHz)
              ▼
       ┌──────────────┐          ┌───────────────────┐          ┌──────────────┐
       │  VoiceBridge │ ───────► │  WebRTC VAD       │ ───────► │ Faster       │
       │  Audio Sink  │          │  (Speech Detect)  │          │ Whisper STT  │
       └──────────────┘          └───────────────────┘          └──────┬───────┘
                                                                       │
                                                                       ▼
                                                                ┌──────────────┐
                                                                │ Text Channel │
                                                                │ Transcription│
                                                                └──────────────┘
```

---

## ✨ Features

| Feature | Description |
| :--- | :--- |
| **STT Transcription** | Real-time speech-to-text using Faster-Whisper (Tiny to Large-v3). |
| **TTS Playback** | High-quality, low-latency neural TTS using Piper. |
| **User Isolation** | Processes each speaker's audio in independent streams for clarity. |
| **VAD Filtering** | Intelligent Voice Activity Detection to minimize background noise processing. |
| **Rate Limiting** | Built-in TTS cooldowns and input validation to prevent spam. |
| **Cross-Platform** | Optimized for macOS (Apple Silicon), Linux, and Windows. |

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.9+**
- **FFmpeg** (`brew install ffmpeg` on macOS)
- **Piper TTS Model**: Download an `.onnx` and `.json` model from the [Piper VOICES](https://github.com/rhasspy/piper#voices) collection.

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/ghively/openclaw-voice-bridge.git
   cd openclaw-voice-bridge
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your Discord Token and Channel IDs
   ```

4. **Launch the bridge**
   ```bash
   python main.py
   ```

---

## ⚙️ Configuration Reference

Edit your `.env` file to tune the bridge performance:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DISCORD_BOT_TOKEN` | — | Your Discord application bot token. |
| `TARGET_TEXT_CHANNEL_ID` | — | Where transcriptions will be posted. |
| `BOT_USER_ID` | — | The bot user ID whose messages trigger TTS playback. |
| `WHISPER_MODEL_SIZE` | `base` | `tiny`, `base`, `small`, `medium`, `large-v3`. |
| `WHISPER_DEVICE` | `cpu` | `cpu` for Mac/Standard, `cuda` for NVIDIA GPUs. |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8` for CPU, `float16` for GPU. |
| `PIPER_MODEL_PATH` | — | Full path to your `.onnx` Piper model. |
| `VAD_SILENCE_DURATION` | `1.5` | Seconds of silence before ending an utterance. |
| `VAD_AGGRESSIVENESS` | `3` | 0 (least) to 3 (most) aggressive noise filtering. |

---

## ⌨️ Bot Commands

- `!join` — Summon the bot to your current voice channel.
- `!leave` — Dismiss the bot and clean up resources.
- `!status` — Display real-time processing stats and connection health.

---

## 🤝 Contributing

Contributions are welcome! Whether it's optimizing the audio pipeline, adding new STT engines, or improving the documentation:

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.

---

<p align="center">
  Built with ❤️ for the Discord Community
</p>
