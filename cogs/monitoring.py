import time
from datetime import datetime

import discord
import pytz
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.embeds import error as error_embed
from utils.logger import emit, log_exception
from utils.metrics import metric_count, metric_top, process_stats, record_event, set_gauge
from utils.monitoring import (
    collect_database_metrics,
    monitoring_history,
    persist_monitoring_sample,
)
from utils.permissions import is_bot_owner


def format_duration(seconds):
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


class Monitoring(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.started_at = time.monotonic()
        self.last_tick = time.monotonic()
        self.last_persist = 0.0
        self.last_console = 0.0
        self.last_health_state = None
        self.current_event_loop_lag_ms = 0.0
        self.latest_snapshot = None
        self.monitor_runtime.add_exception_type(Exception)
        self.monitor_runtime.start()

    def cog_unload(self):
        self.monitor_runtime.cancel()

    def workers(self):
        diagnostics = self.bot.get_cog("Diagnostics")
        return diagnostics.workers() if diagnostics else []

    async def collect_snapshot(self, guild_id=None):
        system = process_stats()
        database = await collect_database_metrics(guild_id)
        gateway_latency_ms = max(0.0, self.bot.latency * 1000)
        if guild_id is None:
            guild_count = len(self.bot.guilds)
            member_count = sum(guild.member_count or 0 for guild in self.bot.guilds)
        else:
            guild = self.bot.get_guild(int(guild_id))
            guild_count = 1 if guild else 0
            member_count = guild.member_count or 0 if guild else 0
        commands_1h = metric_count("command.slash", 3600, guild_id) + metric_count(
            "command.prefix", 3600, guild_id
        )
        interactions_1h = metric_count("interaction.total", 3600, guild_id)
        messages_1h = metric_count("message.total", 3600, guild_id)
        command_failures_1h = metric_count("command.failed", 3600, guild_id)
        top_commands = [
            {
                "name": name.removeprefix("command.name.").replace("_", " "),
                "count": int(value),
            }
            for name, value in metric_top(
                "command.name.", 3600, guild_id, limit=5
            )
        ]
        dm_failures_24h = metric_count("dm.failed", 86400, guild_id)
        dm_success_24h = metric_count("dm.success", 86400, guild_id)
        reconnects = int(metric_count("gateway.resumed"))
        workers = self.workers()
        running_workers = sum(
            1 for worker in workers if worker["status"] == "Running"
        )

        issues = []
        warnings = []
        if workers and running_workers != len(workers):
            issues.append(f"Background workers {running_workers}/{len(workers)} running")
        if gateway_latency_ms >= config.MONITOR_GATEWAY_CRITICAL_MS:
            issues.append(f"Discord latency {gateway_latency_ms:.0f} ms")
        elif gateway_latency_ms >= config.MONITOR_GATEWAY_WARNING_MS:
            warnings.append(f"Discord latency {gateway_latency_ms:.0f} ms")
        if database["db_latency_ms"] >= config.MONITOR_DB_CRITICAL_MS:
            issues.append(f"Database latency {database['db_latency_ms']:.1f} ms")
        elif database["db_latency_ms"] >= config.MONITOR_DB_WARNING_MS:
            warnings.append(f"Database latency {database['db_latency_ms']:.1f} ms")
        if self.current_event_loop_lag_ms >= config.MONITOR_EVENT_LOOP_CRITICAL_MS:
            issues.append(f"Event loop lag {self.current_event_loop_lag_ms:.1f} ms")
        elif self.current_event_loop_lag_ms >= config.MONITOR_EVENT_LOOP_WARNING_MS:
            warnings.append(f"Event loop lag {self.current_event_loop_lag_ms:.1f} ms")
        if system["cpu_percent"] >= config.MONITOR_CPU_WARNING_PERCENT:
            warnings.append(f"Process CPU {system['cpu_percent']:.1f}%")
        if system["memory_mb"] >= config.MONITOR_MEMORY_WARNING_MB:
            warnings.append(f"Process memory {system['memory_mb']:.1f} MB")
        if database["errors_1h"] >= config.MONITOR_ERROR_SPIKE_COUNT:
            warnings.append(f"{database['errors_1h']} errors recorded in the last hour")

        status = "Critical" if issues else "Degraded" if warnings else "Healthy"
        registry_missing = list(getattr(self.bot, "command_registry_missing", []))
        registry_extra = list(getattr(self.bot, "command_registry_extra", []))
        remote_command_count = getattr(self.bot, "remote_command_count", None)
        if registry_missing or registry_extra:
            warnings.append("Discord command registry does not match the local command tree")
            status = "Degraded" if not issues else "Critical"
        snapshot = {
            "status": status,
            "issues": issues,
            "warnings": warnings,
            "uptime": format_duration(time.monotonic() - self.started_at),
            "uptime_seconds": time.monotonic() - self.started_at,
            "gateway_latency_ms": gateway_latency_ms,
            "db_latency_ms": database["db_latency_ms"],
            "event_loop_lag_ms": self.current_event_loop_lag_ms,
            "process_cpu_percent": system["cpu_percent"],
            "process_memory_mb": system["memory_mb"],
            "load_1m": system["load_1m"],
            "load_5m": system["load_5m"],
            "guild_count": guild_count,
            "member_count": member_count,
            "open_tickets": database["open_tickets"],
            "tickets_created_24h": database["tickets_created_24h"],
            "tickets_closed_24h": database["tickets_closed_24h"],
            "errors_1h": database["errors_1h"],
            "errors_24h": database["errors_24h"],
            "unique_errors_1h": database["unique_errors_1h"],
            "unique_errors_24h": database["unique_errors_24h"],
            "latest_error": database["latest_error"],
            "slow_operations_24h": database["slow_operations_24h"],
            "slowest_ms_24h": database["slowest_ms_24h"],
            "commands_1h": int(commands_1h),
            "interactions_1h": int(interactions_1h),
            "messages_1h": int(messages_1h),
            "command_failures_1h": int(command_failures_1h),
            "top_commands": top_commands,
            "dm_failures_24h": int(dm_failures_24h),
            "dm_success_24h": int(dm_success_24h),
            "reconnects": reconnects,
            "workers_running": running_workers,
            "workers_total": len(workers),
            "remote_command_count": remote_command_count,
            "command_registry_missing": registry_missing,
            "command_registry_extra": registry_extra,
        }
        self.latest_snapshot = snapshot
        for name, value in (
            ("gateway.latency_ms", gateway_latency_ms),
            ("database.latency_ms", database["db_latency_ms"]),
            ("event_loop.lag_ms", self.current_event_loop_lag_ms),
            ("tickets.open", database["open_tickets"]),
            ("errors.1h", database["errors_1h"]),
        ):
            set_gauge(name, value)
        return snapshot

    async def maybe_emit_health_change(self, snapshot):
        status = snapshot["status"]
        if self.last_health_state is None:
            self.last_health_state = status
            return
        if status == self.last_health_state:
            return
        previous = self.last_health_state
        self.last_health_state = status
        detail = "; ".join(snapshot["issues"] + snapshot["warnings"]) or "all monitored signals recovered"
        emit(
            "SUCCESS" if status == "Healthy" else "WARNING",
            "HEALTH",
            f"Runtime health changed | {previous} -> {status} | {detail}",
        )
        record_event("health.state_change")

    def emit_console_snapshot(self, snapshot):
        emit(
            "INFO",
            "HEALTH",
            (
                f"Runtime snapshot | status={snapshot['status']} | "
                f"uptime={snapshot['uptime']} | gateway={snapshot['gateway_latency_ms']:.0f}ms | "
                f"db={snapshot['db_latency_ms']:.1f}ms | loop={snapshot['event_loop_lag_ms']:.1f}ms | "
                f"cpu={snapshot['process_cpu_percent']:.1f}% | ram={snapshot['process_memory_mb']:.1f}MB | "
                f"commands_1h={snapshot['commands_1h']} | errors_1h={snapshot['errors_1h']} | "
                f"workers={snapshot['workers_running']}/{snapshot['workers_total']}"
            ),
        )

    @tasks.loop(seconds=60)
    async def monitor_runtime(self):
        now = time.monotonic()
        elapsed = now - self.last_tick
        self.last_tick = now
        self.current_event_loop_lag_ms = max(0.0, (elapsed - 60.0) * 1000)
        try:
            snapshot = await self.collect_snapshot()
            await self.maybe_emit_health_change(snapshot)
            if now - self.last_persist >= config.MONITOR_PERSIST_MINUTES * 60:
                await persist_monitoring_sample(snapshot)
                self.last_persist = now
            if now - self.last_console >= config.MONITOR_CONSOLE_MINUTES * 60:
                self.emit_console_snapshot(snapshot)
                self.last_console = now
        except Exception as error:
            log_exception("MONITORING", error, context="Runtime monitoring sample failed")

    @monitor_runtime.before_loop
    async def before_monitor_runtime(self):
        await self.bot.wait_until_ready()
        self.last_tick = time.monotonic()

    @monitor_runtime.error
    async def monitor_runtime_error(self, error):
        log_exception("MONITORING", error, context="Runtime monitoring worker stopped")

    async def require_owner(self, interaction):
        if is_bot_owner(interaction.user):
            return True
        await interaction.response.send_message(
            embed=error_embed("You do not have permission to view live diagnostics."),
            ephemeral=True,
        )
        return False

    @app_commands.command(
        name="debuglive",
        description="Show live runtime, error, traffic, and performance statistics",
    )
    async def debuglive(self, interaction: discord.Interaction):
        if not await self.require_owner(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        snapshot = await self.collect_snapshot()
        history = await monitoring_history(24)
        color = (
            0x2ECC71
            if snapshot["status"] == "Healthy"
            else 0xF0B232
            if snapshot["status"] == "Degraded"
            else 0xED4245
        )
        embed = discord.Embed(
            title=f"{config.BOT_NAME} Live Operations",
            description=f"Runtime status: **{snapshot['status']}**",
            color=color,
            timestamp=datetime.now(pytz.utc),
        )
        embed.add_field(name="Uptime", value=snapshot["uptime"], inline=True)
        embed.add_field(
            name="Gateway",
            value=f"{snapshot['gateway_latency_ms']:.0f} ms",
            inline=True,
        )
        embed.add_field(
            name="Database",
            value=f"{snapshot['db_latency_ms']:.1f} ms",
            inline=True,
        )
        embed.add_field(
            name="Event Loop",
            value=f"{snapshot['event_loop_lag_ms']:.1f} ms lag",
            inline=True,
        )
        embed.add_field(
            name="Process",
            value=(
                f"CPU {snapshot['process_cpu_percent']:.1f}%\n"
                f"RAM {snapshot['process_memory_mb']:.1f} MB\n"
                f"Load {snapshot['load_1m']:.2f}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Discord Scope",
            value=(
                f"{snapshot['guild_count']} servers\n"
                f"{snapshot['member_count']:,} members\n"
                f"{snapshot['reconnects']} reconnects"
            ),
            inline=True,
        )
        embed.add_field(
            name="Traffic / 1h",
            value=(
                f"Commands: {snapshot['commands_1h']}\n"
                f"Interactions: {snapshot['interactions_1h']}\n"
                f"Messages: {snapshot['messages_1h']}\n"
                f"Command failures: {snapshot['command_failures_1h']}"
            ),
            inline=True,
        )
        top_command_text = (
            "\n".join(
                f"{item['name']}: {item['count']}"
                for item in snapshot["top_commands"]
            )
            or "No commands recorded in this runtime window."
        )
        embed.add_field(
            name="Top Commands / 1h",
            value=top_command_text[:1024],
            inline=True,
        )
        embed.add_field(
            name="Tickets / 24h",
            value=(
                f"Open: {snapshot['open_tickets']}\n"
                f"Created: {snapshot['tickets_created_24h']}\n"
                f"Closed: {snapshot['tickets_closed_24h']}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Errors",
            value=(
                f"1h: {snapshot['errors_1h']} ({snapshot['unique_errors_1h']} groups)\n"
                f"24h: {snapshot['errors_24h']} ({snapshot['unique_errors_24h']} groups)\n"
                f"Latest: {(snapshot['latest_error'] or {}).get('reference', 'None')}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Delivery / 24h",
            value=(
                f"DM success: {snapshot['dm_success_24h']}\n"
                f"DM failures: {snapshot['dm_failures_24h']}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Performance / 24h",
            value=(
                f"Slow operations: {snapshot['slow_operations_24h']}\n"
                f"Slowest: {snapshot['slowest_ms_24h']:.1f} ms"
            ),
            inline=True,
        )
        embed.add_field(
            name="Workers",
            value=f"{snapshot['workers_running']}/{snapshot['workers_total']} running",
            inline=True,
        )
        registry_value = (
            f"Verified ({snapshot['remote_command_count']} remote)"
            if snapshot["remote_command_count"] is not None
            and not snapshot["command_registry_missing"]
            and not snapshot["command_registry_extra"]
            else (
                f"Missing: {', '.join(snapshot['command_registry_missing']) or 'None'}\n"
                f"Extra: {', '.join(snapshot['command_registry_extra']) or 'None'}"
            )
            if snapshot["remote_command_count"] is not None
            else "Remote verification unavailable"
        )
        embed.add_field(
            name="Command Registry",
            value=registry_value[:1024],
            inline=False,
        )
        embed.add_field(
            name="24h Trends",
            value=(
                f"Gateway avg/max: {history['gateway_avg_ms']:.0f}/{history['gateway_max_ms']:.0f} ms\n"
                f"DB avg/max: {history['db_avg_ms']:.1f}/{history['db_max_ms']:.1f} ms\n"
                f"Loop avg/max: {history['event_loop_avg_ms']:.1f}/{history['event_loop_max_ms']:.1f} ms\n"
                f"CPU avg/max: {history['cpu_avg_percent']:.1f}/{history['cpu_max_percent']:.1f}%\n"
                f"RAM avg/max: {history['memory_avg_mb']:.1f}/{history['memory_max_mb']:.1f} MB"
            ),
            inline=False,
        )
        active = snapshot["issues"] + snapshot["warnings"]
        embed.add_field(
            name="Active Signals",
            value="\n".join(active)[:1024] if active else "No active warnings.",
            inline=False,
        )
        embed.set_footer(
            text=f"{config.BOT_NAME} | Live monitoring | {history['samples']} persisted samples"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Monitoring(bot))
