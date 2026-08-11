import os
import pytz
import discord

from datetime import datetime

import config


# ==========================================
# Logger Configuration
# ==========================================


timezone = pytz.timezone(
    "Europe/Berlin"
)


def get_time():
    return datetime.now(
        timezone
    ).strftime(
        "%d.%m.%Y %H:%M:%S"
    )


def setup_logs():
    if not os.path.exists(
        config.LOG_FOLDER
    ):
        os.makedirs(
            config.LOG_FOLDER
        )


def log(message, guild=None):
    setup_logs()
    formatted = f"[{get_time()}] {message}"
    print(formatted)

    # Always write to global bot.log
    filename = f"{config.LOG_FOLDER}/bot.log"
    with open(
        filename,
        "a",
        encoding="utf-8"
    ) as file:
        file.write(formatted + "\n")

    # If guild is provided or resolvable, write to specific server log file
    guild_id = None
    actual_name = None
    if isinstance(guild, int):
        guild_id = guild
    elif hasattr(guild, "id"):
        guild_id = guild.id
        if hasattr(guild, "name") and guild.name:
            actual_name = guild.name

    if guild_id and (guild_id in config.GUILDS or actual_name):
        if not actual_name and guild_id in config.GUILDS:
            actual_name = config.GUILDS[guild_id]["NAME"]
        
        # Sanitize server name for Windows filename
        safe_name = "".join(c if c.isalnum() or c in (" ", "_", "-") else "_" for c in actual_name).strip()
        safe_name = safe_name.replace(" ", "_")
        server_filename = f"{config.LOG_FOLDER}/{safe_name}.log"
        with open(server_filename, "a", encoding="utf-8") as file:
            file.write(formatted + "\n")


# ==========================================
# User & Entity Formatters for Debugging
# ==========================================

def format_user(user):
    if user is None:
        return "Unknown User"
    if isinstance(user, (discord.Member, discord.User)):
        return f"{user.name} -> {user.id}"
    if isinstance(user, int):
        return f"User ID -> {user}"
    if hasattr(user, "name") and hasattr(user, "id"):
        return f"{user.name} -> {user.id}"
    return str(user)


def format_channel(channel):
    if channel is None:
        return "Unknown Channel"
    if hasattr(channel, "name") and hasattr(channel, "id"):
        return f"#{channel.name} (ID: {channel.id})"
    return str(channel)


def _extract_guild(obj):
    if hasattr(obj, "guild"):
        return obj.guild
    return None


def log_debug(category, message, guild=None):
    log(f"[DEBUG/{category.upper()}] {message}", guild=guild)


def log_dm(recipient, subject, success=True, error_detail=None):
    user_str = format_user(recipient)
    guild = _extract_guild(recipient)
    if success:
        log(f"[DEBUG/DM] Dmed {user_str} (Subject: {subject})", guild=guild)
    else:
        err_str = f" | Error: {error_detail}" if error_detail else ""
        log(f"[DEBUG/DM] Failed to DM {user_str} (Subject: {subject}{err_str})", guild=guild)


def log_mod(action, moderator, target, reason=None, extra=None):
    mod_str = format_user(moderator)
    target_str = format_user(target)
    reason_str = f" | Reason: {reason}" if reason else ""
    extra_str = f" | {extra}" if extra else ""
    guild = _extract_guild(moderator) or _extract_guild(target)
    log(f"[DEBUG/MOD] {action} {target_str} by {mod_str}{reason_str}{extra_str}", guild=guild)


def log_command(user, command_name, channel=None, details=None):
    user_str = format_user(user)
    channel_str = f" in {format_channel(channel)}" if channel else ""
    details_str = f" | Details: {details}" if details else ""
    guild = _extract_guild(channel) or _extract_guild(user)
    log(f"[DEBUG/COMMAND] {user_str} executed command '{command_name}'{channel_str}{details_str}", guild=guild)


def log_ticket(action, channel, user=None, details=None):
    channel_str = format_channel(channel)
    user_str = f" by {format_user(user)}" if user else ""
    details_str = f" | Details: {details}" if details else ""
    guild = _extract_guild(channel) or _extract_guild(user)
    log(f"[DEBUG/TICKET] {action} ticket {channel_str}{user_str}{details_str}", guild=guild)


def log_interaction(user, custom_id, channel=None, details=None):
    user_str = format_user(user)
    channel_str = f" in {format_channel(channel)}" if channel else ""
    details_str = f" | Details: {details}" if details else ""
    guild = _extract_guild(channel) or _extract_guild(user)
    log(f"[DEBUG/INTERACTION] {user_str} triggered '{custom_id}'{channel_str}{details_str}", guild=guild)


def log_db(operation, table, details=None):
    details_str = f" | {details}" if details else ""
    log(f"[DEBUG/DB] {operation} on '{table}'{details_str}")


def log_filter(user, words, channel=None):
    user_str = format_user(user)
    channel_str = f" in {format_channel(channel)}" if channel else ""
    guild = _extract_guild(channel) or _extract_guild(user)
    log(f"[DEBUG/FILTER] Flagged bad word(s) {words} from {user_str}{channel_str}", guild=guild)


def log_transcript(action, channel=None, details=None):
    channel_str = f" for {format_channel(channel)}" if channel else ""
    details_str = f" | {details}" if details else ""
    guild = _extract_guild(channel)
    log(f"[DEBUG/TRANSCRIPT] {action}{channel_str}{details_str}", guild=guild)


def log_inactivity(action, channel=None, user=None, details=None):
    channel_str = f" {format_channel(channel)}" if channel else ""
    user_str = f" for {format_user(user)}" if user else ""
    details_str = f" | {details}" if details else ""
    guild = _extract_guild(channel) or _extract_guild(user)
    log(f"[DEBUG/INACTIVITY] {action}{channel_str}{user_str}{details_str}", guild=guild)


def log_perm(channel, target, permissions_summary):
    channel_str = format_channel(channel)
    target_str = format_user(target)
    guild = _extract_guild(channel) or _extract_guild(target)
    log(f"[DEBUG/PERM] Configured permissions on {channel_str} for {target_str}: {permissions_summary}", guild=guild)


# ==========================================
# Legacy Box Reports
# ==========================================

async def send_report_to_owner(bot, embed, file=None, is_error=False):
    if not bot or not is_error:
        return
    try:
        owner = bot.get_user(config.SETUP_USER_ID)
        if owner is None:
            owner = await bot.fetch_user(config.SETUP_USER_ID)
        if owner:
            if file:
                await owner.send(embed=embed, file=file)
            else:
                await owner.send(embed=embed)
            log_dm(owner, "ZER Ticket Error Embed to Owner", success=True)
    except Exception as e:
        log_dm(config.SETUP_USER_ID, "ZER Ticket Error Embed to Owner", success=False, error_detail=str(e))
        print(f"\033[91mFailed to send error DM to owner: {str(e)}\033[0m")



def ticket_report(
    user,
    application,
    channel,
    bot=None
):
    print()
    print("\033[96m══════════════════════════════════════\033[0m")
    print("           \033[93mZER Ticket Report\033[0m")
    print(f"  \033[90mTime:\033[0m {get_time()}")
    print("\033[96m══════════════════════════════════════\033[0m")
    print("  \033[92m[+] NEW TICKET INITIATED\033[0m")
    print(f"  \033[95mApplicant:\033[0m {user.display_name} ({user.id})")
    print(f"  \033[95mApplication:\033[0m {application}")
    print(f"  \033[95mChannel:\033[0m #{channel.name} ({channel.id})")
    print("\033[96m══════════════════════════════════════\033[0m")
    print()

    log_ticket("Created", channel, user, details=f"Application: {application}")

    if bot:
        embed = discord.Embed(
            title="Ticket Created",
            description="A new application ticket has been opened.",
            color=discord.Color.from_rgb(255, 172, 51),
            timestamp=datetime.now(timezone)
        )
        embed.add_field(name="Applicant", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Application Type", value=application, inline=True)
        embed.add_field(name="Channel", value=f"{channel.mention} (`#{channel.name}`)", inline=False)
        embed.set_footer(text="ZER Ticket Logging System")
        bot.loop.create_task(send_report_to_owner(bot, embed))


def ticket_claim_report(channel, staff, owner_id, bot):
    print(f"\033[96m[~] TICKET CLAIMED:\033[0m #{channel.name} claimed by {staff.display_name}")
    log_ticket("Claimed", channel, staff, details=f"Applicant ID: {owner_id or 'Unknown'}")

    if bot:
        applicant_mention = f"<@{owner_id}>" if owner_id else "Unknown"
        embed = discord.Embed(
            title="Ticket Claimed",
            description="A ticket has been claimed by a staff member.",
            color=discord.Color.from_rgb(0, 229, 255),
            timestamp=datetime.now(timezone)
        )
        embed.add_field(name="Ticket Channel", value=f"{channel.mention} (`#{channel.name}`)", inline=False)
        embed.add_field(name="Claimed By", value=f"{staff.mention} (`{staff.id}`)", inline=True)
        embed.add_field(name="Applicant", value=f"{applicant_mention} (`{owner_id or 'Unknown'}`)", inline=True)
        embed.set_footer(text="ZER Ticket Logging System")
        bot.loop.create_task(send_report_to_owner(bot, embed))


def ticket_close_report(channel, moderator, owner_id, reason, transcript_path, bot):
    print(f"\033[95m[-] TICKET CLOSED:\033[0m #{channel.name} closed by {moderator.display_name}. Reason: {reason}")
    log_ticket("Closed", channel, moderator, details=f"Reason: {reason} | Applicant ID: {owner_id or 'Unknown'}")

    if bot:
        applicant_mention = f"<@{owner_id}>" if owner_id else "Unknown"
        embed = discord.Embed(
            title="Ticket Closed",
            description="A ticket has been closed and archived.",
            color=discord.Color.from_rgb(224, 64, 251),
            timestamp=datetime.now(timezone)
        )
        embed.add_field(name="Ticket Channel", value=f"`#{channel.name}`", inline=False)
        embed.add_field(name="Closed By", value=f"{moderator.mention} (`{moderator.id}`)", inline=True)
        embed.add_field(name="Applicant", value=f"{applicant_mention} (`{owner_id or 'Unknown'}`)", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)

        file = None
        if transcript_path and os.path.exists(transcript_path):
            embed.add_field(name="Transcript", value="Attached to this message.", inline=False)
            file = discord.File(transcript_path)
        else:
            embed.add_field(name="Transcript", value="Not generated or failed.", inline=False)

        embed.set_footer(text="ZER Ticket Logging System")
        bot.loop.create_task(send_report_to_owner(bot, embed, file))


def ticket_reopen_report(channel, moderator, owner_id, bot):
    print(f"\033[92m[+] TICKET REOPENED:\033[0m #{channel.name} reopened by {moderator.display_name}")
    log_ticket("Reopened", channel, moderator, details=f"Applicant ID: {owner_id or 'Unknown'}")

    if bot:
        applicant_mention = f"<@{owner_id}>" if owner_id else "Unknown"
        embed = discord.Embed(
            title="Ticket Reopened",
            description="An archived ticket has been reopened.",
            color=discord.Color.from_rgb(0, 230, 118),
            timestamp=datetime.now(timezone)
        )
        embed.add_field(name="Ticket Channel", value=f"{channel.mention} (`#{channel.name}`)", inline=False)
        embed.add_field(name="Reopened By", value=f"{moderator.mention} (`{moderator.id}`)", inline=True)
        embed.add_field(name="Applicant", value=f"{applicant_mention} (`{owner_id or 'Unknown'}`)", inline=True)
        embed.set_footer(text="ZER Ticket Logging System")
        bot.loop.create_task(send_report_to_owner(bot, embed))


def ticket_delete_report(channel_name, moderator, owner_id, bot):
    print(f"\033[31m[x] TICKET DELETED:\033[0m #{channel_name} deleted by {moderator.display_name}")
    log_ticket("Deleted", channel_name, moderator, details=f"Applicant ID: {owner_id or 'Unknown'}")

    if bot:
        applicant_mention = f"<@{owner_id}>" if owner_id else "Unknown"
        embed = discord.Embed(
            title="Ticket Channel Deleted",
            description="A ticket channel has been permanently deleted.",
            color=discord.Color.from_rgb(192, 57, 43),
            timestamp=datetime.now(timezone)
        )
        embed.add_field(name="Ticket Name", value=f"`#{channel_name}`", inline=False)
        embed.add_field(name="Deleted By", value=f"{moderator.mention} (`{moderator.id}`)", inline=True)
        embed.add_field(name="Applicant", value=f"{applicant_mention} (`{owner_id or 'Unknown'}`)", inline=True)
        embed.set_footer(text="ZER Ticket Logging System")
        bot.loop.create_task(send_report_to_owner(bot, embed))


def error_report(error):
    log(f"ERROR: {error}")
