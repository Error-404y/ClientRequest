from datetime import datetime

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.embeds import error as error_embed
from utils.logger import log_exception, log_interaction
from utils.permissions import is_staff


def format_duration(seconds):
    if seconds is None or seconds < 0:
        return "N/A"
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m {int(seconds % 60)}s"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h {int(minutes % 60)}m"
    days = hours / 24
    return f"{int(days)}d {int(hours % 24)}h"


def average_interval(rows):
    durations = []
    for started_at, ended_at in rows:
        try:
            duration = (
                datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
            ).total_seconds()
        except (TypeError, ValueError):
            continue
        if duration >= 0:
            durations.append(duration)
    return format_duration(sum(durations) / len(durations)) if durations else "N/A"


async def staff_metrics(guild_id, user_id):
    async with aiosqlite.connect(config.DATABASE) as database:
        assigned_cursor = await database.execute(
            "SELECT COUNT(*) FROM tickets WHERE claimed_by=? AND guild_id=?",
            (int(user_id), int(guild_id)),
        )
        closed_cursor = await database.execute(
            "SELECT COUNT(*) FROM tickets WHERE closed_by=? AND guild_id=?",
            (int(user_id), int(guild_id)),
        )
        claim_cursor = await database.execute(
            "SELECT created_at, claimed_at FROM tickets WHERE claimed_by=? AND claimed_at IS NOT NULL AND guild_id=?",
            (int(user_id), int(guild_id)),
        )
        resolution_cursor = await database.execute(
            "SELECT claimed_at, closed_at FROM tickets WHERE claimed_by=? AND claimed_at IS NOT NULL AND closed_at IS NOT NULL AND guild_id=?",
            (int(user_id), int(guild_id)),
        )
        assigned = (await assigned_cursor.fetchone())[0]
        closed = (await closed_cursor.fetchone())[0]
        claim_rows = await claim_cursor.fetchall()
        resolution_rows = await resolution_cursor.fetchall()
    return {
        "assigned": assigned,
        "closed": closed,
        "average_claim": average_interval(claim_rows),
        "average_resolution": average_interval(resolution_rows),
    }


class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def require_staff(self, interaction):
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.send_message(
                embed=error_embed("This command can only be used inside a server."),
                ephemeral=True,
            )
            return False
        if not config.is_guild_configured(interaction.guild.id):
            await interaction.response.send_message(
                embed=error_embed(
                    "This server must complete `/setup start` before ticket statistics are available."
                ),
                ephemeral=True,
            )
            return False
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed(
                    "You do not have permission to view staff statistics."
                ),
                ephemeral=True,
            )
            return False
        return True

    @app_commands.command(
        name="stats", description="View ticket performance for one staff member"
    )
    @app_commands.describe(
        member="Staff member to inspect, or leave empty for yourself"
    )
    async def stats(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        if not await self.require_staff(interaction):
            return
        target = member or interaction.user
        if not is_staff(target):
            await interaction.response.send_message(
                embed=error_embed(
                    f"{target.mention} is not recognized as staff in this server."
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        log_interaction(
            interaction.user,
            "stats",
            interaction.channel,
            details=f"Target: {target.id}",
        )
        try:
            metrics = await staff_metrics(interaction.guild.id, target.id)
        except Exception as error:
            reference = log_exception(
                "DATABASE",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context=f"Staff statistics lookup failed for {target.id}",
            )
            await interaction.followup.send(
                embed=error_embed(
                    f"Staff statistics could not be loaded. Error reference: `{reference}`"
                ),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Staff Ticket Performance",
            description=(
                f"Operational ticket metrics for {target.mention}. Values are limited "
                "to this server and use the ticket's current or final assignee."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Current or Final Assignments", value=str(metrics["assigned"])
        )
        embed.add_field(name="Tickets Closed", value=str(metrics["closed"]))
        embed.add_field(name="Average First Assignment", value=metrics["average_claim"])
        embed.add_field(
            name="Average Assigned Resolution",
            value=metrics["average_resolution"],
        )
        embed.add_field(
            name="Measurement Scope",
            value=(
                "Reassignments and released claims are not counted as completed work. "
                "The report reflects the assignment currently stored on each ticket."
            ),
            inline=False,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"{config.BOT_NAME} | Server-Scoped Staff Analytics")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="leaderboard",
        description="Compare current ticket performance across staff",
    )
    async def leaderboard(self, interaction: discord.Interaction):
        if not await self.require_staff(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        log_interaction(interaction.user, "leaderboard", interaction.channel)
        try:
            async with aiosqlite.connect(config.DATABASE) as database:
                claim_cursor = await database.execute(
                    "SELECT claimed_by, COUNT(*) FROM tickets WHERE claimed_by IS NOT NULL AND guild_id=? GROUP BY claimed_by ORDER BY COUNT(*) DESC, claimed_by ASC LIMIT 10",
                    (interaction.guild.id,),
                )
                close_cursor = await database.execute(
                    "SELECT closed_by, COUNT(*) FROM tickets WHERE closed_by IS NOT NULL AND guild_id=? GROUP BY closed_by ORDER BY COUNT(*) DESC, closed_by ASC LIMIT 10",
                    (interaction.guild.id,),
                )
                assignments = await claim_cursor.fetchall()
                closures = await close_cursor.fetchall()
        except Exception as error:
            reference = log_exception(
                "DATABASE",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Staff leaderboard lookup failed",
            )
            await interaction.followup.send(
                embed=error_embed(
                    f"The staff leaderboard could not be loaded. Error reference: `{reference}`"
                ),
                ephemeral=True,
            )
            return

        def ranking(rows):
            values = []
            for position, (user_id, count) in enumerate(rows, 1):
                member = interaction.guild.get_member(user_id)
                identity = member.mention if member else f"User ID `{user_id}`"
                values.append(f"**{position}.** {identity} | `{count}`")
            return "\n".join(values) or "No ticket data is available yet."

        embed = discord.Embed(
            title="Staff Ticket Leaderboard",
            description="A server-only overview based on current or final ticket ownership.",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Current or Final Assignments",
            value=ranking(assignments),
            inline=False,
        )
        embed.add_field(
            name="Tickets Closed",
            value=ranking(closures),
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Server-Scoped Staff Analytics")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Stats(bot))
