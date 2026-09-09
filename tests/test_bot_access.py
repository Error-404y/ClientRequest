import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite

import config
from cogs.bot_access import BotAccess
from utils.bot_access import (
    BOT_ACCESS_OWNER_ID,
    ban_bot_user,
    check_bot_access,
    is_bot_banned,
    parse_user_id,
)


class BotAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            config, "DATABASE", str(Path(self.directory.name) / "test.db")
        )
        self.database_patch.start()
        async with aiosqlite.connect(config.DATABASE) as db:
            await db.execute(
                "CREATE TABLE bot_bans (user_id INTEGER PRIMARY KEY, banned_by INTEGER NOT NULL, created_at TEXT NOT NULL)"
            )
            await db.commit()

    async def asyncTearDown(self):
        self.database_patch.stop()
        self.directory.cleanup()

    async def test_ban_is_persistent_and_idempotent(self):
        target = 1536561752659984500
        self.assertFalse(await is_bot_banned(target))
        self.assertTrue(await ban_bot_user(target, BOT_ACCESS_OWNER_ID))
        self.assertFalse(await ban_bot_user(target, BOT_ACCESS_OWNER_ID))
        self.assertTrue(await is_bot_banned(target))

    async def test_owner_and_unauthorized_bans_rejected(self):
        with self.assertRaises(ValueError):
            await ban_bot_user(BOT_ACCESS_OWNER_ID, BOT_ACCESS_OWNER_ID)
        with self.assertRaises(PermissionError):
            await ban_bot_user(1536561752659984500, 123)

    def test_only_numeric_ids_accepted(self):
        self.assertEqual(parse_user_id(str(BOT_ACCESS_OWNER_ID)), BOT_ACCESS_OWNER_ID)
        for value in (
            "name",
            "<@1536561752659984514>",
            "123",
            "1.536561752659984514e18",
            " 1536561752659984514",
            "99999999999999999999",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_user_id(value)

    async def test_banned_interaction_denied_across_servers(self):
        target = 1536561752659984500
        await ban_bot_user(target, BOT_ACCESS_OWNER_ID)
        for guild_id in (1, 2, None):
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=target),
                guild_id=guild_id,
                type=SimpleNamespace(name="application_command"),
                response=SimpleNamespace(
                    is_done=lambda: False, send_message=AsyncMock()
                ),
            )
            self.assertFalse(await check_bot_access(interaction))
            kwargs = interaction.response.send_message.call_args.kwargs
            self.assertTrue(kwargs["ephemeral"])
            self.assertIn("banned", kwargs["embed"].description)

    async def test_other_admin_cannot_invoke_command(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await BotAccess.bbmaja.callback(BotAccess(), interaction, "1536561752659984500")
        self.assertFalse(await is_bot_banned(1536561752659984500))
        interaction.response.send_message.assert_awaited_once()
