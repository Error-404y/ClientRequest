from datetime import datetime

import aiosqlite
import discord
import pytz
from discord.ext import commands, tasks

import config
from utils.logger import log_exception, log_inactivity
from utils.ticket_actions import close_ticket_channel

timezone = pytz.timezone(config.TIMEZONE)


def hours_since(value, now=None):
    if not value:
        return 0.0
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = timezone.localize(parsed)
    current = now or datetime.now(timezone)
    return max(0.0, (current - parsed.astimezone(timezone)).total_seconds() / 3600.0)


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
                        (datetime.now(timezone).isoformat(), channel_id, guild_id),
                    )
                    await db.commit()
                log_inactivity(
                    "Missing channel marked deleted",
                    details=f"Guild ID: {guild_id}, Channel ID: {channel_id}",
                )
                continue

            if warned:
                try:
                    warning_age = hours_since(warned_at)
                except (TypeError, ValueError):
                    warning_age = 0.0
                if warning_age >= config.INACTIVITY_CLOSE_HOURS:
                    try:
                        await close_ticket_channel(
                            channel=channel,
                            moderator=self.bot.user,
                            reason="Closed automatically due to inactivity.",
                            bot=self.bot,
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
            human_activity = False
            try:
                async for message in channel.history(limit=None):
                    if last_activity is None:
                        last_activity = message.created_at.astimezone(timezone)
                    if not message.author.bot:
                        human_activity = True
                        break
            except discord.HTTPException as error:
                log_exception(
                    "INACTIVITY",
                    error,
                    guild=guild,
                    channel=channel,
                    context="Inactivity history lookup failed",
                )

            if last_activity is None:
                try:
                    inactive_hours = hours_since(created_at)
                except (TypeError, ValueError):
                    inactive_hours = 0.0
            else:
                inactive_hours = (
                    datetime.now(timezone) - last_activity
                ).total_seconds() / 3600.0

            if (
                not human_activity
                and hours_since(created_at) >= config.NO_RESPONSE_ESCALATION_HOURS
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

            warned_at_value = datetime.now(timezone).isoformat()
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
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                "SELECT warned_inactive FROM tickets WHERE channel_id=? AND guild_id=? AND status='open'",
                (message.channel.id, message.guild.id),
            )
            row = await cursor.fetchone()
            if row and row[0]:
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
