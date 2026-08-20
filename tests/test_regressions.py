"""
Regression tests for the two bugs commit 94febf6 ("fix: critical bugs
found in audit") claimed to fix but did not actually touch:

  1. main.py:648 - `sink.cleanup()` was called unconditionally in
     `join_voice()`'s exception handler, crashing with AttributeError
     when `sink` was still None (e.g. `target_channel.connect()` fails
     before the sink is created).
  2. main.py:64 - the `CHANNELS` constant was defined but never
     referenced anywhere, and was never actually removed.

Run with:
    python -m pytest tests/
or
    python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Make the repo root importable regardless of the working directory tests
# are invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


class TestChannelsConstantRemoved(unittest.TestCase):
    """main.py:64 - dead CHANNELS constant should no longer exist."""

    def test_channels_constant_is_gone(self):
        self.assertFalse(
            hasattr(main, "CHANNELS"),
            "CHANNELS is dead code and should have been removed (main.py:64)",
        )


class TestJoinVoiceSinkCleanupNoneCheck(unittest.IsolatedAsyncioTestCase):
    """main.py:648 - sink.cleanup() must not be called when sink is None."""

    async def asyncSetUp(self):
        # join_voice() mutates module globals; reset them so tests don't
        # leak state into each other.
        main.voice_client = None
        main.sink = None
        main._connected = False
        main._leaving = False

    async def test_connect_failure_before_sink_exists_does_not_crash(self):
        """
        Simulates target_channel.connect() failing before `sink` is ever
        assigned (e.g. permission error, already connected elsewhere,
        network hiccup on first join). Before the fix, the except block's
        unconditional `sink.cleanup()` raised
        `AttributeError: 'NoneType' object has no attribute 'cleanup'`
        here, on a genuinely reachable failure path.
        """
        ctx = MagicMock()
        ctx.author.voice.channel.name = "test-channel"
        ctx.author.voice.channel.connect = AsyncMock(
            side_effect=RuntimeError("simulated connect failure")
        )
        ctx.send = AsyncMock()

        # Must not raise.
        await main.join_voice.callback(ctx)

        self.assertIsNone(main.sink)
        self.assertIsNone(main.voice_client)
        self.assertFalse(main._connected)
        ctx.send.assert_awaited()

    async def test_stale_sink_is_still_cleaned_up_on_connect_failure(self):
        """
        Guards against a regression in the other direction: if a sink from
        a prior session is already present when connect() fails again,
        cleanup() must still be called on it.
        """
        stale_sink = MagicMock()
        main.sink = stale_sink

        ctx = MagicMock()
        ctx.author.voice.channel.name = "test-channel"
        ctx.author.voice.channel.connect = AsyncMock(
            side_effect=RuntimeError("simulated connect failure")
        )
        ctx.send = AsyncMock()

        await main.join_voice.callback(ctx)

        stale_sink.cleanup.assert_called_once()
        self.assertIsNone(main.sink)


if __name__ == "__main__":
    unittest.main()
