import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import aiosqlite
import discord

import config
from cogs.governance import Governance
from cogs.moderation import Moderation
from cogs.tickets import TicketControlRecovery
from cogs.transcript import download_file
from utils.database import close_ticket, create_ticket_record, setup_database
from views.ticket_buttons import CloseTicketModal, PrioritySelectionView


async def empty_history(**kwargs):
    for item in ():
        yield item


class ReviewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_acknowledges_before_approval_network_work(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            guild=SimpleNamespace(
                id=1,
                me=SimpleNamespace(
                    top_role=2, guild_permissions=SimpleNamespace(moderate_members=True)
                ),
            ),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        target = SimpleNamespace(id=456, bot=False, top_role=1, display_name="Target")

        async def queue(*args, **kwargs):
            interaction.response.defer.assert_awaited_once()
            return {"queued": True}

        with (
            patch("cogs.moderation.config.is_guild_configured", return_value=True),
            patch("cogs.moderation.is_staff", return_value=True),
            patch("cogs.moderation.can_moderate_target", return_value=True),
            patch("cogs.moderation.queue_moderation_approval", queue),
            patch(
                "cogs.moderation.approval_queued_embed",
                return_value=discord.Embed(title="Queued"),
            ),
        ):
            await Moderation.mutez.callback(
                SimpleNamespace(bot=object()),
                interaction,
                target,
                5,
                SimpleNamespace(value="m"),
            )
        interaction.followup.send.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()

    async def test_banned_approval_never_reaches_decision_handler(self):
        cog = SimpleNamespace(handle_approval_decision=AsyncMock())
        interaction = SimpleNamespace(data={"custom_id": "approval:approve:uuid"})
        with patch(
            "cogs.governance.check_bot_access", AsyncMock(return_value=False)
        ) as check:
            await Governance.on_interaction(cog, interaction)
        check.assert_awaited_once_with(interaction)
        cog.handle_approval_decision.assert_not_awaited()

    async def test_transcript_download_waits_for_complete_stream(self):
        reader = aiohttp.StreamReader(MagicMock(), limit=65536)
        reader.feed_data(b"first chunk")
        response = SimpleNamespace(status=200, content_length=None, content=reader)
        response_context = MagicMock()
        response_context.__aenter__ = AsyncMock(return_value=response)
        session = MagicMock()
        session.get.return_value = response_context
        session_context = MagicMock()
        session_context.__aenter__ = AsyncMock(return_value=session)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "cogs.transcript.aiohttp.ClientSession", return_value=session_context
            ),
        ):
            destination = Path(directory) / "asset.bin"
            task = asyncio.create_task(
                download_file("https://example.invalid/asset", destination)
            )
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            reader.feed_data(b" second chunk")
            reader.feed_eof()
            self.assertTrue(await task)
            self.assertEqual(destination.read_bytes(), b"first chunk second chunk")

    async def test_transcript_rejects_oversized_stream_without_partial_file(self):
        reader = aiohttp.StreamReader(MagicMock(), limit=65536)
        reader.feed_data(b"123456")
        reader.feed_eof()
        response_context = MagicMock()
        response_context.__aenter__ = AsyncMock(
            return_value=SimpleNamespace(
                status=200, content_length=None, content=reader
            )
        )
        session = MagicMock()
        session.get.return_value = response_context
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("cogs.transcript.aiohttp.ClientSession", return_value=context),
            patch("cogs.transcript.MAX_TRANSCRIPT_ASSET_BYTES", 5),
        ):
            destination = Path(directory) / "asset.bin"
            self.assertFalse(
                await download_file("https://example.invalid/asset", destination)
            )
            self.assertFalse(destination.exists())

    async def test_recovery_replaces_deleted_stored_controls(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 123
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"), "Deleted"
            )
        )
        channel.history = empty_history
        channel.send = AsyncMock(return_value=SimpleNamespace(id=789))
        cog = TicketControlRecovery(SimpleNamespace(get_channel=lambda _: channel))
        record = dict(
            id=1,
            channel_id=123,
            control_message_id=456,
            status="open",
            claimed_by=None,
            application="Issues",
            label=None,
        )
        with (
            patch(
                "cogs.tickets.get_ticket_controls",
                AsyncMock(side_effect=[[record], []]),
            ),
            patch("cogs.tickets.set_ticket_control_message", AsyncMock()) as save,
            patch("cogs.tickets.log_ticket"),
            patch("cogs.tickets.asyncio.sleep", AsyncMock()),
        ):
            await cog.on_ready()
        channel.send.assert_awaited_once()
        save.assert_awaited_once_with(123, 789)

    async def test_recovery_initializes_legacy_waiting_state_from_last_user_message(self):
        created_at = discord.utils.utcnow()
        message = SimpleNamespace(
            author=SimpleNamespace(id=222, bot=False),
            created_at=created_at,
        )

        async def history(**kwargs):
            yield message

        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 123
        channel.guild = SimpleNamespace(id=1)
        channel.history = history
        channel.fetch_message = AsyncMock(return_value=SimpleNamespace(id=456))
        cog = TicketControlRecovery(SimpleNamespace(get_channel=lambda _: channel))
        record = dict(
            id=1,
            channel_id=123,
            guild_id=1,
            control_message_id=456,
            status="open",
            claimed_by=None,
            application="Issues",
            label=None,
            user_id=222,
            waiting_on="staff",
            waiting_changed_at=None,
        )
        with (
            patch(
                "cogs.tickets.get_ticket_controls",
                AsyncMock(side_effect=[[record], []]),
            ),
            patch("cogs.tickets.set_ticket_waiting_on", AsyncMock()) as waiting,
            patch("cogs.tickets.asyncio.sleep", AsyncMock()),
        ):
            await cog.on_ready()
        waiting.assert_awaited_once_with(123, "staff", created_at.isoformat())

    async def test_recovery_does_not_duplicate_controls_on_permission_error(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.fetch_message = AsyncMock(
            side_effect=discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"), "No access"
            )
        )
        channel.send = AsyncMock()
        cog = TicketControlRecovery(SimpleNamespace(get_channel=lambda _: channel))
        record = dict(id=1, channel_id=123, control_message_id=456)
        with (
            patch(
                "cogs.tickets.get_ticket_controls",
                AsyncMock(side_effect=[[record], []]),
            ),
            patch("cogs.tickets.log_exception"),
            patch("cogs.tickets.asyncio.sleep", AsyncMock()),
        ):
            await cog.on_ready()
        channel.send.assert_not_awaited()

    async def test_revoked_staff_cannot_submit_close_or_priority(self):
        interaction = SimpleNamespace(
            user=object(), response=SimpleNamespace(send_message=AsyncMock())
        )
        with (
            patch("views.ticket_buttons.is_staff", return_value=False),
            patch("views.ticket_buttons.close_ticket_channel", AsyncMock()) as close,
            patch("utils.database.set_ticket_priority", AsyncMock()) as priority,
        ):
            await CloseTicketModal.on_submit(SimpleNamespace(), interaction)
            await PrioritySelectionView.update_priority(
                SimpleNamespace(), interaction, "High"
            )
        self.assertEqual(interaction.response.send_message.await_count, 2)
        close.assert_not_awaited()
        priority.assert_not_awaited()

    async def test_label_failure_reports_saved_but_not_synchronized(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 123
        control = SimpleNamespace(
            embeds=[discord.Embed(title="Ticket")],
            edit=AsyncMock(
                side_effect=discord.Forbidden(
                    SimpleNamespace(status=403, reason="Forbidden"), "No access"
                )
            ),
        )
        channel.fetch_message = AsyncMock(return_value=control)
        interaction = SimpleNamespace(
            user=SimpleNamespace(mention="staff"),
            channel=channel,
            guild_id=1,
            guild=object(),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        ticket = dict(
            guild_id=1, status="open", control_message_id=456, uuid="ticket-uuid"
        )
        with (
            patch("cogs.tickets.is_staff", return_value=True),
            patch("cogs.tickets.get_ticket_record", AsyncMock(return_value=ticket)),
            patch("cogs.tickets.set_ticket_label", AsyncMock(return_value=True)),
            patch("cogs.tickets.log_exception"),
            patch("cogs.tickets.log_ticket"),
            patch("cogs.tickets.log_interaction"),
        ):
            await TicketControlRecovery.labelz.callback(
                SimpleNamespace(), interaction, SimpleNamespace(value="Technical")
            )
        self.assertIn(
            "could not be updated",
            interaction.followup.send.call_args.kwargs["embed"].description,
        )


class ReviewDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = dict(config.GUILDS)
        self.database_patch = patch.object(
            config, "DATABASE", str(Path(self.directory.name) / "test.db")
        )
        self.database_patch.start()
        await setup_database()

    async def asyncTearDown(self):
        self.database_patch.stop()
        config.replace_guild_configs(self.settings)
        self.directory.cleanup()

    async def test_inactivity_close_rejects_reset_and_replaced_warning(self):
        await create_ticket_record(123, 1, 2, "Issues", "2026-09-01T00:00:00+00:00")
        async with aiosqlite.connect(config.DATABASE) as db:
            await db.execute(
                "UPDATE tickets SET warned_inactive=1, warned_at='new-warning' WHERE channel_id=123"
            )
            await db.commit()
        self.assertFalse(
            await close_ticket(123, "closed", expected_warned_at="old-warning")
        )
        async with aiosqlite.connect(config.DATABASE) as db:
            await db.execute(
                "UPDATE tickets SET warned_inactive=0, warned_at=NULL WHERE channel_id=123"
            )
            await db.commit()
        self.assertFalse(
            await close_ticket(123, "closed", expected_warned_at="new-warning")
        )
        self.assertTrue(await close_ticket(123, "closed"))

    async def test_matching_inactivity_warning_can_close(self):
        await create_ticket_record(123, 1, 2, "Issues", "2026-09-01T00:00:00+00:00")
        async with aiosqlite.connect(config.DATABASE) as db:
            await db.execute(
                "UPDATE tickets SET warned_inactive=1, warned_at='warning' WHERE channel_id=123"
            )
            await db.commit()
        self.assertTrue(await close_ticket(123, "closed", expected_warned_at="warning"))

    async def test_uuid_migration_does_not_rescan_history_on_restart(self):
        queries = []
        original = aiosqlite.Connection.execute

        async def execute(connection, sql, parameters=None):
            queries.append(sql)
            return await original(connection, sql, parameters or ())

        with patch.object(aiosqlite.Connection, "execute", execute):
            await setup_database()
        self.assertFalse(
            any("SELECT id, uuid FROM tickets" in query for query in queries)
        )
        self.assertFalse(
            any(
                "SELECT id, guild_id, uuid FROM infractions" in query
                for query in queries
            )
        )
