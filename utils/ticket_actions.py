import os
from datetime import datetime

import discord
import pytz

import config
from cogs.transcript import create_transcript
from utils.database import (
    close_ticket,
    get_ticket_owner,
    reopen_ticket,
    set_ticket_control_message,
)
from utils.embeds import error as error_embed
from utils.embeds import ticket_closed, ticket_closed_dm
from utils.logger import (
    log_dm,
    log_exception,
    log_perm,
    log_ticket,
    log_transcript,
)
from views.closed_buttons import ClosedTicketButtons

timezone = pytz.timezone(config.TIMEZONE)


async def resolve_ticket_member(channel):
    user_id = await get_ticket_owner(channel.id)
    if not user_id:
        return None, None
    member = channel.guild.get_member(user_id)
    if member is None:
        try:
            member = await channel.guild.fetch_member(user_id)
        except discord.NotFound:
            member = None
        except discord.HTTPException as error:
            log_exception(
                "DISCORD",
                error,
                guild=channel.guild,
                channel=channel,
                user=user_id,
                context="Failed to fetch ticket owner during close",
            )
    return user_id, member


async def create_close_transcript(channel):
    try:
        path = await create_transcript(channel)
        if path and os.path.exists(path) and os.path.getsize(path) > 8_000_000:
            log_transcript(
                "Standard transcript exceeded local size limit",
                channel,
                details="Generating lightweight transcript",
            )
            path = await create_transcript(channel, lightweight=True)
        return path if path and os.path.exists(path) else None
    except Exception as error:
        log_exception(
            "TRANSCRIPT",
            error,
            guild=channel.guild,
            channel=channel,
            context="Automatic close transcript generation failed",
        )
        return None


async def close_ticket_channel(channel, moderator, reason, bot):
    log_ticket("Closing Initiated", channel, moderator, details=f"Reason: {reason}")
    original_category = channel.category
    closed_at = datetime.now(timezone).isoformat()
    if not await close_ticket(channel.id, closed_at, moderator.id, reason):
        return False

    try:
        user_id, member = await resolve_ticket_member(channel)
    except Exception as error:
        await reopen_ticket(channel.id)
        raise RuntimeError("Failed to resolve the ticket owner") from error

    transcript_path = await create_close_transcript(channel)
    created_messages = []

    async def rollback_close():
        try:
            await reopen_ticket(channel.id)
        except Exception as error:
            log_exception(
                "DATABASE",
                error,
                guild=channel.guild,
                channel=channel,
                user=moderator,
                context="Failed to restore ticket database state after close failure",
            )
        for message in reversed(created_messages):
            try:
                await message.delete()
            except discord.HTTPException as error:
                log_exception(
                    "TICKET",
                    error,
                    guild=channel.guild,
                    channel=channel,
                    user=moderator,
                    context="Failed to remove incomplete close message",
                )
        if channel.category != original_category:
            try:
                await channel.edit(category=original_category)
            except discord.HTTPException as error:
                log_exception(
                    "TICKET",
                    error,
                    guild=channel.guild,
                    channel=channel,
                    user=moderator,
                    context="Failed to restore ticket category after close failure",
                )
        if member:
            try:
                await channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
            except discord.HTTPException as error:
                log_exception(
                    "PERMISSION",
                    error,
                    guild=channel.guild,
                    channel=channel,
                    user=member,
                    context="Failed to restore ticket owner permissions after close failure",
                )

    try:
        if member:
            await channel.set_permissions(
                member,
                view_channel=False,
                send_messages=False,
            )
            log_perm(channel, member, "Removed view_channel and send_messages")

        archive_category_id = config.get_archive_category_id(channel.guild.id)
        archive_category = channel.guild.get_channel(archive_category_id)
        if archive_category:
            await channel.edit(category=archive_category)
            log_ticket(
                "Archived Channel",
                channel,
                moderator,
                details="Moved to the configured archive category",
            )
        else:
            log_ticket(
                "Archive Category Missing",
                channel,
                moderator,
                details=f"Category ID: {archive_category_id}",
            )

        audit_file = discord.File(transcript_path) if transcript_path else None
        audit_message = await channel.send(
            embed=ticket_closed(moderator, reason, applicant=member),
            file=audit_file,
        )
        created_messages.append(audit_message)

        controls_embed = discord.Embed(
            title="Archived Ticket Controls",
            description="Authorized staff can reopen this ticket, generate another transcript, or permanently delete the channel.",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        controls_embed.set_footer(text=f"{config.BOT_NAME} | Archived Ticket Controls")
        control_message = await channel.send(
            embed=controls_embed, view=ClosedTicketButtons()
        )
        created_messages.append(control_message)
        await set_ticket_control_message(channel.id, control_message.id)
    except Exception as error:
        await rollback_close()
        raise RuntimeError(
            "Ticket close preparation failed and was rolled back"
        ) from error

    dm_success = True
    if member:
        try:
            dm_embed = ticket_closed_dm(
                channel.guild,
                channel,
                moderator,
                reason,
                transcript_path is not None,
                getattr(bot, "user", None),
            )
            if transcript_path:
                try:
                    await member.send(
                        embed=dm_embed, file=discord.File(transcript_path)
                    )
                except discord.HTTPException as error:
                    if error.status != 413 and error.code != 40005:
                        raise
                    log_transcript(
                        "Standard transcript exceeded Discord upload limit",
                        channel,
                        details="Retrying with lightweight transcript",
                    )
                    lightweight_path = await create_transcript(
                        channel, lightweight=True
                    )
                    if lightweight_path and os.path.exists(lightweight_path):
                        await member.send(
                            embed=ticket_closed_dm(
                                channel.guild,
                                channel,
                                moderator,
                                reason,
                                True,
                                getattr(bot, "user", None),
                            ),
                            file=discord.File(lightweight_path),
                        )
                    else:
                        await member.send(
                            embed=ticket_closed_dm(
                                channel.guild,
                                channel,
                                moderator,
                                reason,
                                False,
                                getattr(bot, "user", None),
                            )
                        )
            else:
                await member.send(embed=dm_embed)
            log_dm(member, f"Ticket #{channel.name} Close Notice", success=True)
        except discord.Forbidden:
            dm_success = False
            log_dm(
                member,
                f"Ticket #{channel.name} Close Notice",
                success=False,
                error_detail="Direct Messages Disabled",
            )
        except Exception as error:
            dm_success = False
            log_dm(
                member,
                f"Ticket #{channel.name} Close Notice",
                success=False,
                error_detail=str(error),
            )
            log_exception(
                "DM",
                error,
                guild=channel.guild,
                channel=channel,
                user=member,
                context="Failed to deliver ticket close notice",
            )

    if not dm_success and member:
        try:
            await channel.send(
                embed=error_embed(
                    f"The close notice could not be delivered to {member.mention}. The offline transcript remains available above for staff review."
                )
            )
        except discord.HTTPException as error:
            log_exception(
                "TICKET",
                error,
                guild=channel.guild,
                channel=channel,
                user=moderator,
                context="Failed to publish applicant DM warning",
            )

    try:
        from utils.logger import ticket_close_report

        ticket_close_report(channel, moderator, user_id, reason, transcript_path, bot)
    except Exception as error:
        log_exception(
            "TICKET",
            error,
            guild=channel.guild,
            channel=channel,
            user=moderator,
            context="Failed to record ticket close report",
        )
    return True
