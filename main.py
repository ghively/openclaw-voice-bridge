#!/usr/bin/env python3
"""
OpenClaw Voice Bridge Bot v3

A Discord voice bridge bot that:
- Joins voice channels and transcribes speech to text using Faster-Whisper
- Reads text messages aloud using Piper TTS
- Uses discord-ext-voice-recv for real-time audio streaming

Built for discord.py (NOT Pycord)
"""

import asyncio
import os
import sys
import shutil
import signal
import atexit
import threading
import logging
import webrtcvad
import wave
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set, Deque, Any

import numpy as np
from dotenv import load_dotenv

import discord
from discord.ext import commands
from discord.ext import voice_recv

# Import our custom audio sink
from audio_sink import VoiceBridgeSink

# ============================================================================
# Configuration
# ============================================================================

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TARGET_TEXT_CHANNEL_ID = int(os.getenv("TARGET_TEXT_CHANNEL_ID", 0))
BOT_USER_ID = int(os.getenv("BOT_USER_ID", 0))

# STT Configuration
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# TTS Configuration
PIPER_MODEL_PATH = os.getenv("PIPER_MODEL_PATH")

# VAD Configuration
VAD_SILENCE_DURATION = float(os.getenv("VAD_SILENCE_DURATION", "1.5"))
VAD_AGGRESSIVENESS = int(os.getenv("VAD_AGGRESSIVENESS", "3"))

# Audio Configuration
SAMPLE_RATE = 48000
CHANNELS = 1  # Mono after conversion

# Rate Limiting
TTS_COOLDOWN = 3.0  # seconds
MAX_UTTERANCE_DURATION = 30.0  # seconds - force transcription after this
MAX_TTS_INPUT_LENGTH = 2000  # characters

# Temp file tracking
TEMP_DIR = Path("/tmp/openclaw_voice_bridge")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("VoiceBridge")

# ============================================================================
# Bot Setup
# ============================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ============================================================================
# Global State
# ============================================================================

# Voice connection
voice_client: Optional[voice_recv.VoiceRecvClient] = None
sink: Optional[VoiceBridgeSink] = None

# Thread pool for blocking operations
executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="voice_bridge")

# Task tracking
background_tasks: Set[asyncio.Task] = set()

# State flags
_leaving = False
_connected = False

# User processing tracking
processing_users: Set[int] = set()

# Message monitoring
monitored_messages: Deque[int] = deque(maxlen=1000)

# Per-user pending audio (for overlapping utterances)

# TTS state
_tts_lock = asyncio.Lock()
_last_tts_time: Dict[int, float] = {}

# Temp file tracking (thread-safe)
_temp_files: Set[Path] = set()
_temp_files_lock = threading.Lock()


# ============================================================================
# STT and TTS Initialization
# ============================================================================

# Load STT model
def load_stt_model():
    """Load Faster-Whisper model for speech-to-text."""
    from faster_whisper import WhisperModel
    logger.info(f"Loading Faster-Whisper model: {WHISPER_MODEL_SIZE} on {WHISPER_DEVICE} ({WHISPER_COMPUTE_TYPE})")
    model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE
    )
    logger.info("STT model loaded successfully")
    return model


# Load TTS model
def load_tts_model():
    """Load Piper TTS model for text-to-speech."""
    from piper import PiperVoice
    logger.info(f"Loading Piper TTS model from: {PIPER_MODEL_PATH}")
    model = PiperVoice.load(PIPER_MODEL_PATH)
    logger.info("TTS model loaded successfully")
    return model


# Models loaded lazily at startup
stt_model = None
tts_model = None


# ============================================================================
# Helper Functions
# ============================================================================

def register_temp_file(path: Path):
    """Track a temp file for cleanup."""
    with _temp_files_lock:
        _temp_files.add(path)


def cleanup_temp_file(path: Path):
    """Clean up a temp file safely."""
    try:
        if path.exists():
            path.unlink()
        with _temp_files_lock:
            _temp_files.discard(path)
    except Exception as e:
        logger.warning(f"Failed to cleanup temp file {path}: {e}")


def cleanup_all_temp_files():
    """Clean up all tracked temp files."""
    with _temp_files_lock:
        files = list(_temp_files)
    for path in files:
        cleanup_temp_file(path)


def create_temp_file(suffix: str = ".wav") -> Path:
    """Create a temp file and track it for cleanup."""
    fd, path = tempfile.mkstemp(suffix=suffix, dir=TEMP_DIR)
    os.close(fd)
    path_obj = Path(path)
    register_temp_file(path_obj)
    return path_obj


def track_task(task: asyncio.Task):
    """Track a background task for cleanup."""
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


def check_ffmpeg():
    """Check if ffmpeg is available."""
    if shutil.which("ffmpeg") is None:
        logger.error("ffmpeg not found! Please install ffmpeg for audio playback.")
        logger.error("  macOS: brew install ffmpeg")
        logger.error("  Linux: sudo apt install ffmpeg")
        sys.exit(1)
    logger.info("ffmpeg found")


# ============================================================================
# Audio Processing
# ============================================================================

async def audio_monitor_loop():
    """
    Monitor the audio queue for new speakers and start processing tasks.
    This is the bridge between the sink (thread) and the async event loop.
    """
    logger.info("Audio monitor loop started")
    loop = asyncio.get_event_loop()

    try:
        while _connected and sink is not None:
            # Check for new speakers without consuming their frames
            # Sleep briefly to avoid busy-waiting
            await asyncio.sleep(0.05)

            with sink._lock:
                for uid in list(sink._user_queues.keys()):
                    if uid not in processing_users and not sink._user_queues[uid].empty():
                        # New speaker detected — start processing task
                        display_name = str(uid)
                        if voice_client and voice_client.guild:
                            member = voice_client.guild.get_member(uid)
                            if member:
                                display_name = member.display_name

                        # Add to processing set BEFORE creating task (TOCTOU fix)
                        processing_users.add(uid)

                        task = asyncio.create_task(
                            process_user_audio(uid, display_name)
                        )
                        background_tasks.add(task)
                        task.add_done_callback(background_tasks.discard)
                        logger.info(f"Started processing audio for {display_name} ({uid})")

    except asyncio.CancelledError:
        logger.info("Audio monitor loop cancelled")
    except Exception as e:
        logger.error(f"Error in audio monitor loop: {e}", exc_info=True)


async def process_user_audio(user_id: int, user_display_name: str):
    """
    Process audio frames for a user through VAD, buffer, and STT.

    This runs in a background task per speaking user.
    """
    global sink

    if sink is None:
        return

    logger.info(f"Starting audio processing for {user_display_name} ({user_id})")

    # Audio buffer for this user's speech
    audio_buffer: bytearray = bytearray()

    # VAD setup
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    frame_duration_ms = 20
    silence_threshold_frames = int(VAD_SILENCE_DURATION * 1000 / frame_duration_ms)
    max_frames = int(MAX_UTTERANCE_DURATION * 1000 / frame_duration_ms)

    in_speech = False
    silence_frames = 0
    speech_frames = 0

    # Process until user stops speaking
    try:
        while user_id in processing_users:
            # Read frame from sink with timeout
            try:
                loop = asyncio.get_event_loop()
                frame_data = await loop.run_in_executor(
                    executor,
                    sink.get_user_frame,
                    user_id,
                    0.5  # 500ms timeout
                )

                if frame_data is None:
                    # Timeout while waiting for this user's frames
                    if in_speech:
                        silence_frames += 25  # 500ms / 20ms = 25 frames of silence
                        if silence_frames >= silence_threshold_frames:
                            logger.info(f"VAD timeout: speech ended for {user_display_name}")
                            break
                    continue

                # frame_data is bytes (the 20ms mono frame)
                # Check VAD
                if len(frame_data) == VoiceBridgeSink.FRAME_BYTES:
                    is_speech = vad.is_speech(frame_data, VoiceBridgeSink.SAMPLE_RATE)
                else:
                    is_speech = False

                if is_speech:
                    silence_frames = 0
                    if not in_speech:
                        logger.info(f"VAD: Speech started for {user_display_name}")
                        in_speech = True
                    audio_buffer.extend(frame_data)
                    speech_frames += 1

                    # Check max duration
                    if speech_frames >= max_frames:
                        logger.info(f"Max utterance reached for {user_display_name}, forcing transcription")
                        break
                elif in_speech:
                    # Add silence frames to buffer for smoother transcription
                    audio_buffer.extend(frame_data)
                    silence_frames += 1
                    if silence_frames >= silence_threshold_frames:
                        logger.info(f"VAD: Speech ended for {user_display_name}")
                        break

            except Exception as e:
                logger.error(f"Error reading frame for {user_display_name}: {e}")
                break

        # Transcribe the buffered audio
        if len(audio_buffer) > 0:
            # Check minimum duration (0.5s = 25 frames)
            min_bytes = int(0.5 * VoiceBridgeSink.SAMPLE_RATE * VoiceBridgeSink.BYTES_PER_SAMPLE)
            if len(audio_buffer) >= min_bytes:
                await transcribe_audio(user_id, user_display_name, bytes(audio_buffer))
            else:
                logger.debug(f"Audio too short for {user_display_name}, skipping")

    except Exception as e:
        logger.error(f"Error in process_user_audio for {user_display_name}: {e}", exc_info=True)
    finally:
        # Clean up
        processing_users.discard(user_id)
        logger.info(f"Finished audio processing for {user_display_name} ({user_id})")


async def transcribe_audio(user_id: int, user_display_name: str, audio_bytes: bytes):
    """Transcribe audio using Faster-Whisper."""
    global stt_model

    if stt_model is None:
        logger.warning("STT model not loaded, skipping transcription")
        return

    if not audio_bytes:
        return

    logger.info(f"Transcribing {len(audio_bytes)} bytes from {user_display_name}")

    try:
        # Convert bytes to numpy float32 array for Whisper
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Resample from 48kHz to 16kHz (Whisper expects 16kHz)
        from scipy import signal as scipy_signal
        target_rate = 16000
        gcd = np.gcd(SAMPLE_RATE, target_rate)
        up = target_rate // gcd
        down = SAMPLE_RATE // gcd
        audio_array = scipy_signal.resample_poly(audio_array, up, down)

        # Run STT in executor to avoid blocking
        loop = asyncio.get_event_loop()
        segments, info = await loop.run_in_executor(
            executor,
            lambda: stt_model.transcribe(
                audio_array,
                language="en",
                beam_size=5,
                vad_filter=False  # We already did VAD
            )
        )

        # Combine segments
        transcription = " ".join(seg.text for seg in segments).strip()

        if transcription:
            logger.info(f"Transcription from {user_display_name}: {transcription}")

            # Send to text channel using fetch_channel for reliability
            try:
                channel = await bot.fetch_channel(TARGET_TEXT_CHANNEL_ID)
                await channel.send(f"**{user_display_name}:** {transcription}")
            except discord.NotFound:
                logger.error(f"Target channel {TARGET_TEXT_CHANNEL_ID} not found")
            except Exception as e:
                logger.error(f"Error sending transcription: {e}", exc_info=True)
        else:
            logger.info(f"No transcription from {user_display_name} (empty result)")

    except Exception as e:
        logger.error(f"Error transcribing audio from {user_display_name}: {e}", exc_info=True)

async def speak_text(text: str, voice_client: discord.VoiceClient) -> Optional[Path]:
    """Generate TTS audio and return the audio file path."""
    global tts_model

    # Validate input
    if not text or not text.strip():
        return None

    text = text.strip()[:MAX_TTS_INPUT_LENGTH]

    logger.info(f"Generating TTS for: {text[:50]}...")

    try:
        # Generate audio in executor
        loop = asyncio.get_event_loop()

        # Create temp file
        temp_file = create_temp_file(suffix=".wav")

        await loop.run_in_executor(
            executor,
            lambda: generate_tts_audio(text, temp_file)
        )

        logger.info(f"TTS audio generated: {temp_file}")
        return temp_file

    except Exception as e:
        logger.error(f"Error generating TTS: {e}", exc_info=True)
        return None


def generate_tts_audio(text: str, output_path: Path):
    """Generate TTS audio using Piper 1.4+ (blocking)."""
    global tts_model

    # Piper 1.4+ synthesize() returns Iterable[AudioChunk]
    audio_chunks = list(tts_model.synthesize(text))

    if not audio_chunks:
        raise ValueError("Piper returned no audio chunks")

    # Use sample rate from first chunk (all chunks should be the same)
    sample_rate = audio_chunks[0].sample_rate

    # Concatenate all audio data
    audio_bytes = b''.join(
        chunk.audio_int16_array.tobytes() for chunk in audio_chunks
    )

    # Save to WAV
    with wave.open(str(output_path), 'wb') as wf:
        wf.setnchannels(1)  # Mono
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio_bytes)


async def play_audio(file_path: Path, voice_client: discord.VoiceClient):
    """Play audio file through voice client."""
    try:
        # Use ffmpeg to play audio
        voice_client.play(
            discord.FFmpegPCMAudio(str(file_path)),
            after=lambda e: cleanup_temp_file(file_path)
        )

        # Wait for playback to finish
        while voice_client.is_playing():
            await asyncio.sleep(0.1)

    except Exception as e:
        logger.error(f"Error playing audio: {e}", exc_info=True)
        cleanup_temp_file(file_path)


async def process_tts(message: discord.Message):
    """Process a message for TTS playback."""
    global voice_client

    if not voice_client or not voice_client.is_connected():
        return

    user_id = message.author.id

    # Check rate limit
    now = datetime.now().timestamp()
    last_time = _last_tts_time.get(user_id, 0)
    if now - last_time < TTS_COOLDOWN:
        logger.info(f"TTS rate limited for {message.author.display_name}")
        return

    # Skip bots (except the configured bot user)
    if message.author.bot and message.author.id != BOT_USER_ID:
        return

    # Update last TTS time
    _last_tts_time[user_id] = now

    # Generate TTS
    async with _tts_lock:
        audio_file = await speak_text(message.content, voice_client)

        if audio_file:
            await play_audio(audio_file, voice_client)


# ============================================================================
# Discord Event Handlers
# ============================================================================

@bot.event
async def on_ready():
    """Called when bot is ready."""
    global stt_model, tts_model

    logger.info(f"Bot logged in as {bot.user} (ID: {bot.user.id})")

    # Check ffmpeg
    check_ffmpeg()

    # Load models
    try:
        stt_model = await asyncio.get_event_loop().run_in_executor(executor, load_stt_model)
        tts_model = await asyncio.get_event_loop().run_in_executor(executor, load_tts_model)
        logger.info("Models loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load models: {e}", exc_info=True)
        sys.exit(1)


@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages."""
    # Skip own messages only
    if message.author.id == bot.user.id:
        return

    # Track message
    monitored_messages.append(message.id)

    # Process for TTS if we're in a voice channel
    if voice_client and voice_client.is_connected():
        task = asyncio.create_task(process_tts(message))
        track_task(task)

    # Process commands
    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Handle voice state changes."""
    global voice_client, sink, _connected, _leaving

    # Skip if we're leaving
    if _leaving:
        return

    # User joined our voice channel
    if after.channel and voice_client and voice_client.channel == after.channel:
        if not before.channel or before.channel != after.channel:
            logger.info(f"{member.display_name} joined voice channel")

    # User left our voice channel
    elif before.channel and voice_client and voice_client.channel == before.channel:
        if not after.channel or after.channel != before.channel:
            logger.info(f"{member.display_name} left voice channel")

            # Remove from processing
            processing_users.discard(member.id)

            # Clean up pending audio

    # Bot was moved to another channel
    if member.id == bot.user.id and after.channel and after.channel != before.channel:
        logger.info(f"Bot moved to {after.channel.name}")


# ============================================================================
# Bot Commands
# ============================================================================

@bot.command(name="join")
async def join_voice(ctx: commands.Context):
    """Join the voice channel the user is in."""
    global voice_client, sink, _connected, _leaving

    # Check if user is in a voice channel
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You must be in a voice channel to use this command.")
        return

    # Check if already connected
    if voice_client and voice_client.is_connected():
        await ctx.send("I'm already in a voice channel.")
        return

    # Reset leaving flag
    _leaving = False

    target_channel = ctx.author.voice.channel

    try:
        logger.info(f"Joining voice channel: {target_channel.name}")

        # Connect with voice_recv client
        voice_client = await target_channel.connect(
            cls=voice_recv.VoiceRecvClient,
            self_mute=False,
            self_deaf=False
        )

        # Create sink
        sink = VoiceBridgeSink()

        # Start listening
        voice_client.listen(sink)

        _connected = True

        # Start the audio monitoring loop
        task = asyncio.create_task(audio_monitor_loop())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

        logger.info("Successfully joined and started listening")
        await ctx.send(f"Joined voice channel: **{target_channel.name}**")

    except Exception as e:
        logger.error(f"Error joining voice channel: {e}", exc_info=True)
        await ctx.send(f"Error joining voice channel: {e}")
        voice_client = None
        sink.cleanup()
        sink = None
        _connected = False


@bot.command(name="leave")
async def leave_voice(ctx: commands.Context):
    """Leave the voice channel."""
    global voice_client, sink, _connected, _leaving

    if not voice_client or not voice_client.is_connected():
        await ctx.send("I'm not in a voice channel.")
        return

    # Set leaving flag to prevent reconnect
    _leaving = True

    try:
        logger.info("Leaving voice channel")

        # Stop listening
        if voice_client.is_listening():
            voice_client.stop_listening()

        # Cancel all processing tasks
        for user_id in list(processing_users):
            processing_users.discard(user_id)

        # Disconnect
        await voice_client.disconnect()

        # Cleanup
        sink.cleanup()
        sink = None
        voice_client = None
        _connected = False

        logger.info("Successfully left voice channel")
        await ctx.send("Left voice channel.")

    except Exception as e:
        logger.error(f"Error leaving voice channel: {e}", exc_info=True)
        await ctx.send(f"Error leaving voice channel: {e}")


@bot.command(name="status")
async def status_cmd(ctx: commands.Context):
    """Show bot status."""
    status_lines = [
        "**Voice Bridge Bot Status**",
        f"Connected: {'Yes' if voice_client and voice_client.is_connected() else 'No'}",
        f"Listening: {'Yes' if voice_client and voice_client.is_listening() else 'No'}",
        f"Processing Users: {len(processing_users)}",
        f"Background Tasks: {len(background_tasks)}",
        f"Temp Files: {len(_temp_files)}",
    ]

    if voice_client and voice_client.is_connected():
        status_lines.append(f"Channel: **{voice_client.channel.name}**")

    await ctx.send("\n".join(status_lines))


# ============================================================================
# Shutdown Handler
# ============================================================================

async def shutdown():
    """Clean shutdown."""
    global voice_client, sink, _connected

    logger.info("Shutting down...")

    # Cancel all background tasks
    for task in list(background_tasks):
        task.cancel()

    # Wait for tasks to cancel
    if background_tasks:
        await asyncio.sleep(0.5)

    # Stop listening
    if voice_client and voice_client.is_listening():
        voice_client.stop_listening()

    # Disconnect
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()

    # Cleanup temp files
    cleanup_all_temp_files()

    # Shutdown executor
    executor.shutdown(wait=False)

    # Close the bot
    await bot.close()

    logger.info("Shutdown complete")


def signal_handler():
    """Handle shutdown signals."""
    logger.info("Received shutdown signal")

    # Schedule shutdown
    loop = asyncio.get_event_loop()
    loop.create_task(shutdown())


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point."""
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set in environment!")
        sys.exit(1)

    if not TARGET_TEXT_CHANNEL_ID:
        logger.error("TARGET_TEXT_CHANNEL_ID not set in environment!")
        sys.exit(1)

    if not PIPER_MODEL_PATH or not Path(PIPER_MODEL_PATH).exists():
        logger.error(f"PIPER_MODEL_PATH not set or file does not exist: {PIPER_MODEL_PATH}")
        sys.exit(1)

    # Register atexit handler
    atexit.register(cleanup_all_temp_files)

    # Register signal handlers
    signal.signal(signal.SIGINT, lambda s, f: signal_handler())
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler())

    # Run bot
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
