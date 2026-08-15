import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import discord
from discord.ext import commands

import asyncio
import os
import pytz

from datetime import datetime

import config

from utils.database import setup_database





timezone = pytz.timezone(
    "Europe/Berlin"
)


def log(message):

    time = datetime.now(
        timezone
    ).strftime(
        "%d.%m.%Y %H:%M:%S"
    )

    print(
        f"[{time}] {message}"
    )






intents = discord.Intents.default()

intents.message_content = True

intents.guilds = True

intents.members = True



bot = commands.Bot(

    command_prefix="!",

    intents=intents,

    help_command=None

)






extensions = [

    "cogs.tickets",

    "cogs.transcript",

    "cogs.inactivity",

    "cogs.stats",

    "cogs.ban"

    "cogs.findz"

]






@bot.event

async def setup_hook():


    log(
        "Bot Setup starting..."
    )


    await setup_database()


    log(
        "Database loaded"
    )


    log(
        "Loading extensions..."
    )



    for extension in extensions:


        try:


            await bot.load_extension(

                extension

            )


            log(

                f"Loaded module: {extension}"

            )


        except Exception as error:


            log(

                f"Failed loading {extension}: {error}"

            )

    log("Syncing application slash commands to all configured servers...")
    for gid in config.GUILDS.keys():
        try:
            guild_obj = discord.Object(id=gid)
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            log(f"Synced {len(synced)} slash command(s) to guild {gid}")
        except Exception as error:
            log(f"Failed to sync slash commands to guild {gid}: {error}")







@bot.event
async def on_ready():
    guild = bot.get_guild(config.GUILD_ID)

    log("Bot starting...")
    

    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="application tickets"
            )
        )
        log("Activity status set to: Watching application tickets")
    except Exception as e:
        log(f"Failed to set status: {str(e)}")


    for guild_connected in list(bot.guilds):
        if guild_connected.id == 1490348711182733495 or guild_connected.id not in config.GUILDS:
            log(f"Ignoring events from unapproved server: {guild_connected.name} ({guild_connected.id})")

    for gid, gcfg in config.GUILDS.items():
        if gid == 1490348711182733495:
            continue
        g = bot.get_guild(gid)
        if g:
            log(f"Connected to Server: {g.id} - Name: {g.name}", guild=g)
            category = g.get_channel(gcfg["TICKET_CATEGORY_ID"])
            log(f"  Ticket category ({gcfg['TICKET_CATEGORY_ID']}): {'OK' if category else 'WARNING: Missing'}", guild=g)
            archive_category = g.get_channel(gcfg["TICKET_ARCHIVE_CATEGORY_ID"])
            log(f"  Archive category ({gcfg['TICKET_ARCHIVE_CATEGORY_ID']}): {'OK' if archive_category else 'WARNING: Missing'}", guild=g)
            panel = g.get_channel(gcfg["TICKET_PANEL_CHANNEL_ID"])
            log(f"  Panel channel ({gcfg['TICKET_PANEL_CHANNEL_ID']}): {'OK' if panel else 'WARNING: Missing'}", guild=g)
            
            loaded = sum(1 for rid in gcfg["OWNER_ROLES"] if g.get_role(rid))
            log(f"  Owner roles loaded: {loaded}/{len(gcfg['OWNER_ROLES'])}", guild=g)
        else:
            log(f"Server {gid} ({gcfg['NAME']}) not found", guild=gid)

    log("Bot Setup completed and successfully connected!")


    print()
    print("\033[96m══════════════════════════════════════\033[0m")
    print("           \033[95m\033[1mZER Ticket Bot v2\033[0m")
    print("\033[96m══════════════════════════════════════\033[0m")
    print(f"  \033[90mStatus:\033[0m    \033[92m● ONLINE\033[0m")
    print(f"  \033[90mServer:\033[0m    \033[97m{guild.name if guild else 'Unknown'}\033[0m")
    print(f"  \033[90mLatency:\033[0m   \033[97m{round(bot.latency * 1000)}ms\033[0m")
    print("\033[96m──────────────────────────────────────\033[0m")
    print("  \033[93mListening for events:\033[0m")
    print("    \033[92m✔\033[0m Ticket Creation")
    print("    \033[92m✔\033[0m Ticket Claiming")
    print("    \033[92m✔\033[0m Ticket Closing")
    print("    \033[92m✔\033[0m Ticket Reopening")
    print("    \033[92m✔\033[0m Ticket Deletion")
    print("    \033[92m✔\033[0m Transcript Generation")
    print("\033[96m══════════════════════════════════════\033[0m")
    print()

    log("Bot is now listening for reports...")






from utils.logger import log, log_interaction, log_command

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.guild_id == 1490348711182733495 or (interaction.guild_id and interaction.guild_id not in config.GUILDS):
        return

    custom_id = None
    if interaction.data:
        custom_id = interaction.data.get("custom_id") or interaction.data.get("name")
    interaction_type = str(interaction.type).replace("InteractionType.", "")
    log_interaction(
        interaction.user,
        custom_id or interaction_type,
        interaction.channel,
        details=f"Type: {interaction_type}"
    )


import traceback

# ==========================================
# Error Handling
# ==========================================


@bot.event
async def on_command_error(
    ctx,
    error
):
    if isinstance(error, commands.CommandNotFound):
        return

    tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    log(
        f"[DEBUG/ERROR] Prefix Command Error in '{ctx.command}': {error} (User: {format_user(ctx.author)})\n{tb_str}"
    )
    embed = discord.Embed(
        title="⚠️ Bot Command Error Alert",
        description=f"An error occurred while executing command `{ctx.command}`.",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone)
    )
    embed.add_field(name="User", value=f"{ctx.author.mention} (`{ctx.author.id}`)", inline=True)
    embed.add_field(name="Channel", value=f"{ctx.channel.mention if hasattr(ctx.channel, 'mention') else 'DM'}", inline=True)
    embed.add_field(name="Error", value=f"```{str(error)[:1000]}```", inline=False)
    bot.loop.create_task(send_report_to_owner(bot, embed, is_error=True))


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: discord.app_commands.AppCommandError
):
    cmd_name = interaction.command.name if interaction.command else "Unknown Slash Command"
    tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    log(
        f"[DEBUG/ERROR] Slash Command Error in '/{cmd_name}': {error} (User: {format_user(interaction.user)})\n{tb_str}"
    )
    embed = discord.Embed(
        title="⚠️ Bot Slash Command Error Alert",
        description=f"An error occurred while executing slash command `/{cmd_name}`.",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone)
    )
    embed.add_field(name="User", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=True)
    embed.add_field(name="Error", value=f"```{str(error)[:1000]}```", inline=False)
    bot.loop.create_task(send_report_to_owner(bot, embed, is_error=True))





if __name__ == "__main__":


    if not os.path.exists(

        config.TRANSCRIPT_FOLDER

    ):

        os.makedirs(

            config.TRANSCRIPT_FOLDER

        )



    if not os.path.exists(

        config.LOG_FOLDER

    ):

        os.makedirs(

            config.LOG_FOLDER

        )



    bot.run(

        config.TOKEN

    )
