import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import discord

import config
from cogs.bot_access import BotAccess
from utils.bot_access import (
    BOT_ACCESS_OWNER_ID,
    ban_bot_user,
    check_bot_access,
    is_bot_banned,
    parse_user_id,
    unban_bot_user,
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

    async def test_unban_persists_and_requires_owner(self):
        target = 1536561752659984500
        await ban_bot_user(target, BOT_ACCESS_OWNER_ID)
        with self.assertRaises(PermissionError):
            await unban_bot_user(target, 123)
        self.assertTrue(await is_bot_banned(target))
        self.assertTrue(await unban_bot_user(target, BOT_ACCESS_OWNER_ID))
        self.assertFalse(await is_bot_banned(target))
        self.assertFalse(await unban_bot_user(target, BOT_ACCESS_OWNER_ID))

    async def test_commands_notify_only_on_state_changes(self):
        target = 1536561752659984500
        user = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=BOT_ACCESS_OWNER_ID),
            client=SimpleNamespace(get_user=lambda target_id: user),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        cog = BotAccess()
        for command in (BotAccess.bbmaja, BotAccess.bbmaja):
            await command.callback(cog, interaction, str(target))
        self.assertEqual(user.send.await_count, 1)
        self.assertEqual(
            user.send.call_args.kwargs["embed"].title, "Bot Access Restricted"
        )
        for command in (BotAccess.bbmajaubb, BotAccess.bbmajaubb):
            await command.callback(cog, interaction, str(target))
        self.assertEqual(user.send.await_count, 2)
        self.assertEqual(
            user.send.call_args.kwargs["embed"].title, "Bot Access Restored"
        )

    async def test_closed_dms_do_not_revert_access_changes(self):
        target = 1536561752659984500
        failure = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            "Cannot send messages to this user",
        )
        user = SimpleNamespace(send=AsyncMock(side_effect=failure))
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=BOT_ACCESS_OWNER_ID),
            client=SimpleNamespace(get_user=lambda target_id: user),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await BotAccess.bbmaja.callback(BotAccess(), interaction, str(target))
        self.assertTrue(await is_bot_banned(target))
        await BotAccess.bbmajaubb.callback(BotAccess(), interaction, str(target))
        self.assertFalse(await is_bot_banned(target))
        self.assertIn(
            "Not delivered",
            interaction.followup.send.call_args.kwargs["embed"].fields[-1].value,
        )

    async def test_unauthorized_unban_command_leaves_ban_active(self):
        target = 1536561752659984500
        await ban_bot_user(target, BOT_ACCESS_OWNER_ID)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await BotAccess.bbmajaubb.callback(BotAccess(), interaction, str(target))
        self.assertTrue(await is_bot_banned(target))

    async def test_second_bot_owner_can_manage_access(self):
        target = 1536561752659984500
        second_owner = 1269233770834165860
        self.assertTrue(await ban_bot_user(target, second_owner))
        self.assertTrue(await is_bot_banned(target))
        self.assertTrue(await unban_bot_user(target, second_owner))
        self.assertFalse(await is_bot_banned(target))

    async def test_bot_owners_can_never_be_banned(self):
        for owner_id in config.BOT_OWNER_IDS:
            with self.assertRaises(ValueError):
                await ban_bot_user(owner_id, BOT_ACCESS_OWNER_ID)
            self.assertFalse(await is_bot_banned(owner_id))
