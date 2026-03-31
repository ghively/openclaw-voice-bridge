# OpenClaw Discord Voice Bridge Bot

A Discord bot that bridges voice channels to text, enabling voice interaction with OpenClaw (or any other Discord bot). The bot transcribes speech using Faster-Whisper and converts text responses to speech using Piper TTS.

## Features

- **Voice Channel Commands**: Join/leave voice channels with `!join` and `!leave`
- **Speech-to-Text**: Local transcription using Faster-Whisper (fast, privacy-friendly)
- **Text-to-Speech**: High-quality speech synthesis using Piper TTS
- **Voice Activity Detection**: Automatically detects when you stop speaking
- **Bidirectional Bridge**: Sends transcriptions to a text channel and speaks responses
- **Privacy-Friendly**: All processing happens locally on your machine

## Architecture

```
User (Voice) -> Discord -> Bot (VAD) -> Faster-Whisper -> Text Channel -> OpenClaw
                                                                     |
                                                                     v
User (Voice) <- Discord <- Bot (Piper TTS) <- Text Channel <---------
```

## Requirements

- Python 3.10 or higher
- FFmpeg (for audio encoding/decoding)
- CUDA-capable GPU (recommended for Whisper) or CPU
- Discord Bot Token

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd openclaw-voice-bridge
```

### 2. Install System Dependencies

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg python3-dev
```

**Windows:**
- Download FFmpeg from https://ffmpeg.org/download.html
- Add FFmpeg to your PATH

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 4. Download Whisper Model

The first time you run the bot, Faster-Whisper will automatically download the model. Models available:
- `tiny` (39M, fastest, least accurate)
- `base` (74M, fast, good accuracy) - **Recommended**
- `small` (244M, slower, more accurate)
- `medium` (769M, slow, very accurate)
- `large-v2` / `large-v3` (1.5B, slowest, most accurate)

Set the model size in `.env` (see Configuration below).

### 5. Download Piper TTS Model

Download a Piper TTS model from the [Piper Models repository](https://github.com/rhasspy/piper/releases/tag/v1.0.0).

Example for English male voice:
```bash
# Create models directory
mkdir -p models

# Download model
curl -L -o models/en_US-lessac-medium.onnx https://github.com/rhasspy/piper/releases/download/v1.0.0/en_US-lessac-medium.onnx
curl -L -o models/en_US-lessac-medium.onnx.json https://github.com/rhasspy/piper/releases/download/v1.0.0/en_US-lessac-medium.onnx.json
```

Update `.env` with the model paths.

### 6. Create Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to the "Bot" section and create a bot
4. Enable "MESSAGE CONTENT INTENT" and "SERVER MEMBERS INTENT"
5. Enable "VOICE" and "CONNECT" privileged intents
6. Copy the bot token
7. Go to "OAuth2" > "URL Generator"
8. Select scopes: `bot`, `applications.commands`
9. Select bot permissions: `Connect`, `Speak`, `Use Voice Activity`, `Send Messages`, `Read Messages`, `Read Message History`
10. Use the generated URL to invite the bot to your server

### 7. Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Discord Bot Configuration
DISCORD_BOT_TOKEN=your_actual_bot_token_here
TARGET_TEXT_CHANNEL_ID=123456789012345678  # Channel where OpenClaw listens
BOT_USER_ID=987654321098765432  # OpenClaw's bot user ID

# STT Configuration (Faster-Whisper)
WHISPER_MODEL_SIZE=base
WHISPER_DEVICE=cuda  # Use 'cpu' if you don't have CUDA
WHISPER_COMPUTE_TYPE=float16  # Use 'int8' for CPU

# TTS Configuration (Piper)
PIPER_MODEL_PATH=/absolute/path/to/models/en_US-lessac-medium.onnx
PIPER_CONFIG_PATH=/absolute/path/to/models/en_US-lessac-medium.onnx.json

# Voice Activity Detection
VAD_SILENCE_DURATION=1.5  # Seconds of silence before transcription
VAD_AGGRESSIVENESS=3  # 0-3 (3 = most aggressive speech detection)

# Audio Configuration
SAMPLE_RATE=48000  # Discord standard
CHANNELS=1  # Mono
```

### 8. Get Channel and User IDs

**To get a Channel ID:**
- Enable Developer Mode in Discord (User Settings > Advanced)
- Right-click the channel and select "Copy ID"

**To get the Bot User ID:**
- In Discord, type `@OpenClaw` (or your bot's name)
- Right-click the mention and select "Copy ID"

## Usage

### Starting the Bot

```bash
python main.py
```

The bot will log in and become ready.

### Commands

In Discord text channels:

- `!join` - Join the voice channel you're currently in
- `!leave` - Leave the current voice channel

### Workflow

1. Join a voice channel in Discord
2. Type `!join` in a text channel
3. The bot will join your voice channel
4. Speak in the voice channel
5. The bot transcribes your speech and sends it to the target text channel
6. OpenClaw (or any bot) replies in the text channel
7. The bot detects the reply and speaks it in the voice channel
8. Type `!leave` to disconnect

## Troubleshooting

### Bot won't connect to voice
- Ensure the bot has "CONNECT" and "SPEAK" permissions
- Check that you're in a voice channel before using `!join`

### No transcription happening
- Check that Faster-Whisper model downloaded successfully
- Ensure VAD_AGGRESSIVENESS is appropriate (try 2 or 3)
- Check that SAMPLE_RATE matches Discord (48000)

### TTS not working
- Verify PIPER_MODEL_PATH and PIPER_CONFIG_PATH are correct
- Ensure FFmpeg is installed and in PATH
- Check that the model files exist

### Audio quality issues
- Adjust VAD_SILENCE_DURATION (longer = fewer interruptions)
- Try a different Whisper model size
- Ensure good microphone quality

### High CPU usage
- Use a smaller Whisper model (`tiny` or `base`)
- Set WHISPER_DEVICE to `cpu` if GPU is not available
- Adjust VAD parameters to reduce processing frequency

## Performance Tips

1. **Use GPU**: If available, use CUDA for much faster transcription
2. **Model Selection**: `base` model offers good speed/accuracy tradeoff
3. **Silence Duration**: 1.5s is good balance; increase for longer utterances
4. **VAD Aggressiveness**: 3 works best for most environments

## Logging

The bot logs all important events:
- Bot startup/connection
- Voice channel joins/leaves
- Speech detection and transcription
- TTS generation and playback
- Errors and warnings

Logs are printed to stdout with timestamps.

## License

MIT License - feel free to use and modify for your needs.

## Contributing

Contributions welcome! Please feel free to submit pull requests.

## Support

For issues or questions:
- Check the troubleshooting section
- Review logs for error messages
- Ensure all dependencies are correctly installed
