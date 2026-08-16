import platform
import io
import json
import time
from datetime import datetime, timedelta

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.logger import emit, log_exception, log_performance, redact
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
        self.reported_workers = set()
        if not hasattr(bot, "started_at_monotonic"):
            bot.started_at_monotonic = time.monotonic()
        self.monitor_workers.start()

    def cog_unload(self):
        self.monitor_workers.cancel()

    async def database_status(self):
        started = time.perf_counter()
        try:
            async with aiosqlite.connect(config.DATABASE) as database:
                cursor = await database.execute("PRAGMA integrity_check")
                result = await cursor.fetchone()
                cursor = await database.execute("SELECT COUNT(*) FROM tickets WHERE status='open'")
                open_tickets = (await cursor.fetchone())[0]
            latency = log_performance("diagnostics.database_health", started, threshold_ms=250)
            return result[0] == "ok", latency, open_tickets, result[0]
        except Exception as error:
            reference = log_exception("DATABASE", error, context="Health check failed")
            return False, 0.0, 0, reference

    async def error_summary(self, limit=5):
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT reference, category, error_type, occurrence_count, last_seen FROM error_events ORDER BY last_seen DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "reference": row[0],
                "category": row[1],
                "error_type": row[2],
                "occurrences": row[3],
                "last_seen": row[4],
            }
            for row in rows
        ]

    async def error_details(self, reference):
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT reference, fingerprint, category, error_type, message, traceback, context, guild_id, channel_id, user_id, occurrence_count, first_seen, last_seen FROM error_events WHERE UPPER(reference)=UPPER(?)",
                (reference.strip(),),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        keys = ("reference", "fingerprint", "category", "error_type", "message", "traceback", "context", "guild_id", "channel_id", "user_id", "occurrence_count", "first_seen", "last_seen")
        return dict(zip(keys, row))

    async def performance_summary(self):
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT COUNT(*), COALESCE(AVG(duration_ms), 0), COALESCE(MAX(duration_ms), 0) FROM performance_events WHERE created_at>=?",
                (cutoff,),
            )
            row = await cursor.fetchone()
        return {"operations": row[0], "average_ms": row[1], "maximum_ms": row[2]}

    def worker_results(self):
        results = []
        monitored = {
            "Inactivity": "check_inactivity",
            "Escalations": "audit_escalations",
            "Diagnostics": "monitor_workers",
        }
        for cog_name, attribute in monitored.items():
            cog = self.bot.get_cog(cog_name)
            worker = getattr(cog, attribute, None) if cog else None
            if worker is None:
                results.append((f"{cog_name}.{attribute}", "Missing", "Worker or extension not loaded"))
                continue
            if worker.failed():
                status = "Failed"
            elif worker.is_running():
                status = "Running"
            else:
                status = "Stopped"
            next_run = getattr(worker, "next_iteration", None)
            detail = f"Next run: {next_run.isoformat()}" if next_run else "No next execution scheduled"
            results.append((f"{cog_name}.{attribute}", status, detail))
        return results

    def permission_results(self):
        results = []
        required = (
            "view_channel",
            "send_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
            "manage_channels",
            "manage_roles",
        )
        for guild_id, guild_config in config.GUILDS.items():
            guild = self.bot.get_guild(guild_id)
            if guild is None or guild.me is None:
                results.append((guild_config.get("NAME", str(guild_id)), ["bot_not_connected"]))
                continue
            panel = guild.get_channel(guild_config["TICKET_PANEL_CHANNEL_ID"])
            permissions = panel.permissions_for(guild.me) if panel else guild.me.guild_permissions
            missing = [name for name in required if not getattr(permissions, name, False)]
            results.append((guild.name, missing))
        return results

    @tasks.loop(minutes=1)
    async def monitor_workers(self):
        for name, status, detail in self.worker_results():
            if name == "Diagnostics.monitor_workers":
                continue
            if status == "Running":
                self.reported_workers.discard(name)
                continue
            if name in self.reported_workers:
                continue
            self.reported_workers.add(name)
            error = RuntimeError(f"Background worker {name} is {status.lower()}")
            log_exception("WORKER", error, context=detail)

    @monitor_workers.before_loop
    async def before_monitor_workers(self):
        await self.bot.wait_until_ready()

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
        recent_errors = await self.error_summary(limit=1)
        performance = await self.performance_summary()
        workers = self.worker_results()
        failed_workers = sum(1 for _, status, _ in workers if status != "Running")
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
        embed.add_field(name="Background Workers", value=f"{len(workers) - failed_workers}/{len(workers)} running", inline=True)
        embed.add_field(name="24h Operations", value=str(performance["operations"]), inline=True)
        embed.add_field(name="Slowest Operation", value=f"{performance['maximum_ms']:.1f} ms", inline=True)
        embed.add_field(name="Latest Error", value=recent_errors[0]["reference"] if recent_errors else "No recorded errors", inline=True)
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
        workers = self.worker_results()
        permissions = self.permission_results()
        recent_errors = await self.error_summary()
        performance = await self.performance_summary()
        problems = (
            sum(1 for _, status, _ in results if status != "Operational")
            + sum(1 for _, status, _ in workers if status != "Running")
            + sum(1 for _, missing in permissions if missing)
            + (0 if database_ok else 1)
        )
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
        worker_lines = [f"{name}: **{status}**" for name, status, _ in workers]
        embed.add_field(name="Background Workers", value="\n".join(worker_lines)[:1024], inline=False)
        permission_lines = [
            f"{guild_name}: {'Operational' if not missing else 'Missing ' + ', '.join(missing)}"
            for guild_name, missing in permissions
        ]
        embed.add_field(name="Discord Permissions", value="\n".join(permission_lines)[:1024], inline=False)
        error_lines = [
            f"`{item['reference']}` {item['category']} / {item['error_type']} | {item['occurrences']} occurrence(s)"
            for item in recent_errors
        ]
        embed.add_field(name="Recent Error Groups", value="\n".join(error_lines)[:1024] if error_lines else "No recorded errors", inline=False)
        embed.add_field(
            name="Performance Window",
            value=f"Operations: {performance['operations']}\nAverage: {performance['average_ms']:.1f} ms\nMaximum: {performance['maximum_ms']:.1f} ms",
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Owner diagnostics")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="debugerror", description="Display a stored error group by reference")
    @app_commands.describe(reference="Error reference such as ERR-A31F9C20")
    async def debugerror(self, interaction: discord.Interaction, reference: str):
        if not is_owner(interaction.user):
            await interaction.response.send_message("You do not have permission to inspect error records.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        record = await self.error_details(reference)
        if record is None:
            await interaction.followup.send("No error record was found for that reference.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"Error Investigation {record['reference']}",
            description="Sanitized diagnostic details for the selected grouped error.",
            color=0xED4245,
        )
        embed.add_field(name="Classification", value=f"{record['category']} / {record['error_type']}", inline=True)
        embed.add_field(name="Occurrences", value=str(record["occurrence_count"]), inline=True)
        embed.add_field(name="Fingerprint", value=f"`{record['fingerprint']}`", inline=True)
        embed.add_field(name="First Seen", value=record["first_seen"], inline=False)
        embed.add_field(name="Last Seen", value=record["last_seen"], inline=False)
        context = record["context"] or "No additional context"
        embed.add_field(name="Context", value=redact(context)[:1024], inline=False)
        embed.add_field(name="Error Message", value=redact(record["message"])[:1024], inline=False)
        identifiers = f"Guild: {record['guild_id'] or 'Unknown'}\nChannel: {record['channel_id'] or 'Unknown'}\nUser: {record['user_id'] or 'Unknown'}"
        embed.add_field(name="Identifiers", value=identifiers, inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Restricted error intelligence")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="debugexport", description="Export a sanitized system diagnostic package")
    async def debugexport(self, interaction: discord.Interaction):
        if not is_owner(interaction.user):
            await interaction.response.send_message("You do not have permission to export diagnostics.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        started = time.perf_counter()
        database_ok, database_latency, open_tickets, database_detail = await self.database_status()
        errors = await self.error_summary(limit=50)
        report = {
            "generated_at": datetime.now().isoformat(),
            "bot": config.BOT_NAME,
            "runtime": {"python": platform.python_version(), "discord_py": discord.__version__},
            "health": {
                "database_ok": database_ok,
                "database_detail": redact(database_detail),
                "database_latency_ms": database_latency,
                "discord_latency_ms": self.bot.latency * 1000,
                "open_tickets": open_tickets,
                "uptime": format_uptime(time.monotonic() - self.bot.started_at_monotonic),
            },
            "configuration": self.configuration_results(),
            "permissions": self.permission_results(),
            "workers": self.worker_results(),
            "performance": await self.performance_summary(),
            "recent_error_groups": errors,
            "loaded_extensions": sorted(self.bot.extensions),
        }
        payload = json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")
        file = discord.File(io.BytesIO(payload), filename=f"maja-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        duration = log_performance("diagnostics.export", started, threshold_ms=1000, guild=interaction.guild)
        embed = discord.Embed(
            title=f"{config.BOT_NAME} Diagnostic Export",
            description="The sanitized operations package was generated successfully.",
            color=0x5865F2,
        )
        embed.add_field(name="Error Groups", value=str(len(errors)), inline=True)
        embed.add_field(name="Generation Time", value=f"{duration:.1f} ms", inline=True)
        embed.add_field(name="Sensitive Values", value="Redacted", inline=True)
        embed.set_footer(text=f"{config.BOT_NAME} | Owner-only export")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Diagnostics(bot))
