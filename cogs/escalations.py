from datetime import datetime

import aiosqlite
import discord
import pytz
from discord.ext import commands, tasks

import config
from utils.database import clear_escalation_event, register_escalation_event
from utils.logger import log_exception, log_ticket
from utils.permissions import is_staff

timezone = pytz.timezone("Europe/Berlin")


def minutes_since(value, now=None):
    if not value:
        return 0.0
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
    roles = staff_role_ids(guild.id)
    return [
        member
        for member in guild.members
        if not member.bot
        and any(role.id in roles for role in member.roles)
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
        self.audit_escalations.start()

    def cog_unload(self):
        self.audit_escalations.cancel()

    async def send_escalation(self, guild, channel, event_key, title, description, severity):
        created_at = datetime.now(timezone).isoformat()
        registered = await register_escalation_event(guild.id, channel.id, event_key, created_at)
        if not registered:
            return False

        active_staff = online_staff(guild)
        role_mentions = unavailable_staff_mentions(guild)
        color = 0xED4245 if severity == "Critical" else 0xF0B232
        embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone))
        embed.add_field(name="Severity", value=severity, inline=True)
        embed.add_field(name="Online Staff", value=str(len(active_staff)), inline=True)
        embed.add_field(name="Required Action", value="Review, claim, and respond to this ticket as soon as possible.", inline=False)
        if not active_staff:
            embed.add_field(name="Staff Coverage", value="No online staff member was detected. Configured staff roles have been notified.", inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Automated Escalation")

        try:
            await channel.send(
                content=role_mentions or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )
        except discord.HTTPException:
            async with aiosqlite.connect(config.DATABASE) as db:
                await db.execute(
                    "DELETE FROM escalation_events WHERE guild_id=? AND channel_id=? AND event_key=?",
                    (guild.id, channel.id, event_key),
                )
                await db.commit()
            raise

        log_ticket(title, channel, details=f"Event: {event_key}, Online staff: {len(active_staff)}")
        return True

    async def latest_customer_wait(self, channel, user_id, created_at):
        latest_customer_message = None
        latest_staff_response = None
        try:
            async for message in channel.history(limit=100):
                if message.author.id == user_id and latest_customer_message is None:
                    latest_customer_message = message
                if is_staff(message.author) and latest_staff_response is None:
                    latest_staff_response = message
                if latest_customer_message and latest_staff_response:
                    break
        except discord.HTTPException as error:
            log_exception(
                "ESCALATION",
                error,
                guild=channel.guild,
                channel=channel,
                user=user_id,
                context="Failed to inspect ticket history for customer wait",
            )
            return None, 0.0

        if latest_customer_message is None:
            if latest_staff_response:
                return None, 0.0
            return "created", minutes_since(created_at)
        if latest_staff_response and latest_staff_response.created_at > latest_customer_message.created_at:
            return None, 0.0
        return str(latest_customer_message.id), minutes_since(latest_customer_message.created_at.isoformat())

    @tasks.loop(minutes=5)
    async def audit_escalations(self):
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                "SELECT channel_id, guild_id, user_id, created_at, claimed_by, priority FROM tickets WHERE status='open'"
            )
            tickets = await cursor.fetchall()

        for channel_id, guild_id, user_id, created_at, claimed_by, priority in tickets:
            guild = self.bot.get_guild(guild_id)
            channel = guild.get_channel(channel_id) if guild else None
            if guild is None or channel is None:
                continue
            try:
                if not online_staff(guild):
                    await self.send_escalation(
                        guild,
                        channel,
                        "no_staff_online",
                        "Staff Coverage Required",
                        "This ticket is open while no configured staff member is currently online.",
                        "Critical",
                    )
                else:
                    await clear_escalation_event(guild.id, channel.id, "no_staff_online")

                if claimed_by is None and minutes_since(created_at) >= config.UNCLAIMED_ESCALATION_MINUTES:
                    await self.send_escalation(
                        guild,
                        channel,
                        "unclaimed",
                        "Unclaimed Ticket Escalation",
                        f"This ticket has remained unclaimed for at least {config.UNCLAIMED_ESCALATION_MINUTES} minutes.",
                        "High",
                    )

                if str(priority).lower() == "high":
                    await self.send_escalation(
                        guild,
                        channel,
                        "high_priority",
                        "High-Priority Ticket",
                        "This ticket has been classified as high priority and requires prompt staff review.",
                        "Critical",
                    )

                wait_key, wait_minutes = await self.latest_customer_wait(channel, user_id, created_at)
                if wait_key and wait_minutes >= config.CUSTOMER_WAIT_ESCALATION_MINUTES:
                    await self.send_escalation(
                        guild,
                        channel,
                        f"customer_wait:{wait_key}",
                        "Customer Response Overdue",
                        f"The customer has been waiting for a staff response for at least {config.CUSTOMER_WAIT_ESCALATION_MINUTES} minutes.",
                        "High",
                    )
            except Exception as error:
                log_exception("ESCALATION", error, guild=guild, channel=channel, context="Ticket escalation audit failed")

    @audit_escalations.before_loop
    async def before_audit_escalations(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Escalations(bot))
