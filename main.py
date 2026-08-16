import sys 
import asyncio
import os 
import traceback 
from datetime import datetime 

import discord 
import pytz 
from discord .ext import commands 

import config 

from utils .database import setup_database 
from utils .logger import (
format_user ,
log ,
log_command ,
log_interaction ,
send_report_to_owner ,
log_exception,
setup_logs,
)

sys .stdout .reconfigure (encoding ="utf-8")
sys .stderr .reconfigure (encoding ="utf-8")

timezone =pytz .timezone ("Europe/Berlin")

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

    for guild_connected in list (bot .guilds ):
        if (
        guild_connected .id ==1490348711182733495 
        or guild_connected .id not in config .GUILDS 
        ):
            log (
            "Ignoring events from unapproved server: "
            f"{guild_connected .name } ({guild_connected .id })"
            )
            log (
            "Developer Team has successfully closed the connection for: "
            f"{guild_connected .name } ({guild_connected .id })"
            )

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

    log ("Bot Setup completed and successfully connected!")
    log ("Bot Setup connected to DB")

    print ()
    print ("======================================")
    print ("! maja ! Ticket System v2")
    print ("======================================")
    print ("Status: ONLINE")
    print (
    f"Server: {guild .name if guild else 'Unknown'}"
    )
    print (f"Latency: {round (bot .latency *1000 )}ms")
    print ("--------------------------------------")
    print ("Listening for events:")
    print ("Ticket Creation")
    print ("Ticket Claiming")
    print ("Ticket Closing")
    print ("Ticket Reopening")
    print ("Ticket Deletion")
    print ("Transcript Generation")
    print ("======================================")
    print ()

    log ("Bot is now listening for reports...")
    log ("Debugging is starting to fire")


@bot .event 
async def on_interaction (
interaction :discord .Interaction ,
):
    if (
    interaction .guild_id ==1490348711182733495 
    or (
    interaction .guild_id 
    and interaction .guild_id not in config .GUILDS 
    )
    ):
        return 

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


@bot .event 
async def on_command_error (
ctx :commands .Context ,
error :commands .CommandError ,
):
    if isinstance (error ,commands .CommandNotFound ):
        return 

    original_error =getattr (
    error ,
    "original",
    error ,
    )

    reference = log_exception(
        "COMMAND",
        original_error,
        guild=ctx.guild,
        channel=ctx.channel,
        user=ctx.author,
        context=f"Prefix command {ctx.command}",
    )

    embed =discord .Embed (
    title ="Bot Command Error Alert",
    description =(
    "An error occurred while executing "
    f"`{ctx .command }`."
    ),
    color =discord .Color .red (),
    timestamp =datetime .now (timezone ),
    )

    embed .add_field (
    name ="User",
    value =(
    f"{ctx .author .mention } "
    f"(`{ctx .author .id }`)"
    ),
    inline =True ,
    )

    channel_value =(
    ctx .channel .mention 
    if hasattr (ctx .channel ,"mention")
    else "DM"
    )

    embed .add_field (
    name ="Channel",
    value =channel_value ,
    inline =True ,
    )

    embed .add_field (
    name ="Error",
    value =f"Reference: `{reference}`",
    inline =False ,
    )

    await send_report_to_owner (
    bot ,
    embed ,
    is_error =True ,
    )


@bot .tree .error 
async def on_app_command_error (
interaction :discord .Interaction ,
error :discord .app_commands .AppCommandError ,
):
    cmd_name =(
    interaction .command .name 
    if interaction .command 
    else "Unknown Slash Command"
    )

    original_error =getattr (
    error ,
    "original",
    error ,
    )

    reference = log_exception(
        "COMMAND",
        original_error,
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        context=f"Slash command /{cmd_name}",
    )

    embed =discord .Embed (
    title ="Bot Slash Command Error Alert",
    description =(
    "An error occurred while executing "
    f"`/{cmd_name }`."
    ),
    color =discord .Color .red (),
    timestamp =datetime .now (timezone ),
    )

    embed .add_field (
    name ="User",
    value =(
    f"{interaction .user .mention } "
    f"(`{interaction .user .id }`)"
    ),
    inline =True ,
    )

    if interaction .channel :
        channel_value =(
        interaction .channel .mention 
        if hasattr (interaction .channel ,"mention")
        else str (interaction .channel )
        )

        embed .add_field (
        name ="Channel",
        value =channel_value ,
        inline =True ,
        )

    embed .add_field (
    name ="Error",
    value =f"Reference: `{reference}`",
    inline =False ,
    )

    await send_report_to_owner (
    bot ,
    embed ,
    is_error =True ,
    )

    try :
        if interaction .response .is_done ():
            await interaction .followup .send (
            f"An internal error occurred. Reference: `{reference}`",
            ephemeral =True ,
            )
        else :
            await interaction .response .send_message (
            f"An internal error occurred. Reference: `{reference}`",
            ephemeral =True ,
            )
    except Exception :
        pass 


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
