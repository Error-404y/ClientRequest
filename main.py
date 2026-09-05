import asyncio
import fcntl
import logging
import os
import sys

import discord
from discord.ext import commands

import config
from utils.database import setup_database
from utils.embeds import error as error_embed
from utils.logger import (
    emit,
    log_exception,
    log_interaction,
    setup_logs,
)

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.presences = True
intents.auto_moderation_configuration = True
intents.auto_moderation_execution = True

bot = commands.AutoShardedBot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)


def acquire_instance_lock():
    lock_path = config.BASE_DIR / ".maja.lock"
    lock_handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        lock_handle.close()
        raise RuntimeError(
            "Another ! maja ! bot instance is already running from this project folder."
        ) from error
    lock_handle.seek(0)
    lock_handle.truncate()
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()
    bot.instance_lock = lock_handle


@bot.tree.error
async def on_app_command_error(interaction, error):
    original = getattr(error, "original", error)
    reference = log_exception(
        "APPLICATION",
        original,
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        context=f"Slash command: {getattr(interaction.command, 'qualified_name', 'Unknown')}",
    )
    message = error_embed(
        f"The operation could not be completed. Error reference: `{reference}`"
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=message, ephemeral=True)
        else:
            await interaction.response.send_message(embed=message, ephemeral=True)
    except discord.HTTPException as response_error:
        log_exception(
            "APPLICATION",
            response_error,
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            context=f"Failed to deliver error reference {reference}",
        )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    original = getattr(error, "original", error)
    reference = log_exception(
        "COMMAND",
        original,
        guild=ctx.guild,
        channel=ctx.channel,
        user=ctx.author,
        context=f"Prefix command: {getattr(ctx.command, 'qualified_name', 'Unknown')}",
    )
    try:
        await ctx.send(
            embed=error_embed(
                f"The command could not be completed. Error reference: `{reference}`"
            )
        )
    except discord.HTTPException as response_error:
        log_exception(
            "COMMAND",
            response_error,
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            context=f"Failed to deliver error reference {reference}",
        )


@bot.event
async def on_error(event_method, *args, **kwargs):
    error = sys.exc_info()[1] or RuntimeError(
        f"Unknown event failure in {event_method}"
    )
    values = (*args, *kwargs.values())
    guild = next(
        (
            value if isinstance(value, discord.Guild) else getattr(value, "guild", None)
            for value in values
            if isinstance(value, discord.Guild)
            or getattr(value, "guild", None) is not None
        ),
        None,
    )
    channel = next(
        (
            getattr(value, "channel", None)
            for value in values
            if getattr(value, "channel", None) is not None
        ),
        None,
    )
    if channel is None:
        channel = next(
            (
                value
                for value in values
                if getattr(value, "guild", None) is not None
                and hasattr(value, "permissions_for")
            ),
            None,
        )
    user = next(
        (
            getattr(value, "author", None) or getattr(value, "user", None)
            for value in values
            if getattr(value, "author", None) or getattr(value, "user", None)
        ),
        None,
    )
    log_exception(
        "EVENT",
        error,
        guild=guild,
        channel=channel,
        user=user,
        context=f"Discord event: {event_method}",
    )


extensions = [
    "cogs.onboarding",
    "cogs.tickets",
    "cogs.transcript",
    "cogs.inactivity",
    "cogs.stats",
    "cogs.ban",
    "cogs.findz",
    "cogs.updates",
    "cogs.diagnostics",
    "cogs.availability",
    "cogs.escalations",
    "cogs.moderation",
    "cogs.afk",
    "cogs.automod",
    "cogs.governance",
]


@bot.event
async def setup_hook():
    setup_logs()
    loop = asyncio.get_running_loop()

    def handle_async_exception(active_loop, context):
        error = context.get("exception") or RuntimeError(
            context.get("message", "Unknown asynchronous error")
        )
        log_exception(
            "BACKGROUND", error, context="Unhandled asynchronous task failure"
        )

    loop.set_exception_handler(handle_async_exception)
    emit("INFO", "STARTUP", "Initializing core services")

    await setup_database()

    failed_extensions = []
    for extension in extensions:
        try:
            await bot.load_extension(extension)
        except Exception as error:
            failed_extensions.append(extension)
            log_exception(
                "STARTUP",
                error,
                context=f"Failed to load required extension: {extension}",
            )

    if failed_extensions:
        raise RuntimeError(
            f"Required extensions failed to load: {', '.join(failed_extensions)}"
        )

    emit(
        "SUCCESS",
        "STARTUP",
        f"Required modules loaded | count={len(bot.extensions)}/{len(extensions)}",
    )

    sync_error = None
    for attempt, delay in enumerate((0, 2, 5), 1):
        if delay:
            await asyncio.sleep(delay)
        try:
            global_synced = await bot.tree.sync()
            bot.synced_command_count = len(global_synced)
            bot.slash_command_count = sum(
                1
                for command in bot.tree.walk_commands()
                if not isinstance(command, discord.app_commands.Group)
            )
            emit(
                "SUCCESS",
                "STARTUP",
                f"Application commands synchronized | commands={bot.slash_command_count} | groups={bot.synced_command_count}",
            )
            sync_error = None
            break
        except Exception as error:
            sync_error = error
            log_exception(
                "APPLICATION",
                error,
                context=f"Global slash command synchronization attempt {attempt} failed",
            )
    if sync_error is not None:
        raise RuntimeError(
            "Global slash commands could not be synchronized after three attempts"
        ) from sync_error


@bot.event
async def on_ready():
    emit("SUCCESS", "SYSTEM", "Discord gateway connection established")

    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Ticket Operations | ! maja !",
            )
        )
    except Exception as error:
        log_exception("DISCORD", error, context="Failed to set bot activity status")

    if getattr(bot, "operations_console_ready", False):
        return
    bot.operations_console_ready = True

    diagnostics = bot.get_cog("Diagnostics")
    workers = diagnostics.workers() if diagnostics else []
    running_workers = sum(1 for worker in workers if worker["status"] == "Running")
    database = await diagnostics.database_health(deep=False) if diagnostics else None
    gateway_latency = round(bot.latency * 1000)
    connected_shards = len(getattr(bot, "shards", {})) or 1
    configured_servers = sum(
        1 for settings in config.GUILDS.values() if settings.get("SETUP_COMPLETE")
    )
    database_ready = bool(database and database["ok"])
    operational = (
        database_ready
        and running_workers == len(workers)
        and len(bot.extensions) == len(extensions)
        and getattr(bot, "slash_command_count", 0) > 0
    )

    console_width = 72

    def console_row(label, value):
        safe_value = str(value)[:45]
        content = f"  {label:<24}{safe_value}"
        return f"║{content:<{console_width}}║"

    title = f"{config.BOT_NAME}  OPERATIONS CONTROL CENTER"
    print()
    print(f"╔{'═' * console_width}╗")
    print(f"║{title:^{console_width}}║")
    print(f"╠{'═' * console_width}╣")
    print(console_row("SYSTEM STATUS", "READY" if operational else "ATTENTION"))
    print(console_row("CONNECTED SERVERS", f"{len(bot.guilds):,}"))
    print(console_row("CONFIGURED SERVERS", f"{configured_servers:,}"))
    print(console_row("CONNECTED SHARDS", f"{connected_shards:,}"))
    print(
        console_row(
            "SLASH COMMANDS", f"{getattr(bot, 'slash_command_count', 0):,} synchronized"
        )
    )
    print(console_row("LOADED MODULES", f"{len(bot.extensions)}/{len(extensions)}"))
    print(
        console_row("BACKGROUND WORKERS", f"{running_workers}/{len(workers)} RUNNING")
    )
    print(
        console_row(
            "DATABASE",
            "HEALTHY" if database_ready else "ATTENTION REQUIRED",
        )
    )
    print(
        console_row(
            "OPEN TICKETS",
            f"{database['open_tickets']:,}" if database else "Unavailable",
        )
    )
    print(
        console_row(
            "ERRORS / 24 HOURS",
            f"{database['errors_24h']:,}" if database else "Unavailable",
        )
    )
    print(console_row("GATEWAY LATENCY", f"{gateway_latency} ms"))
    print(f"╚{'═' * console_width}╝")
    print()

    emit(
        "SUCCESS" if operational else "WARNING",
        "HEALTH",
        f"Startup verification complete | status={'ready' if operational else 'attention'} | servers={len(bot.guilds)} | shards={connected_shards} | commands={getattr(bot, 'slash_command_count', 0)} | workers={running_workers}/{len(workers)}",
    )


@bot.event
async def on_interaction(
    interaction: discord.Interaction,
):
    if interaction.type in {
        discord.InteractionType.component,
        discord.InteractionType.modal_submit,
    }:
        return

    custom_id = None

    if interaction.data:
        custom_id = interaction.data.get("custom_id") or interaction.data.get("name")

    interaction_type = str(interaction.type).replace(
        "InteractionType.",
        "",
    )

    log_interaction(
        interaction.user,
        custom_id or interaction_type,
        interaction.channel,
        details=f"Type: {interaction_type}",
    )


if __name__ == "__main__":
    if not config.TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing from the environment or .env file")
    acquire_instance_lock()
    os.makedirs(
        config.TRANSCRIPT_FOLDER,
        exist_ok=True,
    )

    os.makedirs(
        config.LOG_FOLDER,
        exist_ok=True,
    )

    bot.run(config.TOKEN, log_level=logging.WARNING)
