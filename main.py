import sys 
import asyncio
import os 
import traceback 

import discord 
from discord .ext import commands 

import config 

from utils .database import setup_database 
from utils .logger import (
log ,
log_interaction ,
log_exception,
setup_logs,
)

sys .stdout .reconfigure (encoding ="utf-8")
sys .stderr .reconfigure (encoding ="utf-8")

intents =discord .Intents .default ()
intents .message_content =True 
intents .guilds =True 
intents .members =True 
intents .presences =True

bot =commands .Bot (
command_prefix ="!",
intents =intents ,
help_command =None ,
)


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
    message = f"The operation could not be completed. Error reference: `{reference}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
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
        await ctx.send(f"The command could not be completed. Error reference: `{reference}`")
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
    error = sys.exc_info()[1] or RuntimeError(f"Unknown event failure in {event_method}")
    log_exception("EVENT", error, context=f"Discord event: {event_method}")

extensions =[
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
]


@bot .event 
async def setup_hook ():
    setup_logs()
    loop = asyncio.get_running_loop()

    def handle_async_exception(active_loop, context):
        error = context.get("exception") or RuntimeError(context.get("message", "Unknown asynchronous error"))
        log_exception("BACKGROUND", error, context="Unhandled asynchronous task failure")

    loop.set_exception_handler(handle_async_exception)
    log ("Bot Setup starting...")

    await setup_database ()
    log ("Database loaded")
    log ("Loading extensions...")

    failed_extensions = []
    for extension in extensions :
        try :
            await bot .load_extension (extension )
            log (f"Loaded module: {extension }")
        except Exception as error :
            failed_extensions.append(extension)
            tb_str ="".join (
            traceback .format_exception (
            type (error ),
            error ,
            error .__traceback__ ,
            )
            )
            log (
            f"Failed loading {extension }: {error }\n{tb_str }"
            )

    if failed_extensions:
        raise RuntimeError(f"Required extensions failed to load: {', '.join(failed_extensions)}")

    log (
    "Syncing application slash commands to all configured servers..."
    )

    for gid in config .GUILDS .keys ():
        try :
            guild_obj =discord .Object (id =gid )
            bot .tree .copy_global_to (guild =guild_obj )
            synced =await bot .tree .sync (guild =guild_obj )
            log (
            f"Synced {len (synced )} slash command(s) to guild {gid }"
            )
        except Exception as error :
            tb_str ="".join (
            traceback .format_exception (
            type (error ),
            error ,
            error .__traceback__ ,
            )
            )
            log (
            f"Failed to sync slash commands to guild {gid }: "
            f"{error }\n{tb_str }"
            )

    try:
        global_synced =await bot.tree.sync()
        log(f"Synced {len(global_synced)} global slash command(s) for connected servers")
    except Exception as error:
        log_exception("APPLICATION", error, context="Global slash command synchronization failed")


@bot .event 
async def on_ready ():
    guild =bot .get_guild (config .GUILD_ID )

    log ("Bot First Fire Up - Catched")
    log ("Bot starting...")

    try :
        await bot .change_presence (
        activity =discord .Activity (
        type =discord .ActivityType .watching ,
        name ="Ticket Operations | ! maja !",
        )
        )
        log ("Activity status set to: Ticket Operations | ! maja !")
    except Exception as error :
        log (f"Failed to set status: {error }")

    for gid ,gcfg in config .GUILDS .items ():
        if gid ==1490348711182733495 :
            continue 

        current_guild =bot .get_guild (gid )

        if current_guild :
            log (
            f"Connected to Server: {current_guild .id } - "
            f"Name: {current_guild .name }",
            guild =current_guild ,
            )

            category =current_guild .get_channel (
            gcfg ["TICKET_CATEGORY_ID"]
            )
            log (
            f"Ticket category ({gcfg ['TICKET_CATEGORY_ID']}): "
            f"{'OK'if category else 'WARNING: Missing'}",
            guild =current_guild ,
            )

            archive_category =current_guild .get_channel (
            gcfg ["TICKET_ARCHIVE_CATEGORY_ID"]
            )
            log (
            f"Archive category "
            f"({gcfg ['TICKET_ARCHIVE_CATEGORY_ID']}): "
            f"{'OK'if archive_category else 'WARNING: Missing'}",
            guild =current_guild ,
            )

            panel =current_guild .get_channel (
            gcfg ["TICKET_PANEL_CHANNEL_ID"]
            )
            log (
            f"Panel channel ({gcfg ['TICKET_PANEL_CHANNEL_ID']}): "
            f"{'OK'if panel else 'WARNING: Missing'}",
            guild =current_guild ,
            )

            loaded =sum (
            1 
            for rid in gcfg ["OWNER_ROLES"]
            if current_guild .get_role (rid )
            )

            log (
            f"Owner roles loaded: "
            f"{loaded }/{len (gcfg ['OWNER_ROLES'])}",
            guild =current_guild ,
            )
        else :
            log (
            f"Server {gid } "
            f"({gcfg .get ('NAME','Unknown')}) not found",
            guild =gid ,
            )

    diagnostics =bot .get_cog ("Diagnostics")
    workers =diagnostics .workers ()if diagnostics else []
    running_workers =sum (1 for worker in workers if worker ["status"]=="Running")
    primary_server =guild .name if guild else "Unavailable"
    gateway_latency =round (bot .latency *1000 )

    console_width =66

    def console_row (label ,value ):
        safe_value =str (value )[:40 ]
        content =f"  {label :<22}{safe_value }"
        return f"║{content :<{console_width }}║"

    title =f"{config.BOT_NAME}  OPERATIONS CONTROL CENTER"
    print ()
    print (f"╔{'═'*console_width }╗")
    print (f"║{title :^{console_width }}║")
    print (f"╠{'═'*console_width }╣")
    print (console_row ("SYSTEM STATUS","ONLINE / READY"))
    print (console_row ("PRIMARY SERVER",primary_server ))
    print (console_row ("CONNECTED SERVERS",str (len (bot .guilds ))))
    print (console_row ("LOADED MODULES",f"{len (bot .extensions )}/{len (extensions )}"))
    print (console_row ("BACKGROUND WORKERS",f"{running_workers}/{len (workers )} RUNNING"))
    print (console_row ("GATEWAY LATENCY",f"{gateway_latency } ms"))
    print (f"╠{'─'*console_width }╣")
    print (console_row ("CORE SERVICES","TICKETS | TRANSCRIPTS | MODERATION"))
    print (console_row ("OPERATIONS","AVAILABILITY | ESCALATIONS | DIAGNOSTICS"))
    print (f"╚{'═'*console_width }╝")
    print ()

    log ("Operations console ready")


@bot .event 
async def on_interaction (
interaction :discord .Interaction ,
):
    custom_id =None 

    if interaction .data :
        custom_id =(
        interaction .data .get ("custom_id")
        or interaction .data .get ("name")
        )

    interaction_type =str (
    interaction .type 
    ).replace (
    "InteractionType.",
    "",
    )

    log_interaction (
    interaction .user ,
    custom_id or interaction_type ,
    interaction .channel ,
    details =f"Type: {interaction_type }",
    )


if __name__ =="__main__":
    if not config.TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing from the environment or .env file")
    os .makedirs (
    config .TRANSCRIPT_FOLDER ,
    exist_ok =True ,
    )

    os .makedirs (
    config .LOG_FOLDER ,
    exist_ok =True ,
    )

    bot .run (config .TOKEN )
