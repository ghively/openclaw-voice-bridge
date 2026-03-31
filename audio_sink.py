"""
Voice Bridge Audio Sink

Processes Discord audio packets through a real-time pipeline:
- Receives decoded PCM from discord-ext-voice-recv
- Converts stereo to mono
- Buffers into 20ms frames (960 samples at 48kHz)
- Queues frames for VAD and Whisper processing
"""

import queue
import threading
import time
from typing import Dict, Optional

import numpy as np
from discord import Member, User
from discord.ext import voice_recv


class VoiceBridgeSink(voice_recv.AudioSink):
    """
    Audio sink for Discord voice bridge using discord-ext-voice-recv.

    Receives decoded PCM audio from Discord voice, converts stereo to mono,
    buffers into 20ms frames, and queues them for processing.
    """

    # Audio constants
    SAMPLE_RATE = 48000  # Discord voice is 48kHz
    FRAME_DURATION_MS = 20  # VAD requires 20ms frames
    FRAME_SIZE = SAMPLE_RATE * FRAME_DURATION_MS // 1000  # 960 samples
    BYTES_PER_SAMPLE = 2  # 16-bit signed int
    FRAME_BYTES = FRAME_SIZE * BYTES_PER_SAMPLE  # 1920 bytes

    def __init__(self) -> None:
        """Initialize the audio sink."""
        super().__init__()
        # Per-user frame queues for isolated audio streams
        # user_id -> queue of frame bytes
        self._user_queues: Dict[int, queue.Queue] = {}

        # Per-user buffers for partial frames
        # user_id -> bytearray of accumulated mono PCM
        self._buffers: Dict[int, bytearray] = {}

        # Lock for buffer/queue operations
        self._lock = threading.Lock()

        # Lock for buffer operations
        self._buffer_lock = threading.Lock()

    def wants_opus(self) -> bool:
        """
        Return False to receive decoded PCM instead of Opus packets.

        discord-ext-voice-recv will decode Opus to PCM for us.
        """
        return False

    def write(self, user: Optional[User | Member], data: voice_recv.VoiceData) -> None:
        """
        Process incoming audio data from Discord.

        Args:
            user: The Discord user or member who sent the audio, or None if unknown.
            data: VoiceData containing .pcm attribute with decoded audio.
        """
        if user is None:
            # Unknown user, skip this packet
            return

        user_id = user.id

        # VoiceData.pcm is decoded PCM: 48kHz, stereo, 16-bit signed int, little-endian
        # Convert bytes to numpy array for efficient processing
        pcm_data = np.frombuffer(data.pcm, dtype=np.int16)

        # Convert stereo to mono by averaging left and right channels
        # Stereo layout: [L, R, L, R, L, R, ...]
        if len(pcm_data) % 2 == 1:
            # Odd length - drop the last sample (shouldn't happen with Discord)
            pcm_data = pcm_data[:-1]

        # Reshape to (N, 2) and average across channels
        stereo_samples = pcm_data.reshape(-1, 2)
        mono_samples = stereo_samples.mean(axis=1).astype(np.int16)

        # Convert back to bytes
        mono_bytes = mono_samples.tobytes()

        # Buffer and extract complete 20ms frames
        with self._buffer_lock:
            if user_id not in self._buffers:
                self._buffers[user_id] = bytearray()

            buffer = self._buffers[user_id]
            buffer.extend(mono_bytes)

            # Extract complete frames
            while len(buffer) >= self.FRAME_BYTES:
                frame = bytes(buffer[:self.FRAME_BYTES])
                del buffer[:self.FRAME_BYTES]

                # Queue the frame for this specific user
                if user_id not in self._user_queues:
                    self._user_queues[user_id] = queue.Queue()
                self._user_queues[user_id].put(frame)

    def evict_user(self, user_id: int) -> None:
        """
        Remove a user's buffer and queue when they leave.

        Args:
            user_id: Discord user ID to evict.
        """
        with self._lock:
            self._buffers.pop(user_id, None)
            self._user_queues.pop(user_id, None)

    def get_frame(self, timeout: float = 0.1) -> Optional[tuple[int, bytes]]:
        """
        Get the next audio frame from any user queue.
        Used by the audio monitor loop to detect new speakers.

        Args:
            timeout: How long to wait for a frame (seconds).

        Returns:
            Tuple of (user_id, frame_bytes) or None if timeout.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            with self._lock:
                for uid, q in self._user_queues.items():
                    if not q.empty():
                        try:
                            frame = q.get_nowait()
                            return (uid, frame)
                        except queue.Empty:
                            continue
            time.sleep(0.01)
        return None

    def get_user_frame(self, user_id: int, timeout: float = 0.1) -> Optional[bytes]:
        """
        Get the next frame for a specific user from their dedicated queue.

        Args:
            user_id: The user to get frames for.
            timeout: How long to wait for a frame (seconds).

        Returns:
            Frame bytes or None if timeout.
        """
        with self._lock:
            q = self._user_queues.get(user_id)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def cleanup(self) -> None:
        """
        Clean up all buffers and queues.

        Called when the bot leaves a voice channel.
        """
        with self._lock:
            self._buffers.clear()
            self._user_queues.clear()
