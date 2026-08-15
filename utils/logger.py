import os
from datetime import datetime

import discord
import pytz

import config

timezone = pytz.timezone("Europe/Berlin")


def get_time():
    return datetime.now(
        timezone
    ).strftime(
        "%d.%m.%Y %H:%M:%S"
    )


def setup_logs():
    os.makedirs(
        config.LOG_FOLDER,
        exist_ok=True,
    )


def _safe_filename(value):
    safe_name = "".join(
        character
        if character.isalnum()
        or character in (" ", "_", "-")
        else "_"
        for character in str(value)
    ).strip()

    return safe_name.replace(
        " ",
        "_",
    )


def log(message, guild=None):
    setup_logs()

    formatted = f"[{get_time()}] {message}"
    print(formatted)

    global_filename = os.path.join(
        config.LOG_FOLDER,
        "bot.log",
    )

    with open(
        global_filename,
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            formatted + "\n"
        )

    guild_id = None
    actual_name = None

    if isinstance(guild, int):
        guild_id = guild
    elif guild is not None and hasattr(
        guild,
        "id",
    ):
        guild_id = guild.id
        actual_name = getattr(
            guild,
            "name",
            None,
        )

    if not guild_id:
        return

    if (
        not actual_name
        and hasattr(config, "GUILDS")
        and guild_id in config.GUILDS
    ):
        actual_name = config.GUILDS[
            guild_id
        ].get(
            "NAME",
            str(guild_id),
        )

    if not actual_name:
        actual_name = str(guild_id)

    server_filename = os.path.join(
        config.LOG_FOLDER,
        f"{_safe_filename(actual_name)}.log",
    )

    with open(
        server_filename,
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            formatted + "\n"
        )


def format_user(user):
    if user is None:
        return "Unknown User"

    if isinstance(
        user,
        (discord.Member, discord.User),
    ):
        return (
            f"{user.name} -> {user.id}"
        )

    if isinstance(user, int):
        return f"User ID -> {user}"

    if (
        hasattr(user, "name")
        and hasattr(user, "id")
    ):
        return (
            f"{user.name} -> {user.id}"
        )

    return str(user)


def format_channel(channel):
    if channel is None:
        return "Unknown Channel"

    if (
        hasattr(channel, "name")
        and hasattr(channel, "id")
    ):
        return (
            f"#{channel.name} "
            f"(ID: {channel.id})"
        )

    return str(channel)


def _extract_guild(obj):
    if obj is not None and hasattr(
        obj,
        "guild",
    ):
        return obj.guild

    return None


def log_debug(
    category,
    message,
    guild=None,
):
    log(
        f"[DEBUG/{str(category).upper()}] "
        f"{message}",
        guild=guild,
    )


def log_dm(
    recipient,
    subject,
    success=True,
    error_detail=None,
):
    user_str = format_user(
        recipient
    )

    guild = _extract_guild(
        recipient
    )

    if success:
        log(
            f"[DEBUG/DM] DMed "
            f"{user_str} "
            f"(Subject: {subject})",
            guild=guild,
        )
        return

    error_text = (
        f" | Error: {error_detail}"
        if error_detail
        else ""
    )

    log(
        f"[DEBUG/DM] Failed to DM "
        f"{user_str} "
        f"(Subject: {subject})"
        f"{error_text}",
        guild=guild,
    )


def log_mod(
    action,
    moderator,
    target,
    reason=None,
    extra=None,
):
    mod_str = format_user(
        moderator
    )
    target_str = format_user(
        target
    )

    reason_str = (
        f" | Reason: {reason}"
        if reason
        else ""
    )

    extra_str = (
        f" | {extra}"
        if extra
        else ""
    )

    guild = (
        _extract_guild(moderator)
        or _extract_guild(target)
    )

    log(
        f"[DEBUG/MOD] "
        f"{action} {target_str} "
        f"by {mod_str}"
        f"{reason_str}"
        f"{extra_str}",
        guild=guild,
    )


def log_command(
    user,
    command_name,
    channel=None,
    details=None,
):
    user_str = format_user(
        user
    )

    channel_str = (
        f" in {format_channel(channel)}"
        if channel
        else ""
    )

    details_str = (
        f" | Details: {details}"
        if details
        else ""
    )

    guild = (
        _extract_guild(channel)
        or _extract_guild(user)
    )

    log(
        f"[DEBUG/COMMAND] "
        f"{user_str} executed command "
        f"'{command_name}'"
        f"{channel_str}"
        f"{details_str}",
        guild=guild,
    )


def log_ticket(
    action,
    channel,
    user=None,
    details=None,
):
    channel_str = format_channel(
        channel
    )

    user_str = (
        f" by {format_user(user)}"
        if user
        else ""
    )

    details_str = (
        f" | Details: {details}"
        if details
        else ""
    )

    guild = (
        _extract_guild(channel)
        or _extract_guild(user)
    )

    log(
        f"[DEBUG/TICKET] "
        f"{action} ticket "
        f"{channel_str}"
        f"{user_str}"
        f"{details_str}",
        guild=guild,
    )


def log_interaction(
    user,
    custom_id,
    channel=None,
    details=None,
):
    user_str = format_user(
        user
    )

    channel_str = (
        f" in {format_channel(channel)}"
        if channel
        else ""
    )

    details_str = (
        f" | Details: {details}"
        if details
        else ""
    )

    guild = (
        _extract_guild(channel)
        or _extract_guild(user)
    )

    log(
        f"[DEBUG/INTERACTION] "
        f"{user_str} triggered "
        f"'{custom_id}'"
        f"{channel_str}"
        f"{details_str}",
        guild=guild,
    )


def log_db(
    operation,
    table,
    details=None,
):
    details_str = (
        f" | {details}"
        if details
        else ""
    )

    log(
        f"[DEBUG/DB] "
        f"{operation} on "
        f"'{table}'"
        f"{details_str}"
    )


def log_filter(
    user,
    words,
    channel=None,
):
    user_str = format_user(
        user
    )

    channel_str = (
        f" in {format_channel(channel)}"
        if channel
        else ""
    )

    guild = (
        _extract_guild(channel)
        or _extract_guild(user)
    )

    log(
        f"[DEBUG/FILTER] "
        f"Flagged bad word(s) "
        f"{words} from "
        f"{user_str}"
        f"{channel_str}",
        guild=guild,
    )


def log_transcript(
    action,
    channel=None,
    details=None,
):
    channel_str = (
        f" for {format_channel(channel)}"
        if channel
        else ""
    )

    details_str = (
        f" | {details}"
        if details
        else ""
    )

    guild = _extract_guild(
        channel
    )

    log(
        f"[DEBUG/TRANSCRIPT] "
        f"{action}"
        f"{channel_str}"
        f"{details_str}",
        guild=guild,
    )


def log_inactivity(
    action,
    channel=None,
    user=None,
    details=None,
):
    channel_str = (
        f" {format_channel(channel)}"
        if channel
        else ""
    )

    user_str = (
        f" for {format_user(user)}"
        if user
        else ""
    )

    details_str = (
        f" | {details}"
        if details
        else ""
    )

    guild = (
        _extract_guild(channel)
        or _extract_guild(user)
    )

    log(
        f"[DEBUG/INACTIVITY] "
        f"{action}"
        f"{channel_str}"
        f"{user_str}"
        f"{details_str}",
        guild=guild,
    )


def log_perm(
    channel,
    target,
    permissions_summary,
):
    channel_str = format_channel(
        channel
    )
    target_str = format_user(
        target
    )

    guild = (
        _extract_guild(channel)
        or _extract_guild(target)
    )

    log(
        f"[DEBUG/PERM] "
        f"Configured permissions on "
        f"{channel_str} for "
        f"{target_str}: "
        f"{permissions_summary}",
        guild=guild,
    )


async def send_report_to_owner(
    bot,
    embed,
    file=None,
    is_error=False,
):
    if not bot or not is_error:
        return

    owner_id = getattr(
        config,
        "SETUP_USER_ID",
        None,
    )

    if not owner_id:
        log(
            "[DEBUG/ERROR] "
            "SETUP_USER_ID is not configured."
        )
        return

    try:
        owner = bot.get_user(
            owner_id
        )

        if owner is None:
            owner = await bot.fetch_user(
                owner_id
            )

        if owner is None:
            log(
                "[DEBUG/ERROR] "
                f"Could not resolve owner "
                f"with ID {owner_id}."
            )
            return

        if file:
            await owner.send(
                embed=embed,
                file=file,
            )
        else:
            await owner.send(
                embed=embed,
            )

        log_dm(
            owner,
            "ZER Ticket Error Embed to Owner",
            success=True,
        )

    except Exception as error:
        log_dm(
            owner_id,
            "ZER Ticket Error Embed to Owner",
            success=False,
            error_detail=str(error),
        )


def ticket_report(
    user,
    application,
    channel,
    bot=None,
):
    print()
    print("======================================")
    print("ZER Ticket Report")
    print(f"Time: {get_time()}")
    print("======================================")
    print("NEW TICKET INITIATED")
    print(
        f"Applicant: "
        f"{user.display_name} "
        f"({user.id})"
    )
    print(
        f"Application: "
        f"{application}"
    )
    print(
        f"Channel: "
        f"#{channel.name} "
        f"({channel.id})"
    )
    print("======================================")
    print()

    log_ticket(
        "Created",
        channel,
        user,
        details=(
            f"Application: {application}"
        ),
    )


def ticket_claim_report(
    channel,
    staff,
    owner_id,
    bot,
):
    print(
        f"TICKET CLAIMED: "
        f"#{channel.name} "
        f"claimed by "
        f"{staff.display_name}"
    )

    log_ticket(
        "Claimed",
        channel,
        staff,
        details=(
            f"Applicant ID: "
            f"{owner_id or 'Unknown'}"
        ),
    )


def ticket_close_report(
    channel,
    moderator,
    owner_id,
    reason,
    transcript_path,
    bot,
):
    print(
        f"TICKET CLOSED: "
        f"#{channel.name} "
        f"closed by "
        f"{moderator.display_name}. "
        f"Reason: {reason}"
    )

    log_ticket(
        "Closed",
        channel,
        moderator,
        details=(
            f"Reason: {reason} | "
            f"Applicant ID: "
            f"{owner_id or 'Unknown'}"
        ),
    )


def ticket_reopen_report(
    channel,
    moderator,
    owner_id,
    bot,
):
    print(
        f"TICKET REOPENED: "
        f"#{channel.name} "
        f"reopened by "
        f"{moderator.display_name}"
    )

    log_ticket(
        "Reopened",
        channel,
        moderator,
        details=(
            f"Applicant ID: "
            f"{owner_id or 'Unknown'}"
        ),
    )


def ticket_delete_report(
    channel_name,
    moderator,
    owner_id,
    bot,
):
    print(
        f"TICKET DELETED: "
        f"#{channel_name} "
        f"deleted by "
        f"{moderator.display_name}"
    )

    log_ticket(
        "Deleted",
        channel_name,
        moderator,
        details=(
            f"Applicant ID: "
            f"{owner_id or 'Unknown'}"
        ),
    )


def error_report(error):
    log(
        f"ERROR: {error}"
    )
