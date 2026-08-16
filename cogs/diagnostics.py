import platform
import time
from datetime import timedelta

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.logger import emit, log_exception
from utils.permissions import is_owner


def format_uptime(seconds):
    value = int(max(0, seconds))
    days, value = divmod(value, 86400)
    hours, value = divmod(value, 3600)
    minutes, seconds = divmod(value, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


class Diagnostics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.validated = False
        if not hasattr(bot, "started_at_monotonic"):
            bot.started_at_monotonic = time.monotonic()

    async def database_status(self):
        started = time.perf_counter()
        try:
            async with aiosqlite.connect(config.DATABASE) as database:
                cursor = await database.execute("PRAGMA integrity_check")
                result = await cursor.fetchone()
                cursor = await database.execute("SELECT COUNT(*) FROM tickets WHERE status='open'")
                open_tickets = (await cursor.fetchone())[0]
            latency = (time.perf_counter() - started) * 1000
            return result[0] == "ok", latency, open_tickets, result[0]
        except Exception as error:
            reference = log_exception("DATABASE", error, context="Health check failed")
            return False, 0.0, 0, reference

    def configuration_results(self):
        results = []
        for guild_id, guild_config in config.GUILDS.items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                results.append((guild_config.get("NAME", str(guild_id)), "Unavailable", "Bot is not connected"))
                continue
            missing = []
            for key in ("TICKET_CATEGORY_ID", "TICKET_PANEL_CHANNEL_ID", "TICKET_ARCHIVE_CATEGORY_ID"):
                if guild.get_channel(guild_config[key]) is None:
                    missing.append(key)
            for role_id in guild_config["OWNER_ROLES"]:
                if guild.get_role(role_id) is None:
                    missing.append(f"OWNER_ROLE:{role_id}")
            status = "Operational" if not missing else "Attention required"
            detail = "All configured resources are available" if not missing else ", ".join(missing)
            results.append((guild.name, status, detail))
        return results

    async def run_startup_validation(self):
        database_ok, database_latency, open_tickets, database_detail = await self.database_status()
        level = "SUCCESS" if database_ok else "ERROR"
        emit(level, "HEALTH", f"Database status={database_detail} latency={database_latency:.1f}ms open_tickets={open_tickets}")
        for guild_name, status, detail in self.configuration_results():
            level = "SUCCESS" if status == "Operational" else "WARNING"
            emit(level, "HEALTH", f"{guild_name} | {status} | {detail}")

    @commands.Cog.listener()
    async def on_ready(self):
        if self.validated:
            return
        self.validated = True
        await self.run_startup_validation()

    @app_commands.command(name="healthz", description="Display the current bot and database health")
    async def healthz(self, interaction: discord.Interaction):
        if not is_owner(interaction.user):
            await interaction.response.send_message("You do not have permission to view system health.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        database_ok, database_latency, open_tickets, database_detail = await self.database_status()
        uptime = format_uptime(time.monotonic() - self.bot.started_at_monotonic)
        embed = discord.Embed(
            title=f"{config.BOT_NAME} System Health",
            description="Live operational status for the ticket infrastructure.",
            color=0x2ECC71 if database_ok else 0xE74C3C,
        )
        embed.add_field(name="Overall Status", value="Operational" if database_ok else "Degraded", inline=True)
        embed.add_field(name="Discord Latency", value=f"{self.bot.latency * 1000:.1f} ms", inline=True)
        embed.add_field(name="Database Latency", value=f"{database_latency:.1f} ms", inline=True)
        embed.add_field(name="Uptime", value=uptime, inline=True)
        embed.add_field(name="Connected Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Open Tickets", value=str(open_tickets), inline=True)
        embed.add_field(name="Loaded Modules", value=str(len(self.bot.extensions)), inline=True)
        embed.add_field(name="Database Integrity", value=str(database_detail), inline=True)
        embed.add_field(name="Runtime", value=f"Python {platform.python_version()} | discord.py {discord.__version__}", inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Private diagnostics")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="debugz", description="Run detailed ticket-system configuration diagnostics")
    async def debugz(self, interaction: discord.Interaction):
        if not is_owner(interaction.user):
            await interaction.response.send_message("You do not have permission to run diagnostics.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        results = self.configuration_results()
        database_ok, database_latency, open_tickets, database_detail = await self.database_status()
        problems = sum(1 for _, status, _ in results if status != "Operational") + (0 if database_ok else 1)
        embed = discord.Embed(
            title=f"{config.BOT_NAME} Diagnostic Report",
            description=f"Diagnostic run completed with {problems} item{'s' if problems != 1 else ''} requiring attention.",
            color=0x2ECC71 if problems == 0 else 0xF0B232,
        )
        for guild_name, status, detail in results:
            value = f"Status: **{status}**\n{detail}"
            embed.add_field(name=guild_name[:256], value=value[:1024], inline=False)
        extension_names = "\n".join(sorted(self.bot.extensions)) or "No extensions loaded"
        embed.add_field(name="Loaded Extensions", value=extension_names[:1024], inline=False)
        embed.add_field(
            name="Database",
            value=f"Integrity: {database_detail}\nLatency: {database_latency:.1f} ms\nOpen tickets: {open_tickets}",
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Owner diagnostics")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Diagnostics(bot))
