from datetime import datetime

import aiosqlite
import discord
import pytz
from discord.ext import commands, tasks

import config
from utils.logger import log_exception, log_inactivity
from utils.permissions import is_staff
from utils.ticket_actions import close_ticket_channel


def guild_timezone(guild_id):
    return pytz.timezone(config.get_timezone(guild_id))


def hours_since(value, guild_id, now=None):
    if not value:
        return 0.0
    timezone = guild_timezone(guild_id)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = timezone.localize(parsed)
    current = now or datetime.now(timezone)
    return max(0.0, (current - parsed.astimezone(timezone)).total_seconds() / 3600.0)


def is_ticket_participant(message, user_id):
    return message.author.id == user_id or is_staff(message.author)


class Inactivity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_inactivity.add_exception_type(aiosqlite.Error)
        self.check_inactivity.start()

    def cog_unload(self):
        self.check_inactivity.cancel()

    @tasks.loop(minutes=30)
    async def check_inactivity(self):
        log_inactivity("Running 30-minute inactivity audit on open tickets")
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                "SELECT channel_id, guild_id, user_id, warned_inactive, created_at, warned_at FROM tickets WHERE status='open'"
            )
            rows = await cursor.fetchall()

        for channel_id, guild_id, user_id, warned, created_at, warned_at in rows:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                log_inactivity(
                    "Configured guild is unavailable", details=f"Guild ID: {guild_id}"
                )
                continue

            channel = guild.get_channel(channel_id)
            if channel is None:
                async with aiosqlite.connect(config.DATABASE) as db:
                    await db.execute(
                        "UPDATE tickets SET status='deleted', closed_at=? WHERE channel_id=? AND guild_id=?",
                        (
                            datetime.now(guild_timezone(guild_id)).isoformat(),
                            channel_id,
                            guild_id,
                        ),
                    )
                    await db.commit()
                log_inactivity(
                    "Missing channel marked deleted",
                    details=f"Guild ID: {guild_id}, Channel ID: {channel_id}",
                )
                continue

            if warned:
                try:
                    warning_age = hours_since(warned_at, guild_id)
                except (TypeError, ValueError):
                    warning_age = 0.0
                if warning_age >= config.INACTIVITY_CLOSE_HOURS:
                    try:
                        timezone = guild_timezone(guild_id)
                        warning_time = datetime.fromisoformat(warned_at)
                        if warning_time.tzinfo is None:
                            warning_time = timezone.localize(warning_time)
                        responded = False
                        async for message in channel.history(
                            limit=None, after=warning_time, oldest_first=True
                        ):
                            if not message.author.bot and is_ticket_participant(
                                message, user_id
                            ):
                                responded = True
                                break
                        if responded:
                            async with aiosqlite.connect(config.DATABASE) as db:
                                await db.execute(
                                    "UPDATE tickets SET warned_inactive=0, warned_at=NULL WHERE channel_id=? AND guild_id=? AND status='open' AND warned_at=?",
                                    (channel_id, guild_id, warned_at),
                                )
                                await db.commit()
                            continue
                        await close_ticket_channel(
                            channel=channel,
                            moderator=self.bot.user,
                            reason="Closed automatically due to inactivity.",
                            bot=self.bot,
                            expected_warned_at=warned_at,
                        )
                    except Exception as error:
                        log_exception(
                            "INACTIVITY",
                            error,
                            guild=guild,
                            channel=channel,
                            context="Automatic inactivity close failed",
                        )
                continue

            last_activity = None
            try:
                async for message in channel.history(limit=250):
                    if not message.author.bot and is_ticket_participant(
                        message, user_id
                    ):
                        last_activity = message.created_at.astimezone(
                            guild_timezone(guild_id)
                        )
                        break
            except discord.HTTPException as error:
                log_exception(
                    "INACTIVITY",
                    error,
                    guild=guild,
                    channel=channel,
                    context="Inactivity history lookup failed",
                )
                continue

            if last_activity is None:
                try:
                    inactive_hours = hours_since(created_at, guild_id)
                except (TypeError, ValueError):
                    inactive_hours = 0.0
            else:
                inactive_hours = (
                    datetime.now(guild_timezone(guild_id)) - last_activity
                ).total_seconds() / 3600.0

            if (
                last_activity is None
                and hours_since(created_at, guild_id)
                >= config.NO_RESPONSE_ESCALATION_HOURS
            ):
                continue

            if inactive_hours < config.INACTIVITY_WARN_HOURS:
                continue

            applicant = guild.get_member(user_id)
            if applicant is None:
                try:
                    applicant = await guild.fetch_member(user_id)
                except discord.HTTPException as error:
                    log_exception(
                        "INACTIVITY",
                        error,
                        guild=guild,
                        channel=channel,
                        user=user_id,
                        context="Failed to fetch inactive ticket applicant",
                    )
                    applicant = None

            mention = applicant.mention if applicant else f"<@{user_id}>"
            embed = discord.Embed(
                title="Inactivity Warning",
                description=(
                    f"Hello {mention}.\n\n"
                    f"This ticket has been inactive for {config.INACTIVITY_WARN_HOURS} hours.\n"
                    f"It will automatically close in {config.INACTIVITY_CLOSE_HOURS} hours unless a new message is sent."
                ),
                color=discord.Color.orange(),
            )
            try:
                await channel.send(content=mention, embed=embed)
            except discord.HTTPException as error:
                log_exception(
                    "INACTIVITY",
                    error,
                    guild=guild,
                    channel=channel,
                    user=applicant or user_id,
                    context="Inactivity warning delivery failed",
                )
                continue

            warned_at_value = datetime.now(guild_timezone(guild_id)).isoformat()
            async with aiosqlite.connect(config.DATABASE) as db:
                await db.execute(
                    "UPDATE tickets SET warned_inactive=1, warned_at=? WHERE channel_id=? AND guild_id=? AND status='open'",
                    (warned_at_value, channel_id, guild_id),
                )
                await db.commit()
            log_inactivity(
                "Issued inactivity warning",
                channel,
                applicant,
                details=f"Inactive hours: {inactive_hours:.1f}",
            )

    @check_inactivity.before_loop
    async def before_check_inactivity(self):
        await self.bot.wait_until_ready()

    @check_inactivity.error
    async def check_inactivity_error(self, error):
        log_exception(
            "INACTIVITY", error, context="Inactivity background worker stopped"
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if not getattr(message.channel, "topic", None) or "ticket_owner:" not in (
            message.channel.topic
        ):
            return
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                "SELECT warned_inactive, user_id FROM tickets WHERE channel_id=? AND guild_id=? AND status='open'",
                (message.channel.id, message.guild.id),
            )
            row = await cursor.fetchone()
            if row and row[0] and is_ticket_participant(message, row[1]):
                await db.execute(
                    "UPDATE tickets SET warned_inactive=0, warned_at=NULL WHERE channel_id=? AND guild_id=?",
                    (message.channel.id, message.guild.id),
                )
                await db.commit()
                log_inactivity(
                    "Reset inactivity state", message.channel, message.author
                )


async def setup(bot):
    await bot.add_cog(Inactivity(bot))
