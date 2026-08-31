import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.afk import AFK, afk_duration


class DurationTests(unittest.TestCase):
    def test_duration_units_and_boundaries(self):
        now = datetime(2026, 8, 31, tzinfo=timezone.utc)
        cases = (
            (0, "0s"),
            (1, "1s"),
            (59, "59s"),
            (60, "1m"),
            (61, "1m 1s"),
            (3599, "59m 59s"),
            (3600, "1h"),
            (86399, "23h 59m 59s"),
            (86400, "1d"),
            (93784, "1d 2h 3m 4s"),
        )
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                started = (now - timedelta(seconds=seconds)).isoformat()
                self.assertEqual(afk_duration(started, now), expected)

    def test_invalid_and_future_timestamps(self):
        now = datetime(2026, 8, 31, tzinfo=timezone.utc)
        for value in (None, "invalid", ""):
            self.assertEqual(afk_duration(value, now), "an unknown duration")
        future = (now + timedelta(hours=1)).isoformat()
        self.assertEqual(afk_duration(future, now), "0s")

    def test_naive_and_offset_timestamps(self):
        now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        self.assertEqual(afk_duration("2026-08-31T11:00:00", now), "1h")
        self.assertEqual(afk_duration("2026-08-31T13:00:00+02:00", now), "1h")
        self.assertEqual(
            afk_duration("2026-08-31T11:00:00", now.replace(tzinfo=None)), "1h"
        )


class AFKReturnTests(unittest.IsolatedAsyncioTestCase):
    async def test_return_embed_contains_username_and_duration(self):
        now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        cog = AFK(SimpleNamespace())
        cog.afk_cache_loaded = True
        cog.afk_users = {(100, 200), (101, 200)}
        message = SimpleNamespace(
            author=SimpleNamespace(id=200, name="Test.User", bot=False),
            guild=SimpleNamespace(id=100),
            mentions=[],
            channel=SimpleNamespace(send=AsyncMock()),
        )
        removed = {
            "user_id": 200,
            "reason": "Working",
            "set_at": (now - timedelta(hours=2, minutes=15, seconds=8)).isoformat(),
        }
        with (
            patch("cogs.afk.process_afk_message", new_callable=AsyncMock) as process,
            patch("cogs.afk.discord.utils.utcnow", return_value=now),
        ):
            process.return_value = (removed, [])
            await cog.on_message(message)
            await cog.on_message(message)
            process.assert_awaited_once_with(100, 200, set())
        message.channel.send.assert_awaited_once()
        kwargs = message.channel.send.call_args.kwargs
        embed = kwargs["embed"]
        self.assertEqual(embed.title, "Welcome back, Test.User")
        self.assertIn("You were AFK for **2h 15m 8s**.", embed.description)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertNotIn((100, 200), cog.afk_users)
        self.assertIn((101, 200), cog.afk_users)

    async def test_non_afk_author_gets_no_welcome(self):
        cog = AFK(SimpleNamespace())
        message = SimpleNamespace(
            author=SimpleNamespace(id=200, bot=False),
            guild=SimpleNamespace(id=100),
            mentions=[],
            channel=SimpleNamespace(send=AsyncMock()),
        )
        with patch("cogs.afk.process_afk_message", new_callable=AsyncMock) as process:
            process.return_value = (None, [])
            await cog.on_message(message)
        message.channel.send.assert_not_awaited()
