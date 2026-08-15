import os
import datetime

import discord
import pytz
from discord.ext import commands

from utils.database import add_infraction


class LoggingMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.monitor_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "MonitorUUID"
        )

        os.makedirs(
            self.monitor_dir,
            exist_ok=True
        )

        self.timezone = pytz.timezone(
            "Europe/Berlin"
        )

    async def log_action(
        self,
        action_type,
        user,
        moderator,
        reason,
        guild_id=None
    ):
        user_id = getattr(
            user,
            "id",
            user
        )

        if hasattr(moderator, "id"):
            moderator_id = moderator.id
        else:
            moderator_id = None

        user_name = str(user)
        moderator_name = str(moderator)

        # Database creates the infraction and UUID.
        # It also creates the MonitorUUID/<UUID>.txt file.
        event_uuid = await add_infraction(
            user_id=user_id,
            moderator_id=moderator_id or 0,
            action_type=action_type,
            reason=reason,
            guild_id=guild_id
        )

        # Use the same timezone as database.py.
        timestamp = datetime.datetime.now(
            self.timezone
        ).strftime(
            "%d/%m/%Y - %H:%M"
        )

        print(
            f"[LoggingMonitor] "
            f"{action_type} | "
            f"UUID: {event_uuid} | "
            f"Timestamp: {timestamp} Europe/Berlin | "
            f"User: {user_name} ({user_id}) | "
            f"Moderator: {moderator_name} "
            f"({moderator_id or 'Unknown'})"
        )

        return event_uuid

    def create_embed(
        self,
        action_type,
        user,
        moderator,
        reason,
        event_uuid
    ):
        embed = discord.Embed(
            title="Moderation Log",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now(
                datetime.timezone.utc
            )
        )

        embed.add_field(
            name="Action",
            value=action_type,
            inline=True
        )

        embed.add_field(
            name="Event UUID",
            value=f"`{event_uuid}`",
            inline=False
        )

        embed.add_field(
            name="User",
            value=(
                f"{user.mention}\n"
                f"`{user}`\n"
                f"ID: `{user.id}`"
            ),
            inline=True
        )

        if hasattr(moderator, "mention"):
            moderator_value = (
                f"{moderator.mention}\n"
                f"`{moderator}`\n"
                f"ID: `{moderator.id}`"
            )
        else:
            moderator_value = f"`{moderator}`"

        embed.add_field(
            name="Moderator",
            value=moderator_value,
            inline=True
        )

        embed.add_field(
            name="Reason",
            value=reason,
            inline=False
        )

        embed.set_footer(
            text="Logging Monitor"
        )

        return embed

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild,
        user
    ):
        try:
            async for entry in guild.audit_logs(
                limit=5,
                action=discord.AuditLogAction.ban
            ):
                if (
                    entry.target
                    and entry.target.id == user.id
                ):
                    moderator = entry.user

                    reason = (
                        entry.reason
                        or "No reason provided."
                    )

                    event_uuid = await self.log_action(
                        action_type="BAN",
                        user=user,
                        moderator=moderator,
                        reason=reason,
                        guild_id=guild.id
                    )

                    print(
                        f"[LoggingMonitor] "
                        f"BAN UUID: {event_uuid}"
                    )

                    return

        except Exception as e:
            print(
                f"[LoggingMonitor] "
                f"Failed to read ban audit log: {e}"
            )

        event_uuid = await self.log_action(
            action_type="BAN",
            user=user,
            moderator="Unknown Moderator",
            reason="Audit log entry not found.",
            guild_id=guild.id
        )

        print(
            f"[LoggingMonitor] "
            f"BAN UUID: {event_uuid}"
        )

    @commands.Cog.listener()
    async def on_member_remove(
        self,
        member
    ):
        try:
            async for entry in member.guild.audit_logs(
                limit=5,
                action=discord.AuditLogAction.kick
            ):
                if (
                    entry.target
                    and entry.target.id == member.id
                ):
                    moderator = entry.user

                    reason = (
                        entry.reason
                        or "No reason provided."
                    )

                    event_uuid = await self.log_action(
                        action_type="KICK",
                        user=member,
                        moderator=moderator,
                        reason=reason,
                        guild_id=member.guild.id
                    )

                    print(
                        f"[LoggingMonitor] "
                        f"KICK UUID: {event_uuid}"
                    )

                    return

        except Exception as e:
            print(
                f"[LoggingMonitor] "
                f"Failed to read kick audit log: {e}"
            )

    @commands.command(name="warn")
    @commands.has_permissions(
        manage_messages=True
    )
    async def warn_user(
        self,
        ctx,
        member: discord.Member,
        *,
        reason="No reason provided."
    ):
        event_uuid = await self.log_action(
            action_type="WARN",
            user=member,
            moderator=ctx.author,
            reason=reason,
            guild_id=(
                ctx.guild.id
                if ctx.guild
                else None
            )
        )

        embed = self.create_embed(
            action_type="WARN",
            user=member,
            moderator=ctx.author,
            reason=reason,
            event_uuid=event_uuid
        )

        await ctx.send(
            embed=embed
        )


async def setup(bot):
    await bot.add_cog(
        LoggingMonitor(bot)
    )
