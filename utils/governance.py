from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord

import config
from utils.database import (
    add_infraction,
    complete_approval_request,
    create_approval_request,
    fail_pending_approval_request,
    get_approval_rule,
    remove_infraction_by_uuid,
    remove_user_warning,
    set_approval_message,
)
from utils.logger import log_dm, log_exception, log_mod
from utils.permissions import (
    can_ban,
    can_kick,
    can_moderate_target,
    can_warn_or_view_history,
    is_staff,
)

APPROVAL_ACTIONS = (
    "WARN",
    "TIMEOUT",
    "KICK",
    "BAN",
    "UNBAN",
    "WARNING_REMOVE",
    "INFRACTION_REMOVE",
)

ACTION_LABELS = {
    "WARN": "Warning",
    "TIMEOUT": "Timeout",
    "KICK": "Kick",
    "BAN": "Ban",
    "UNBAN": "Unban",
    "WARNING_REMOVE": "Warning Removal",
    "INFRACTION_REMOVE": "Infraction Removal",
}

RISK_WEIGHTS = {
    "BAD_WORD": 0.5,
    "WARN": 2.0,
    "TIMEOUT": 4.0,
    "KICK": 6.0,
    "BAN": 10.0,
}


def parse_ticket_questions(values, required_count):
    cleaned = [" ".join(str(value).split()) for value in values if value]
    if not cleaned:
        raise ValueError("At least one question is required")
    if len(cleaned) > 5:
        raise ValueError("Discord ticket forms support a maximum of five questions")
    lowered = [value.casefold() for value in cleaned]
    if len(lowered) != len(set(lowered)):
        raise ValueError("Ticket form questions must be unique")
    required = max(0, min(int(required_count), len(cleaned)))
    return [
        {"label": label[:45], "required": index < required}
        for index, label in enumerate(cleaned)
    ]


def calculate_risk(records, now=None):
    now_value = now or datetime.now(timezone.utc)
    score = 0.0
    counts = {}
    for record in records:
        action = str(record.get("action_type") or "").upper()
        weight = RISK_WEIGHTS.get(action, 0.0)
        if not weight:
            continue
        counts[action] = counts.get(action, 0) + 1
        timestamp = str(record.get("timestamp") or "")
        try:
            occurred = datetime.strptime(timestamp, "%d/%m/%Y - %H:%M").replace(
                tzinfo=ZoneInfo(config.TIMEZONE)
            )
            occurred = occurred.astimezone(timezone.utc)
            age = max(0, (now_value - occurred).days)
        except ValueError:
            age = 0
        if age <= 7:
            multiplier = 1.5
        elif age <= 30:
            multiplier = 1.0
        elif age <= 90:
            multiplier = 0.6
        else:
            multiplier = 0.25
        score += weight * multiplier
    if score >= 21:
        level = 5
    elif score >= 13:
        level = 4
    elif score >= 7:
        level = 3
    elif score >= 3:
        level = 2
    else:
        level = 1
    return {"level": level, "score": round(score, 1), "counts": counts}


def can_approve(member, rule):
    if member is None or member.guild is None:
        return False
    if member.id == member.guild.owner_id:
        return True
    role_id = int(rule.get("approver_role_id") or 0)
    return bool(role_id and any(role.id == role_id for role in member.roles))


def approval_bypassed(member, rule):
    if member.id == member.guild.owner_id:
        return True
    return bool(rule.get("senior_bypass") and can_approve(member, rule))


def approval_request_embed(request, rule, requester, target_name, reason):
    action = request["action_type"]
    embed = discord.Embed(
        title="Senior Approval Required",
        description="A moderation action is awaiting independent review.",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Action", value=ACTION_LABELS[action], inline=True)
    embed.add_field(
        name="Requester",
        value=f"{requester.mention}\n`{requester.id}`",
        inline=True,
    )
    embed.add_field(
        name="Target",
        value=f"{target_name}\n`{request['target_id']}`",
        inline=True,
    )
    embed.add_field(name="Reason", value=reason[:1024], inline=False)
    embed.add_field(
        name="Approval Requirement",
        value=f"{rule['required_approvals']} independent approval(s)",
        inline=True,
    )
    embed.add_field(
        name="Expires",
        value=f"<t:{int(datetime.fromisoformat(request['expires_at']).timestamp())}:R>",
        inline=True,
    )
    embed.add_field(
        name="Request UUID", value=f"`{request['request_uuid']}`", inline=False
    )
    embed.set_footer(text=f"{config.BOT_NAME} | Moderation Governance")
    return embed


def approval_queued_embed(request):
    embed = discord.Embed(
        title="Senior Approval Requested",
        description=(
            "The moderation action has not been executed. It is waiting for "
            "independent review."
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Action", value=ACTION_LABELS[request["action_type"]], inline=True
    )
    embed.add_field(
        name="Request UUID", value=f"`{request['request_uuid']}`", inline=False
    )
    embed.add_field(
        name="Expires",
        value=f"<t:{int(datetime.fromisoformat(request['expires_at']).timestamp())}:R>",
        inline=True,
    )
    embed.set_footer(text=f"{config.BOT_NAME} | Moderation Governance")
    return embed


def approval_view(request_uuid):
    view = discord.ui.View(timeout=300)
    view.add_item(
        discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"approval:approve:{request_uuid}",
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Request Details",
            style=discord.ButtonStyle.secondary,
            custom_id=f"approval:details:{request_uuid}",
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Deny",
            style=discord.ButtonStyle.danger,
            custom_id=f"approval:deny:{request_uuid}",
        )
    )
    return view


def appeal_view(guild_id, infraction_uuid):
    view = discord.ui.View(timeout=300)
    view.add_item(
        discord.ui.Button(
            label="Submit Private Appeal",
            style=discord.ButtonStyle.secondary,
            custom_id=f"appeal:start:{int(guild_id)}:{infraction_uuid}",
        )
    )
    return view


async def queue_moderation_approval(
    bot,
    guild,
    requester,
    action_type,
    target_id,
    target_name,
    reason,
    payload=None,
):
    action = str(action_type).strip().upper()
    rule = await get_approval_rule(guild.id, action)
    if not rule or not rule["enabled"] or approval_bypassed(requester, rule):
        return None
    channel = guild.get_channel(rule["request_channel_id"])
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError(
            "The configured senior-approval channel no longer exists. The server "
            "owner must update `/approvalz configure`."
        )
    bot_member = guild.me
    if bot_member is None:
        raise RuntimeError(
            "The bot member could not be resolved in this server. Restart the bot and try again."
        )
    permissions = channel.permissions_for(bot_member)
    if not permissions.view_channel or not permissions.send_messages:
        raise RuntimeError(
            "The bot cannot access the configured senior-approval channel."
        )
    created_at = datetime.now(timezone.utc).isoformat()
    request = await create_approval_request(
        guild.id,
        action,
        requester.id,
        int(target_id),
        target_name,
        reason or "No reason provided",
        payload or {},
        rule["required_approvals"],
        channel.id,
        rule["expiry_minutes"],
        created_at,
    )
    request.update({"action_type": action, "target_id": int(target_id)})
    try:
        message = await channel.send(
            embed=approval_request_embed(
                request, rule, requester, target_name, reason or "No reason provided"
            ),
            view=approval_view(request["request_uuid"]),
        )
    except discord.HTTPException as error:
        await fail_pending_approval_request(
            request["request_uuid"], guild.id, "Approval message delivery failed"
        )
        raise RuntimeError(
            "The approval request could not be delivered to the configured review channel."
        ) from error
    await set_approval_message(request["request_uuid"], guild.id, message.id)
    return request


async def _resolve_request_members(bot, request):
    guild = bot.get_guild(int(request["guild_id"]))
    if guild is None:
        raise RuntimeError("The bot is no longer connected to this server")
    requester = guild.get_member(int(request["requester_id"]))
    if requester is None:
        try:
            requester = await guild.fetch_member(int(request["requester_id"]))
        except discord.HTTPException as error:
            raise RuntimeError(
                "The requesting moderator is no longer available"
            ) from error
    target = guild.get_member(int(request["target_id"]))
    return guild, requester, target


async def _send_action_dm(target, guild, title, reason, reference=None):
    if target is None:
        return False
    embed = discord.Embed(
        title=title,
        description=f"A moderation action was completed in **{guild.name}**.",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Reason", value=reason[:1024], inline=False)
    if reference:
        embed.add_field(name="Reference", value=f"`{reference}`", inline=False)
    embed.set_footer(text=f"{config.BOT_NAME} | Moderation Notice")
    try:
        await target.send(
            embed=embed,
            view=appeal_view(guild.id, reference) if reference else None,
        )
        log_dm(target, title, success=True)
        return True
    except discord.Forbidden:
        log_dm(target, title, success=False, error_detail="Direct Messages Disabled")
    except discord.HTTPException as error:
        log_dm(target, title, success=False, error_detail=str(error))
        log_exception("DM", error, guild=guild, user=target, context=title)
    return False


async def execute_approved_action(bot, request):
    guild, requester, target = await _resolve_request_members(bot, request)
    action = request["action_type"]
    payload = request["payload"]
    reason = request["reason"] or "No reason provided"
    target_id = int(request["target_id"])
    result_uuid = None
    if not is_staff(requester):
        raise RuntimeError("The requesting moderator no longer has staff access")
    if action in {"WARN", "TIMEOUT", "KICK"}:
        if target is None or not can_moderate_target(requester, target):
            raise RuntimeError("The target is unavailable or no longer moderatable")
    elif action == "BAN":
        if not can_ban(requester):
            raise RuntimeError("The requester no longer has ban permission")
        if target is not None and not can_moderate_target(requester, target):
            raise RuntimeError("The target is no longer moderatable")
    elif action == "UNBAN" and not can_ban(requester):
        raise RuntimeError("The requester no longer has unban permission")
    elif action in {"WARNING_REMOVE", "INFRACTION_REMOVE"}:
        if not can_warn_or_view_history(requester):
            raise RuntimeError("The requester no longer has infraction permission")
    if action == "WARN":
        result_uuid = await add_infraction(
            target_id, requester.id, "WARN", reason, guild.id
        )
        await _send_action_dm(target, guild, "Warning Notice", reason, result_uuid)
    elif action == "TIMEOUT":
        if guild.me is None or target.top_role >= guild.me.top_role:
            raise RuntimeError("The bot role is not above the target member")
        seconds = int(payload["duration_seconds"])
        if seconds < 1 or seconds > 2_419_200:
            raise RuntimeError("The stored timeout duration is invalid")
        await target.timeout(
            timedelta(seconds=seconds),
            reason=f"Approved request {request['request_uuid']} | {reason}",
        )
        try:
            result_uuid = await add_infraction(
                target_id,
                requester.id,
                "TIMEOUT",
                f"{reason} | Duration: {payload['duration_text']}",
                guild.id,
            )
        except Exception:
            await target.timeout(None, reason="Approval execution record failed")
            raise
        await _send_action_dm(target, guild, "Timeout Notice", reason, result_uuid)
    elif action == "KICK":
        if not can_kick(requester):
            raise RuntimeError("The requester no longer has kick permission")
        result_uuid = await add_infraction(
            target_id, requester.id, "KICK", reason, guild.id
        )
        try:
            await _send_action_dm(
                target, guild, "Kick Notification", reason, result_uuid
            )
            await target.kick(
                reason=f"Approved request {request['request_uuid']} | {reason}"
            )
        except Exception:
            await remove_infraction_by_uuid(result_uuid, guild.id)
            raise
    elif action == "BAN":
        result_uuid = await add_infraction(
            target_id, requester.id, "BAN", reason, guild.id
        )
        try:
            await guild.ban(
                target or discord.Object(id=target_id),
                reason=f"Approved request {request['request_uuid']} | {reason}",
                delete_message_days=0,
            )
        except Exception:
            await remove_infraction_by_uuid(result_uuid, guild.id)
            raise
        await _send_action_dm(target, guild, "Ban Notification", reason, result_uuid)
    elif action == "UNBAN":
        await guild.unban(
            discord.Object(id=target_id),
            reason=f"Approved request {request['request_uuid']} | {reason}",
        )
        result_uuid = await add_infraction(
            target_id, requester.id, "UNBAN", reason, guild.id
        )
    elif action == "WARNING_REMOVE":
        removed_count, records = await remove_user_warning(
            target_id, guild.id, payload.get("warn_id")
        )
        if not removed_count:
            raise RuntimeError("The warning no longer exists")
        result_uuid = records[0].get("uuid") if records else None
    elif action == "INFRACTION_REMOVE":
        removed = await remove_infraction_by_uuid(payload["uuid"], guild.id)
        if not removed:
            raise RuntimeError("The infraction no longer exists")
        result_uuid = removed["uuid"]
    log_mod(
        f"APPROVED {action}",
        requester,
        target or target_id,
        reason=reason,
        extra=f"request={request['request_uuid']}, result={result_uuid}",
    )
    await complete_approval_request(
        request["request_uuid"],
        guild.id,
        "EXECUTED",
        result_uuid,
        "Action completed successfully",
    )
    return {"result_uuid": result_uuid, "guild": guild, "requester": requester}
