import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite
import pytz

import config
from cogs.inactivity import hours_since
from utils.database import claim_ticket, close_ticket, create_ticket_record, get_next_ticket_number, setup_database


class ConfigurationTests(unittest.TestCase):
    def test_unknown_guild_is_rejected(self):
        with self.assertRaises(ValueError):
            config.get_guild_config(1)

    def test_inactivity_age_uses_warning_timestamp(self):
        zone = pytz.timezone("Europe/Berlin")
        now = datetime.now(zone)
        value = (now - timedelta(hours=25)).isoformat()
        self.assertGreaterEqual(hours_since(value, now), 25)


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.original_database = config.DATABASE
        config.DATABASE = str(Path(self.temp_directory.name) / "test.db")
        await setup_database()

    async def asyncTearDown(self):
        config.DATABASE = self.original_database
        self.temp_directory.cleanup()

    async def test_ticket_numbers_are_sequential_per_guild(self):
        guild_id = next(iter(config.GUILDS))
        first = await get_next_ticket_number(guild_id)
        second = await get_next_ticket_number(guild_id)
        self.assertEqual(second, first + 1)

    async def test_claim_and_close_are_single_transition_operations(self):
        guild_id = next(iter(config.GUILDS))
        await create_ticket_record(100, guild_id, 200, "Test", datetime.now().isoformat())
        self.assertTrue(await claim_ticket(100, 300, datetime.now().isoformat()))
        self.assertFalse(await claim_ticket(100, 301, datetime.now().isoformat()))
        self.assertTrue(await close_ticket(100, datetime.now().isoformat(), 300, "Done"))
        self.assertFalse(await close_ticket(100, datetime.now().isoformat(), 300, "Done"))

    async def test_warning_timestamp_column_exists(self):
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute("PRAGMA table_info(tickets)")
            names = {row[1] for row in await cursor.fetchall()}
        self.assertIn("warned_at", names)


if __name__ == "__main__":
    unittest.main()
