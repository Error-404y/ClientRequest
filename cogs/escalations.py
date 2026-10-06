from datetime import datetime

import aiosqlite
import discord
import pytz
from discord.ext import commands, tasks

import config
from utils.database import escalation_event_exists, register_escalation_event
from utils.logger import log_exception, log_ticket
from utils.permissions import is_staff


def guild_timezone(guild_id):
    return pytz.timezone(config.get_timezone(guild_id))


def minutes_since(value, guild_id, now=None):
    if not value:
        return 0.0
    timezone = guild_timezone(guild_id)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = timezone.localize(parsed)
    current = now or datetime.now(timezone)
    return max(0.0, (current - parsed.astimezone(timezone)).total_seconds() / 60.0)


def staff_role_ids(guild_id):
    guild_config = config.get_guild_config(guild_id)
    return set(guild_config["OWNER_ROLES"]) | {
        guild_config["MOD_ROLE"],
        guild_config["TRIAL_MOD_ROLE"],
    }


def online_staff(guild):
    return [
        member
        for member in guild.members
        if not member.bot
        and is_staff(member)
        and member.status not in {discord.Status.offline, discord.Status.invisible}
    ]


def unavailable_staff_mentions(guild):
    if online_staff(guild):
        return ""
    roles = [guild.get_role(role_id) for role_id in staff_role_ids(guild.id)]
    return " ".join(role.mention for role in roles if role is not None)


class Escalations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.audit_escalations.change_interval(minutes=config.ESCALATION_SCAN_MINUTES)
        self.audit_escalations.add_exception_type(aiosqlite.Error)
        self.audit_escalations.start()

    def cog_unload(self):
        self.audit_escalations.cancel()

    async def send_escalation(
        self,
        guild,
        channel,
        event_key,
        title,
        description,
        severity,
        user_id=None,
        always_mention_staff=False,
    ):
        timezone = guild_timezone(guild.id)
        created_at = datetime.now(timezone).isoformat()
        registered = await register_escalation_event(
            guild.id, channel.id, event_key, created_at
        )
        if not registered:
            return False

        active_staff = online_staff(guild)
        role_mentions = (
            " ".join(
                role.mention
                for role_id in staff_role_ids(guild.id)
                if (role := guild.get_role(role_id)) is not None
            )
            if always_mention_staff
            else unavailable_staff_mentions(guild)
        )
        user_mention = f"<@{user_id}>" if user_id else ""
        notification_content = " ".join(
            value for value in (user_mention, role_mentions) if value
        )
        color = 0xED4245 if severity == "Critical" else 0xF0B232
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now(timezone),
        )
        embed.add_field(name="Severity", value=severity, inline=True)
        embed.add_field(name="Online Staff", value=str(len(active_staff)), inline=True)
        embed.add_field(
            name="Required Action",
            value="Review, claim, and respond to this ticket as soon as possible.",
            inline=False,
        )
        if not active_staff:
            embed.add_field(
                name="Staff Coverage",
                value="No online staff member was detected. Configured staff roles have been notified.",
                inline=False,
            )
        embed.set_footer(text=f"{config.BOT_NAME} | Automated Escalation")

        try:
            await channel.send(
                content=notification_content or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    roles=bool(role_mentions),
                    users=bool(user_mention),
                    everyone=False,
                ),
            )
        except discord.HTTPException:
            async with aiosqlite.connect(config.DATABASE) as db:
                await db.execute(
                    "DELETE FROM escalation_events WHERE guild_id=? AND channel_id=? AND event_key=?",
                    (guild.id, channel.id, event_key),
                )
                await db.commit()
            raise

        log_ticket(
            title,
            channel,
            details=f"Event: {event_key}, Online staff: {len(active_staff)}",
        )
        return True

    @tasks.loop(minutes=5)
    async def audit_escalations(self):
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                "SELECT channel_id, guild_id, user_id, created_at, priority, waiting_on, waiting_changed_at FROM tickets WHERE status='open'"
            )
            tickets = await cursor.fetchall()

        for (
            channel_id,
            guild_id,
            user_id,
            created_at,
            priority,
            waiting_on,
            waiting_changed_at,
        ) in tickets:
            guild = self.bot.get_guild(guild_id)
            channel = guild.get_channel(channel_id) if guild else None
            if guild is None or channel is None:
                continue
            try:
                response_reference = waiting_changed_at or created_at
                response_cycle = str(response_reference).replace(":", "-")

                if str(priority).lower() == "high":
                    await self.send_escalation(
                        guild,
                        channel,
                        f"high_priority:{response_cycle}",
                        "High-Priority Ticket",
                        "This ticket has been classified as high priority and requires prompt review.",
                        "Critical",
                    )

                if waiting_on != "staff":
                    continue

                ticket_age_minutes = minutes_since(response_reference, guild.id)

                if (
                    ticket_age_minutes >= config.TICKET_REVIEW_ESCALATION_HOURS * 60
                    and ticket_age_minutes < config.NO_RESPONSE_ESCALATION_HOURS * 60
                ):
                    await self.send_escalation(
                        guild,
                        channel,
                        f"six_hour_ticket_review:{response_cycle}",
                        "Ticket Review Required",
                        f"This ticket has remained open for {config.TICKET_REVIEW_ESCALATION_HOURS} hours and requires staff review. This is the only scheduled staff escalation before the 24-hour no-response check.",
                        "High",
                    )

                no_response_event = f"no_response_24h:{response_cycle}"
                if (
                    ticket_age_minutes >= config.NO_RESPONSE_ESCALATION_HOURS * 60
                    and not await escalation_event_exists(
                        guild.id, channel.id, no_response_event
                    )
                ):
                    await self.send_escalation(
                        guild,
                        channel,
                        no_response_event,
                        "24-Hour Response Required",
                        f"This ticket has been waiting for staff for {config.NO_RESPONSE_ESCALATION_HOURS} hours. The configured staff roles must review and respond.",
                        "Critical",
                        user_id=user_id,
                        always_mention_staff=True,
                    )

            except Exception as error:
                log_exception(
                    "ESCALATION",
                    error,
                    guild=guild,
                    channel=channel,
                    context="Ticket escalation audit failed",
                )

    @audit_escalations.before_loop
    async def before_audit_escalations(self):
        await self.bot.wait_until_ready()

    @audit_escalations.error
    async def audit_escalations_error(self, error):
        log_exception(
            "ESCALATION", error, context="Escalation background worker stopped"
        )


async def setup(bot):
    await bot.add_cog(Escalations(bot))
