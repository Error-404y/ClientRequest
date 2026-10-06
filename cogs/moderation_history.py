from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.moderation_common import resolve_user
from utils.database import (
    get_infraction_by_uuid,
    get_ticket_by_uuid,
    get_user_infractions,
    get_user_stats,
    remove_infraction_by_uuid,
)
from utils.embeds import error as error_embed
from utils.governance import approval_queued_embed, queue_moderation_approval
from utils.logger import log_command, log_exception, log_mod
from utils.permissions import can_warn_or_view_history


class ModerationHistoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def build_history_embed(
        self,
        guild,
        target_obj,
        target_name,
        target_id,
    ):
        actual_id = target_id or getattr(target_obj, "id", None)
        gid = guild.id if guild else None

        stats = await get_user_stats(
            actual_id,
            guild_id=gid,
        )
        infractions = await get_user_infractions(
            actual_id,
            guild_id=gid,
        )

        warnings = [i for i in infractions if i["action_type"] == "WARN"]
        bad_word_logs = [i for i in infractions if i["action_type"] == "BAD_WORD"]
        other_mods = [
            i for i in infractions if i["action_type"] in ("BAN", "UNBAN", "KICK")
        ]

        warn_count = len(warnings)
        total_bad_words = stats["bad_word_count"]

        if warn_count >= 3 or len(other_mods) >= 2:
            risk_status = "[HIGH RISK - REPEAT OFFENDER]"
            risk_color = discord.Color.from_rgb(231, 76, 60)
        elif warn_count >= 1 or total_bad_words >= 3:
            risk_status = "[NOTICE - WARNINGS RECORDED]"
            risk_color = discord.Color.from_rgb(241, 196, 15)
        else:
            risk_status = "[CLEAN MEMBER]"
            risk_color = discord.Color.from_rgb(46, 204, 113)

        server_name = guild.name if guild else "Server"

        embed = discord.Embed(
            title=(f"User History Audit Profile ({server_name}) | {target_name}"),
            color=risk_color,
        )

        if (
            isinstance(target_obj, (discord.Member, discord.User))
            and target_obj.display_avatar
        ):
            embed.set_thumbnail(url=target_obj.display_avatar.url)

        embed.add_field(
            name="TARGET USER ID",
            value=f"`{actual_id}`",
            inline=True,
        )
        embed.add_field(
            name="RISK STATUS",
            value=f"`{risk_status}`",
            inline=True,
        )
        embed.add_field(
            name="LAST ACTIVE",
            value=f"`{stats['last_active']}`",
            inline=True,
        )

        embed.add_field(
            name="ACTIVITY & MESSAGES",
            value=(
                "• Total Messages Sent : "
                f"`{stats['message_count']}`\n"
                "• Flagged Bad Words   : "
                f"`{stats['bad_word_count']}`"
            ),
            inline=False,
        )

        if warnings:
            warn_lines = []

            for warning in warnings[:5]:
                mod_user = guild.get_member(warning["moderator_id"]) if guild else None
                mod_name = (
                    mod_user.display_name
                    if mod_user
                    else f"ID: {warning['moderator_id']}"
                )
                uuid_display = f" (`{warning['uuid']}`)" if warning.get("uuid") else ""

                warn_lines.append(
                    f"• [{warning['timestamp']}]"
                    f"{uuid_display} "
                    f"Reason: `{warning['reason']}` | "
                    f"Issued by: {mod_name}"
                )

            warning_string = "\n".join(warn_lines)

            if len(warnings) > 5:
                warning_string += f"\n*...and {len(warnings) - 5} older warning(s)*"
        else:
            warning_string = "*No warnings recorded in database.*"

        embed.add_field(
            name=f"WARNING RECORDS ({len(warnings)})",
            value=warning_string[:1024],
            inline=False,
        )

        if other_mods:
            mod_lines = []

            for moderation in other_mods[:5]:
                uuid_display = (
                    f" (`{moderation['uuid']}`)" if moderation.get("uuid") else ""
                )

                mod_lines.append(
                    f"• [{moderation['timestamp']}]"
                    f"{uuid_display} "
                    f"Action: `{moderation['action_type']}` | "
                    f"Reason: {moderation['reason']}"
                )

            moderation_string = "\n".join(mod_lines)
        else:
            moderation_string = "*No ban, unban, or kick actions recorded.*"

        embed.add_field(
            name=f"MODERATION HISTORY ({len(other_mods)})",
            value=moderation_string[:1024],
            inline=False,
        )

        if bad_word_logs:
            bad_word_lines = []

            for bad_word in bad_word_logs[:3]:
                uuid_display = (
                    f" (`{bad_word['uuid']}`)" if bad_word.get("uuid") else ""
                )

                bad_word_lines.append(
                    f"• [{bad_word['timestamp']}]{uuid_display} {bad_word['reason']}"
                )

            bad_word_string = "\n".join(bad_word_lines)
        else:
            bad_word_string = "*No profanity flags recorded.*"

        embed.add_field(
            name=f"BAD WORD LOGS ({len(bad_word_logs)})",
            value=bad_word_string[:1024],
            inline=False,
        )

        embed.set_footer(text=(f"{config.BOT_NAME} | Audit Record ID: {actual_id}"))

        return embed

    @app_commands.command(
        name="historyz",
        description=(
            "View full message count, bad words, and warning/ban history of a user"
        ),
    )
    @app_commands.describe(user="Username, User Mention, or User ID to check")
    async def historyz_slash(
        self,
        interaction: discord.Interaction,
        user: str | None = None,
    ):
        log_command(
            interaction.user,
            "/historyz",
            interaction.channel,
            f"user={user}",
        )

        if not can_warn_or_view_history(interaction.user):
            log_mod(
                "Permission Denied for /historyz",
                interaction.user,
                user,
            )

            await interaction.response.send_message(
                embed=error_embed("You do not have permission to view user history."),
                ephemeral=True,
            )
            return

        search_user = user or str(interaction.user.id)

        target_obj, target_name, target_id = await resolve_user(
            interaction.guild,
            self.bot,
            search_user,
        )

        actual_id = target_id or getattr(target_obj, "id", None)

        if not actual_id:
            await interaction.response.send_message(
                embed=error_embed(f"Could not find or resolve user: `{search_user}`"),
                ephemeral=True,
            )
            return

        embed = await self.build_history_embed(
            interaction.guild,
            target_obj,
            target_name,
            actual_id,
        )

        await interaction.response.send_message(embed=embed)

    @commands.command(
        name="historyZ",
        aliases=["historyz"],
    )
    async def historyz_prefix(
        self,
        ctx: commands.Context,
        user_input: str | None = None,
    ):
        log_command(
            ctx.author,
            "!historyZ",
            ctx.channel,
            f"user={user_input}",
        )

        if not can_warn_or_view_history(ctx.author):
            log_mod(
                "Permission Denied for !historyZ",
                ctx.author,
                user_input,
            )

            await ctx.send(
                embed=error_embed("You do not have permission to view user history.")
            )
            return

        search_user = user_input or str(ctx.author.id)

        target_obj, target_name, target_id = await resolve_user(
            ctx.guild,
            self.bot,
            search_user,
        )

        actual_id = target_id or getattr(target_obj, "id", None)

        if not actual_id:
            await ctx.send(
                embed=error_embed(f"Could not find or resolve user: `{search_user}`")
            )
            return

        embed = await self.build_history_embed(
            ctx.guild,
            target_obj,
            target_name,
            actual_id,
        )

        await ctx.send(embed=embed)

    @staticmethod
    def _normalize_uuid(uuid_value: str) -> str:
        if uuid_value is None:
            return ""

        return str(uuid_value).strip().strip("`").strip()

    @staticmethod
    def _build_infraction_embed(infraction):
        action_type = str(infraction["action_type"]).upper()

        if action_type == "BAN":
            embed_color = discord.Color.from_rgb(231, 76, 60)
        elif action_type == "WARN":
            embed_color = discord.Color.from_rgb(241, 196, 15)
        else:
            embed_color = discord.Color.from_rgb(52, 152, 219)

        embed = discord.Embed(
            title=(f"Infraction Details | {infraction['action_type']}"),
            color=embed_color,
        )
        embed.add_field(
            name="INFRACTION UUID",
            value=f"`{infraction['uuid']}`",
            inline=False,
        )
        embed.add_field(
            name="TARGET USER ID",
            value=f"`{infraction['user_id']}`",
            inline=True,
        )
        embed.add_field(
            name="MODERATOR ID",
            value=f"`{infraction['moderator_id']}`",
            inline=True,
        )
        embed.add_field(
            name="TIMESTAMP",
            value=f"`{infraction['timestamp']}`",
            inline=True,
        )
        embed.add_field(
            name="REASON",
            value=(infraction["reason"] or "No reason specified")[:1024],
            inline=False,
        )

        return embed

    @staticmethod
    def _build_removed_infraction_embed(removed):
        embed = discord.Embed(
            title="Infraction Removed via UUID",
            description=(
                f"Successfully deleted `{removed['action_type']}` infraction."
            ),
            color=discord.Color.from_rgb(46, 204, 113),
        )
        embed.add_field(
            name="INFRACTION UUID",
            value=f"`{removed['uuid']}`",
            inline=False,
        )
        embed.add_field(
            name="TARGET USER ID",
            value=f"`{removed['user_id']}`",
            inline=True,
        )
        embed.add_field(
            name="ACTION TYPE",
            value=f"`{removed['action_type']}`",
            inline=True,
        )
        embed.add_field(
            name="REASON",
            value=(removed["reason"] or "No reason specified")[:1024],
            inline=False,
        )

        return embed

    @staticmethod
    def _ticket_value(ticket, key, default=None):
        try:
            value = ticket[key]
        except (KeyError, IndexError, TypeError):
            return default

        return default if value is None else value

    async def _resolve_ticket_user(self, user_id, guild=None):
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None, "Unknown User", str(user_id)

        user = None

        if guild:
            user = guild.get_member(user_id_int)

        if user is None:
            user = self.bot.get_user(user_id_int)

        if user is None:
            try:
                user = await self.bot.fetch_user(user_id_int)
            except discord.HTTPException as error:
                log_exception(
                    "DISCORD",
                    error,
                    guild=guild,
                    user=user_id_int,
                    context="Failed to resolve ticket user for infraction display",
                )
                user = None

        if user:
            return user, user.name, str(user_id_int)

        return None, "Unknown User", str(user_id_int)

    async def _build_ticket_embed(self, ticket, guild=None):
        ticket_uuid = self._ticket_value(ticket, "uuid", "Unknown")
        user_id = self._ticket_value(ticket, "user_id", "Unknown")
        application = self._ticket_value(ticket, "application", "Unknown")
        status = self._ticket_value(ticket, "status", "Unknown")
        channel_id = self._ticket_value(ticket, "channel_id", "Unknown")
        guild_id = self._ticket_value(ticket, "guild_id", "Unknown")
        created_at = self._ticket_value(ticket, "created_at", "Unknown")
        priority = self._ticket_value(ticket, "priority", "Not set")
        label = self._ticket_value(ticket, "label", "Not assigned")
        form_response = self._ticket_value(ticket, "form_response", [])

        try:
            created_dt = datetime.fromisoformat(str(created_at))
            created_display = created_dt.strftime("%d.%m.%Y, %H:%M")
        except (ValueError, TypeError):
            created_display = str(created_at)
        claimed_by = self._ticket_value(ticket, "claimed_by")
        claimed_at = self._ticket_value(ticket, "claimed_at")
        closed_by = self._ticket_value(ticket, "closed_by")
        closed_at = self._ticket_value(ticket, "closed_at")
        close_reason = self._ticket_value(ticket, "close_reason")

        if guild is None:
            try:
                guild = self.bot.get_guild(int(guild_id))
            except (TypeError, ValueError):
                guild = None

        user_obj, user_name, resolved_user_id = await self._resolve_ticket_user(
            user_id,
            guild,
        )

        status_text = str(status).strip().title()
        priority_text = str(priority).strip().title()

        if str(status).lower() == "open":
            embed_color = discord.Color.from_rgb(46, 204, 113)
        elif str(status).lower() == "closed":
            embed_color = discord.Color.from_rgb(99, 110, 114)
        else:
            embed_color = discord.Color.blurple()

        embed = discord.Embed(
            title="Support Ticket",
            description=f"Ticket `{ticket_uuid}`",
            color=embed_color,
        )

        user_value = f"**{user_name}**\n`{resolved_user_id}`"

        if user_obj:
            user_value = f"**{user_name}**\n{user_obj.mention}\n`{resolved_user_id}`"

            if user_obj.display_avatar:
                embed.set_thumbnail(url=user_obj.display_avatar.url)

        embed.add_field(
            name="USER",
            value=user_value,
            inline=False,
        )
        embed.add_field(
            name="CATEGORY",
            value=str(application),
            inline=True,
        )
        embed.add_field(
            name="STATUS",
            value=status_text,
            inline=True,
        )
        embed.add_field(
            name="PRIORITY",
            value=priority_text,
            inline=True,
        )
        embed.add_field(
            name="TICKET LABEL",
            value=str(label or "Not assigned"),
            inline=True,
        )

        try:
            channel_id_int = int(channel_id)
            channel_value = f"<#{channel_id_int}>\n`{channel_id_int}`"
        except (TypeError, ValueError):
            channel_value = f"`{channel_id}`"

        embed.add_field(
            name="TICKET CHANNEL",
            value=channel_value,
            inline=True,
        )

        if guild:
            guild_value = f"**{guild.name}**\n`{guild.id}`"
        else:
            guild_value = f"`{guild_id}`"

        embed.add_field(
            name="SERVER",
            value=guild_value,
            inline=True,
        )
        embed.add_field(
            name="CREATED",
            value=f"`{created_display}`",
            inline=False,
        )
        embed.add_field(
            name="TICKET UUID",
            value=f"`{ticket_uuid}`",
            inline=False,
        )

        if form_response:
            response_text = "\n\n".join(
                f"**{item.get('question', 'Question')}**\n{item.get('answer', 'Not provided')}"
                for item in form_response
            )
            embed.add_field(
                name="FORM RESPONSES", value=response_text[:1024], inline=False
            )

        if claimed_by:
            claimed_obj, claimed_name, claimed_id = await self._resolve_ticket_user(
                claimed_by,
                guild,
            )
            claimed_value = f"**{claimed_name}**\n`{claimed_id}`"

            if claimed_obj:
                claimed_value = (
                    f"**{claimed_name}**\n{claimed_obj.mention}\n`{claimed_id}`"
                )

            embed.add_field(
                name="CLAIMED BY",
                value=claimed_value,
                inline=True,
            )

        if claimed_at:
            embed.add_field(
                name="CLAIMED AT",
                value=f"`{claimed_at}`",
                inline=True,
            )

        if closed_by:
            closed_obj, closed_name, closed_id = await self._resolve_ticket_user(
                closed_by,
                guild,
            )
            closed_value = f"**{closed_name}**\n`{closed_id}`"

            if closed_obj:
                closed_value = f"**{closed_name}**\n{closed_obj.mention}\n`{closed_id}`"

            embed.add_field(
                name="CLOSED BY",
                value=closed_value,
                inline=True,
            )

        if closed_at:
            embed.add_field(
                name="CLOSED AT",
                value=f"`{closed_at}`",
                inline=True,
            )

        if close_reason:
            embed.add_field(
                name="CLOSE REASON",
                value=str(close_reason),
                inline=False,
            )

        embed.set_footer(text=config.BOT_NAME)

        return embed

    async def _process_infraction_lookup(
        self,
        guild,
        actor,
        uuid_value,
        action_value,
    ):
        if action_value not in ("view", "remove"):
            return error_embed("Invalid action. Please use `view` or `remove`.")

        infraction = await get_infraction_by_uuid(uuid_value, guild.id)
        if infraction:
            try:
                infraction_guild_id = int(infraction["guild_id"])
            except (KeyError, IndexError, TypeError, ValueError):
                infraction_guild_id = None
            if infraction_guild_id != guild.id:
                infraction = None

        if infraction:
            if action_value == "remove":
                try:
                    approval = await queue_moderation_approval(
                        self.bot,
                        guild,
                        actor,
                        "INFRACTION_REMOVE",
                        infraction["user_id"],
                        str(infraction["user_id"]),
                        f"Remove infraction {infraction['uuid']}",
                        {"uuid": infraction["uuid"]},
                    )
                except RuntimeError as error:
                    return error_embed(str(error))
                if approval:
                    return approval_queued_embed(approval)
                removed = await remove_infraction_by_uuid(uuid_value, guild.id)
                if not removed:
                    return error_embed(f"Could not remove UUID: `{uuid_value}`")
                log_mod(
                    "Removed Infraction by UUID",
                    actor,
                    removed["user_id"],
                    reason=f"Removed via UUID {removed['uuid']}",
                    extra=f"Infraction UUID: {removed['uuid']}",
                )
                return self._build_removed_infraction_embed(removed)
            return self._build_infraction_embed(infraction)

        ticket = await get_ticket_by_uuid(uuid_value, guild.id)
        if ticket:
            try:
                ticket_guild_id = int(ticket["guild_id"])
            except (KeyError, IndexError, TypeError, ValueError):
                ticket_guild_id = None
            if ticket_guild_id != guild.id:
                ticket = None

        if ticket:
            if action_value == "remove":
                return error_embed(
                    "This UUID belongs to a support ticket. Ticket records cannot "
                    "be removed with the infraction command."
                )
            return await self._build_ticket_embed(ticket, guild)

        return error_embed(
            f"No ticket or infraction found matching UUID: `{uuid_value}`"
        )

    @app_commands.command(
        name="infraction",
        description=(
            "Search or manage an infraction, or look up a support ticket UUID"
        ),
    )
    @app_commands.describe(
        uuid="Infraction UUID or Ticket UUID",
        action=(
            "Optional action: 'view' to inspect or 'remove' to delete an infraction"
        ),
    )
    async def infraction_slash(
        self,
        interaction: discord.Interaction,
        uuid: str,
        action: str = "view",
    ):
        log_command(
            interaction.user,
            "/infraction",
            interaction.channel,
            f"uuid={uuid}, action={action}",
        )
        if not can_warn_or_view_history(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to use this command."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        uuid_value = self._normalize_uuid(uuid)
        if not uuid_value:
            await interaction.followup.send(
                embed=error_embed("Please specify an Infraction UUID or Ticket UUID."),
                ephemeral=True,
            )
            return
        embed = await self._process_infraction_lookup(
            interaction.guild,
            interaction.user,
            uuid_value,
            str(action).strip().lower(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.command(name="infraction")
    async def infraction_prefix(
        self,
        ctx: commands.Context,
        uuid_input: str | None = None,
        action: str = "view",
    ):
        log_command(
            ctx.author,
            "!infraction",
            ctx.channel,
            f"uuid={uuid_input}, action={action}",
        )
        if not can_warn_or_view_history(ctx.author):
            await ctx.send(
                embed=error_embed("You do not have permission to use this command.")
            )
            return
        if not uuid_input:
            await ctx.send(
                embed=error_embed("Please specify an Infraction UUID or Ticket UUID.")
            )
            return
        uuid_value = self._normalize_uuid(uuid_input)
        embed = await self._process_infraction_lookup(
            ctx.guild,
            ctx.author,
            uuid_value,
            str(action).strip().lower(),
        )
        await ctx.send(embed=embed)


