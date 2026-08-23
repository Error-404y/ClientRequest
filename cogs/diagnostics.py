import io
import json
import platform
import time
from datetime import datetime, timedelta

import aiosqlite
import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.embeds import error as error_embed
from utils.logger import emit, log_exception, log_performance, redact
from utils.permissions import can_setup

timezone = pytz.timezone(config.TIMEZONE)


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


def clean_label(value):
    return value.replace("_", " ").title()


class Diagnostics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ready_reported = False
        self.reported_worker_failures = set()
        if not hasattr(bot, "started_at_monotonic"):
            bot.started_at_monotonic = time.monotonic()
        self.watch_workers.add_exception_type(aiosqlite.Error)
        self.watch_workers.start()

    def cog_unload(self):
        self.watch_workers.cancel()

    async def database_health(self, guild_id=None):
        started = time.perf_counter()
        try:
            async with aiosqlite.connect(config.DATABASE) as database:
                integrity_cursor = await database.execute("PRAGMA integrity_check")
                integrity = (await integrity_cursor.fetchone())[0]
                if guild_id is None:
                    ticket_cursor = await database.execute(
                        "SELECT COUNT(*) FROM tickets WHERE status='open'"
                    )
                else:
                    ticket_cursor = await database.execute(
                        "SELECT COUNT(*) FROM tickets WHERE status='open' AND guild_id=?",
                        (guild_id,),
                    )
                open_tickets = (await ticket_cursor.fetchone())[0]
                cutoff = (datetime.now(timezone) - timedelta(hours=24)).isoformat()
                if guild_id is None:
                    error_cursor = await database.execute(
                        "SELECT COALESCE(SUM(occurrence_count), 0) FROM error_events WHERE last_seen>=?",
                        (cutoff,),
                    )
                else:
                    error_cursor = await database.execute(
                        "SELECT COALESCE(SUM(occurrence_count), 0) FROM error_events WHERE last_seen>=? AND guild_id=?",
                        (cutoff, guild_id),
                    )
                errors_24h = (await error_cursor.fetchone())[0]
            latency = log_performance(
                "database.health", started, threshold_ms=250, guild=guild_id
            )
            return {
                "ok": integrity == "ok",
                "integrity": integrity,
                "latency_ms": latency,
                "open_tickets": open_tickets,
                "errors_24h": errors_24h,
            }
        except Exception as error:
            reference = log_exception(
                "DATABASE", error, context="Diagnostic database check"
            )
            return {
                "ok": False,
                "integrity": reference,
                "latency_ms": 0.0,
                "open_tickets": 0,
                "errors_24h": 1,
            }

    async def recent_errors(self, guild_id=None, limit=5, full=False):
        columns = (
            "reference",
            "category",
            "error_type",
            "message",
            "context",
            "guild_id",
            "channel_id",
            "user_id",
            "occurrence_count",
            "first_seen",
            "last_seen",
            "fingerprint",
            "traceback",
        )
        async with aiosqlite.connect(config.DATABASE) as database:
            if guild_id is None:
                cursor = await database.execute(
                    "SELECT reference, category, error_type, message, context, guild_id, channel_id, user_id, occurrence_count, first_seen, last_seen, fingerprint, traceback FROM error_events ORDER BY last_seen DESC LIMIT ?",
                    (limit,),
                )
            else:
                cursor = await database.execute(
                    "SELECT reference, category, error_type, message, context, guild_id, channel_id, user_id, occurrence_count, first_seen, last_seen, fingerprint, traceback FROM error_events WHERE guild_id=? ORDER BY last_seen DESC LIMIT ?",
                    (guild_id, limit),
                )
            rows = await cursor.fetchall()
        records = [dict(zip(columns, row, strict=True)) for row in rows]
        if not full:
            for record in records:
                record.pop("fingerprint")
                record.pop("traceback")
        return records

    async def find_error(self, reference, guild_id):
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT reference, category, error_type, message, context, guild_id, channel_id, user_id, occurrence_count, first_seen, last_seen, fingerprint, traceback FROM error_events WHERE UPPER(reference)=UPPER(?) AND guild_id=?",
                (reference.strip(), guild_id),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        keys = (
            "reference",
            "category",
            "error_type",
            "message",
            "context",
            "guild_id",
            "channel_id",
            "user_id",
            "occurrence_count",
            "first_seen",
            "last_seen",
            "fingerprint",
            "traceback",
        )
        return dict(zip(keys, row, strict=True))

    async def slow_operations(self, guild_id=None):
        cutoff = (datetime.now(timezone) - timedelta(hours=24)).isoformat()
        async with aiosqlite.connect(config.DATABASE) as database:
            if guild_id is None:
                count_cursor = await database.execute(
                    "SELECT COUNT(*) FROM performance_events WHERE created_at>=?",
                    (cutoff,),
                )
                slowest_cursor = await database.execute(
                    "SELECT operation, duration_ms FROM performance_events WHERE created_at>=? ORDER BY duration_ms DESC LIMIT 1",
                    (cutoff,),
                )
            else:
                count_cursor = await database.execute(
                    "SELECT COUNT(*) FROM performance_events WHERE created_at>=? AND guild_id=?",
                    (cutoff, guild_id),
                )
                slowest_cursor = await database.execute(
                    "SELECT operation, duration_ms FROM performance_events WHERE created_at>=? AND guild_id=? ORDER BY duration_ms DESC LIMIT 1",
                    (cutoff, guild_id),
                )
            count = (await count_cursor.fetchone())[0]
            slowest = await slowest_cursor.fetchone()
        return {
            "count": count,
            "maximum_ms": slowest[1] if slowest else 0.0,
            "slowest": slowest[0] if slowest else "None",
        }

    def workers(self):
        definitions = (
            ("Ticket inactivity audit", "Inactivity", "check_inactivity"),
            ("Ticket escalation audit", "Escalations", "audit_escalations"),
            ("Diagnostic watchdog", "Diagnostics", "watch_workers"),
        )
        results = []
        for label, cog_name, attribute in definitions:
            cog = self.bot.get_cog(cog_name)
            worker = getattr(cog, attribute, None) if cog else None
            if worker is None:
                status = "Missing"
            elif worker.failed():
                status = "Failed"
            elif worker.is_running():
                status = "Running"
            else:
                status = "Stopped"
            results.append({"name": label, "status": status})
        return results

    def server_checks(self, guild_id=None):
        results = []
        channel_keys = (
            "TICKET_CATEGORY_ID",
            "TICKET_PANEL_CHANNEL_ID",
            "TICKET_ARCHIVE_CATEGORY_ID",
            "LOG_CHANNEL_ID",
        )
        permission_names = (
            "view_channel",
            "send_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
        )
        guild_permission_names = (
            "manage_channels",
            "manage_roles",
            "manage_guild",
            "moderate_members",
            "ban_members",
            "kick_members",
        )
        configured_guilds = (
            config.GUILDS.items()
            if guild_id is None
            else ((guild_id, config.GUILDS.get(guild_id)),)
        )
        for checked_guild_id, settings in configured_guilds:
            if settings is None:
                results.append(
                    {
                        "server": str(checked_guild_id),
                        "issues": ["Server setup is incomplete"],
                        "warnings": [],
                    }
                )
                continue
            guild = self.bot.get_guild(checked_guild_id)
            issues = []
            warnings = []
            if not settings.get("SETUP_COMPLETE"):
                issues.append("Server setup is incomplete")
            if guild is None:
                results.append(
                    {
                        "server": settings.get("NAME", str(checked_guild_id)),
                        "issues": ["Bot is not connected"],
                        "warnings": [],
                    }
                )
                continue
            for key in channel_keys:
                if guild.get_channel(settings[key]) is None:
                    issues.append(f"Missing {clean_label(key)}")
            role_ids = set(settings["OWNER_ROLES"]) | {
                settings["MOD_ROLE"],
                settings["TRIAL_MOD_ROLE"],
            }
            available_roles = [
                role_id for role_id in role_ids if guild.get_role(role_id) is not None
            ]
            missing_roles = sorted(
                role_id for role_id in role_ids if guild.get_role(role_id) is None
            )
            if not available_roles:
                issues.append("No configured staff role could be resolved")
            if missing_roles:
                warnings.append(
                    f"Stale staff role IDs: {', '.join(str(role_id) for role_id in missing_roles)}"
                )
            if guild.me:
                panel = guild.get_channel(settings["TICKET_PANEL_CHANNEL_ID"])
                permissions = (
                    panel.permissions_for(guild.me)
                    if panel
                    else guild.me.guild_permissions
                )
                for permission in permission_names:
                    if not getattr(permissions, permission, False):
                        issues.append(f"Missing {clean_label(permission)} permission")
                for permission in guild_permission_names:
                    if not getattr(guild.me.guild_permissions, permission, False):
                        issues.append(f"Missing {clean_label(permission)} permission")
            results.append(
                {"server": guild.name, "issues": issues, "warnings": warnings}
            )
        return results

    async def snapshot(self, guild_id=None):
        database = await self.database_health(guild_id)
        workers = self.workers()
        servers = self.server_checks(guild_id)
        slow = await self.slow_operations(guild_id)
        errors = await self.recent_errors(guild_id)
        issues = []
        warnings = []
        if not database["ok"]:
            issues.append(f"Database integrity: {database['integrity']}")
        for worker in workers:
            if worker["status"] != "Running":
                issues.append(f"{worker['name']}: {worker['status']}")
        for server in servers:
            issues.extend(f"{server['server']}: {item}" for item in server["issues"])
            warnings.extend(
                f"{server['server']}: {item}" for item in server["warnings"]
            )
        discord_latency = self.bot.latency * 1000
        if discord_latency >= 750:
            issues.append(f"Discord latency is high: {discord_latency:.1f} ms")
        return {
            "status": "Healthy" if not issues else "Attention Required",
            "issues": issues,
            "warnings": warnings,
            "database": database,
            "workers": workers,
            "servers": servers,
            "slow_operations": slow,
            "recent_errors": errors,
            "discord_latency_ms": discord_latency,
            "uptime": format_uptime(time.monotonic() - self.bot.started_at_monotonic),
        }

    @tasks.loop(minutes=1)
    async def watch_workers(self):
        for worker in self.workers():
            name = worker["name"]
            if name == "Diagnostic watchdog":
                continue
            if worker["status"] == "Running":
                self.reported_worker_failures.discard(name)
                continue
            if name not in self.reported_worker_failures:
                self.reported_worker_failures.add(name)
                log_exception(
                    "WORKER",
                    RuntimeError(f"{name} is {worker['status'].lower()}"),
                    context="Background worker watchdog",
                )

    @watch_workers.before_loop
    async def before_watch_workers(self):
        await self.bot.wait_until_ready()

    @watch_workers.error
    async def watch_workers_error(self, error):
        log_exception("WORKER", error, context="Diagnostic watchdog stopped")

    @commands.Cog.listener()
    async def on_ready(self):
        if self.ready_reported:
            return
        self.ready_reported = True
        report = await self.snapshot()
        emit(
            "SUCCESS" if report["status"] == "Healthy" else "WARNING",
            "HEALTH",
            f"Startup status: {report['status']} | issues={len(report['issues'])} | warnings={len(report['warnings'])}",
        )

    async def require_owner(self, interaction, message):
        if (
            interaction.guild is not None
            and interaction.guild.id in config.GUILDS
            and can_setup(interaction.user)
        ):
            return True
        await interaction.response.send_message(
            embed=error_embed(message), ephemeral=True
        )
        return False

    @app_commands.command(
        name="healthz", description="Show a concise operational health overview"
    )
    async def healthz(self, interaction: discord.Interaction):
        if not await self.require_owner(
            interaction, "You do not have permission to view system health."
        ):
            return
        await interaction.response.defer(ephemeral=True)
        report = await self.snapshot(interaction.guild.id)
        healthy = report["status"] == "Healthy"
        running = sum(
            1 for worker in report["workers"] if worker["status"] == "Running"
        )
        embed = discord.Embed(
            title=f"{config.BOT_NAME} Operations Status",
            description="All essential systems are being monitored in real time.",
            color=0x2ECC71 if healthy else 0xF0B232,
            timestamp=datetime.now(timezone),
        )
        embed.add_field(name="System", value=f"**{report['status']}**", inline=True)
        embed.add_field(
            name="Discord", value=f"{report['discord_latency_ms']:.1f} ms", inline=True
        )
        embed.add_field(
            name="Database",
            value=f"{report['database']['latency_ms']:.1f} ms",
            inline=True,
        )
        embed.add_field(name="Uptime", value=report["uptime"], inline=True)
        embed.add_field(
            name="Open Tickets",
            value=str(report["database"]["open_tickets"]),
            inline=True,
        )
        embed.add_field(
            name="Workers",
            value=f"{running}/{len(report['workers'])} running",
            inline=True,
        )
        embed.add_field(
            name="Errors in 24 Hours",
            value=str(report["database"]["errors_24h"]),
            inline=True,
        )
        embed.add_field(
            name="Slow Operations",
            value=str(report["slow_operations"]["count"]),
            inline=True,
        )
        embed.add_field(
            name="Configuration Warnings",
            value=str(len(report["warnings"])),
            inline=True,
        )
        latest = (
            report["recent_errors"][0]["reference"]
            if report["recent_errors"]
            else "None"
        )
        embed.add_field(name="Latest Error", value=latest, inline=True)
        embed.set_footer(text=f"{config.BOT_NAME} | Owner operations")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="debugz", description="Run a readable full-system diagnostic check"
    )
    async def debugz(self, interaction: discord.Interaction):
        if not await self.require_owner(
            interaction, "You do not have permission to run diagnostics."
        ):
            return
        await interaction.response.defer(ephemeral=True)
        report = await self.snapshot(interaction.guild.id)
        embed = discord.Embed(
            title=f"{config.BOT_NAME} Diagnostic Center",
            description=f"Result: **{report['status']}**\nDetected issues: **{len(report['issues'])}**",
            color=0x2ECC71 if not report["issues"] else 0xF0B232,
            timestamp=datetime.now(timezone),
        )
        issue_text = (
            "\n".join(
                f"{index}. {issue}" for index, issue in enumerate(report["issues"], 1)
            )
            or "No operational issues were detected."
        )
        embed.add_field(name="Action Required", value=issue_text[:1024], inline=False)
        warning_text = (
            "\n".join(
                f"{index}. {warning}"
                for index, warning in enumerate(report["warnings"], 1)
            )
            or "No configuration warnings were detected."
        )
        embed.add_field(
            name="Configuration Warnings", value=warning_text[:1024], inline=False
        )
        worker_text = "\n".join(
            f"{worker['name']}: **{worker['status']}**" for worker in report["workers"]
        )
        embed.add_field(
            name="Background Services", value=worker_text[:1024], inline=False
        )
        server_lines = []
        for server in report["servers"]:
            status = (
                "Ready" if not server["issues"] else f"{len(server['issues'])} issue(s)"
            )
            server_lines.append(f"{server['server']}: **{status}**")
        server_text = "\n".join(server_lines)
        embed.add_field(
            name="Server Configuration", value=server_text[:1024], inline=False
        )
        error_text = (
            "\n".join(
                f"`{error['reference']}` {error['category']} / {error['error_type']} | {error['occurrence_count']}x"
                for error in report["recent_errors"]
            )
            or "No errors recorded."
        )
        embed.add_field(
            name="Recent Error Groups", value=error_text[:1024], inline=False
        )
        slow = report["slow_operations"]
        embed.add_field(
            name="Performance",
            value=f"Slow events: {slow['count']}\nSlowest: {slow['slowest']}\nMaximum: {slow['maximum_ms']:.1f} ms",
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Use /debugerror for one reference")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="debugerror", description="Investigate one stored error reference"
    )
    @app_commands.describe(reference="Reference such as ERR-A31F9C20")
    async def debugerror(self, interaction: discord.Interaction, reference: str):
        if not await self.require_owner(
            interaction, "You do not have permission to inspect errors."
        ):
            return
        await interaction.response.defer(ephemeral=True)
        record = await self.find_error(reference, interaction.guild.id)
        if not record:
            await interaction.followup.send(
                embed=error_embed("No stored error matches that reference."),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title=f"Error {record['reference']}",
            description=f"**{record['category']} / {record['error_type']}**",
            color=0xED4245,
            timestamp=datetime.now(timezone),
        )
        embed.add_field(
            name="Message",
            value=redact(record["message"])[:1024] or "Unavailable",
            inline=False,
        )
        embed.add_field(
            name="Context",
            value=redact(record["context"])[:1024] or "No context provided",
            inline=False,
        )
        embed.add_field(
            name="Occurrences", value=str(record["occurrence_count"]), inline=True
        )
        embed.add_field(name="First Seen", value=record["first_seen"], inline=False)
        embed.add_field(name="Last Seen", value=record["last_seen"], inline=False)
        location = f"Guild: {record['guild_id'] or 'Unknown'}\nChannel: {record['channel_id'] or 'Unknown'}\nUser: {record['user_id'] or 'Unknown'}"
        embed.add_field(name="Location", value=location, inline=False)
        embed.set_footer(
            text=f"{config.BOT_NAME} | Fingerprint {record['fingerprint']}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="debugexport", description="Export a sanitized diagnostic report"
    )
    async def debugexport(self, interaction: discord.Interaction):
        if not await self.require_owner(
            interaction, "You do not have permission to export diagnostics."
        ):
            return
        await interaction.response.defer(ephemeral=True)
        started = time.perf_counter()
        report = await self.snapshot(interaction.guild.id)
        report["generated_at"] = datetime.now(timezone).isoformat()
        report["runtime"] = {
            "python": platform.python_version(),
            "discord_py": discord.__version__,
        }
        report["loaded_extensions"] = sorted(self.bot.extensions)
        report["error_details"] = await self.recent_errors(
            interaction.guild.id, limit=25, full=True
        )
        payload = redact(json.dumps(report, indent=2, ensure_ascii=False)).encode(
            "utf-8"
        )
        filename = (
            f"maja-diagnostics-{datetime.now(timezone).strftime('%Y%m%d-%H%M%S')}.json"
        )
        file = discord.File(io.BytesIO(payload), filename=filename)
        duration = log_performance(
            "diagnostics.export", started, threshold_ms=1000, guild=interaction.guild
        )
        embed = discord.Embed(
            title=f"{config.BOT_NAME} Diagnostic Package",
            description="A sanitized technical report is attached for owner review.",
            color=0x5865F2,
            timestamp=datetime.now(timezone),
        )
        embed.add_field(name="Status", value=report["status"], inline=True)
        embed.add_field(name="Issues", value=str(len(report["issues"])), inline=True)
        embed.add_field(name="Generated In", value=f"{duration:.1f} ms", inline=True)
        embed.set_footer(
            text=f"{config.BOT_NAME} | Credentials are automatically redacted"
        )
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Diagnostics(bot))
