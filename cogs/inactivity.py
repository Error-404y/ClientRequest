from datetime import datetime

import aiosqlite
import discord
import pytz
from discord.ext import commands, tasks

import config
from utils.logger import log_exception, log_inactivity
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
                "SELECT channel_id, guild_id, user_id, warned_inactive, created_at, warned_at, waiting_on, waiting_changed_at FROM tickets WHERE status='open'"
            )
            rows = await cursor.fetchall()

        for (
            channel_id,
            guild_id,
            user_id,
            warned,
            created_at,
            warned_at,
            waiting_on,
            waiting_changed_at,
        ) in rows:
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

            if waiting_on != "user":
                continue

            if warned:
                try:
                    warning_age = hours_since(warned_at, guild_id)
                except (TypeError, ValueError):
                    warning_age = 0.0
                if warning_age >= config.INACTIVITY_CLOSE_HOURS:
                    try:
                        await close_ticket_channel(
                            channel=channel,
                            moderator=self.bot.user,
                            reason="Closed automatically while waiting for the ticket owner.",
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

            activity_reference = waiting_changed_at or created_at
            try:
                inactive_hours = hours_since(activity_reference, guild_id)
            except (TypeError, ValueError):
                inactive_hours = 0.0

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

async def setup(bot):
    await bot.add_cog(Inactivity(bot))
