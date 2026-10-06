import re

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.moderation_common import resolve_user
from utils.database import (
    add_infraction,
    get_infraction_by_uuid,
    increment_user_activity,
    remove_user_warning,
)
from utils.embeds import error as error_embed
from utils.governance import (
    appeal_view,
    approval_queued_embed,
    queue_moderation_approval,
)
from utils.logger import (
    log_command,
    log_dm,
    log_exception,
    log_filter,
    log_mod,
)
from utils.permissions import can_moderate_target, can_warn_or_view_history


class ModerationWarningsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.guild:
            return

        content_lower = message.content.lower()
        has_bad_word = False
        detected_words = []
        bad_words_list = getattr(config, "BAD_WORDS", [])

        for bad_word in bad_words_list:
            pattern = r"\b" + re.escape(bad_word) + r"\b"

            if re.search(pattern, content_lower):
                has_bad_word = True
                detected_words.append(bad_word)

        await increment_user_activity(
            message.author.id,
            guild_id=message.guild.id,
            has_bad_word=has_bad_word,
        )

        if has_bad_word:
            log_filter(
                message.author,
                detected_words,
                message.channel,
            )

            await add_infraction(
                user_id=message.author.id,
                moderator_id=self.bot.user.id,
                action_type="BAD_WORD",
                reason=("Used bad word(s): " + ", ".join(detected_words)),
                guild_id=message.guild.id,
            )


    async def _issue_warning(
        self,
        *,
        guild: discord.Guild,
        moderator,
        target_obj,
        target_name: str,
        actual_id: int,
        reason: str,
    ):
        reason = " ".join(reason.split())[:400]
        infraction_uuid = await add_infraction(
            user_id=actual_id,
            moderator_id=moderator.id,
            action_type="WARN",
            reason=reason,
            guild_id=guild.id,
        )
        dm_sent = False

        if isinstance(target_obj, (discord.Member, discord.User)):
            try:
                dm_embed = discord.Embed(
                    title="Warning Notice",
                    description=(
                        f"You have been issued a warning in **{guild.name}**."
                    ),
                    color=discord.Color.from_rgb(241, 196, 15),
                )
                dm_embed.add_field(
                    name="REASON",
                    value=reason,
                    inline=False,
                )
                dm_embed.add_field(
                    name="ISSUED BY",
                    value=moderator.display_name,
                    inline=True,
                )
                dm_embed.set_footer(text=(f"{config.BOT_NAME} | Moderation Operations"))

                await target_obj.send(
                    embed=dm_embed,
                    view=appeal_view(guild.id, infraction_uuid),
                )
                dm_sent = True

                log_dm(
                    target_obj,
                    "Warning Notice",
                    success=True,
                )
            except discord.Forbidden as exc:
                dm_sent = False
                log_dm(
                    target_obj,
                    "Warning Notice",
                    success=False,
                    error_detail=str(exc),
                )
            except discord.HTTPException as exc:
                dm_sent = False
                log_dm(
                    target_obj, "Warning Notice", success=False, error_detail=str(exc)
                )
                log_exception(
                    "DM",
                    exc,
                    guild=guild,
                    user=target_obj,
                    context="Failed to deliver warning notice",
                )

        log_mod(
            "warned",
            moderator,
            target_obj or target_name,
            reason=reason,
            extra=(f"DM Delivered: {dm_sent} | Infraction UUID: {infraction_uuid}"),
        )

        embed = discord.Embed(
            title="Warning Issued",
            description=(
                f"An official warning has been registered for **{target_name}**."
            ),
            color=discord.Color.from_rgb(241, 196, 15),
        )
        embed.add_field(
            name="TARGET USER",
            value=f"**{target_name}** (`{actual_id}`)",
            inline=True,
        )
        embed.add_field(
            name="MODERATOR",
            value=moderator.mention,
            inline=True,
        )
        embed.add_field(
            name="INFRACTION UUID",
            value=f"`{infraction_uuid}`",
            inline=False,
        )
        embed.add_field(
            name="REASON",
            value=reason,
            inline=False,
        )
        embed.add_field(
            name="DIRECT MESSAGE STATUS",
            value=(
                "Delivered to Direct Messages"
                if dm_sent
                else "Failed to Deliver (Direct Messages Disabled)"
            ),
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Infraction Logged")

        return embed

    @app_commands.command(
        name="warnz",
        description="Warn a user and send them a DM notification",
    )
    @app_commands.describe(
        user="Username, User Mention, or User ID to warn",
        reason="Reason for the warning",
    )
    async def warnz_slash(
        self,
        interaction: discord.Interaction,
        user: app_commands.Range[str, 1, 100],
        reason: app_commands.Range[str, 1, 400],
    ):
        await interaction.response.defer()

        log_command(
            interaction.user,
            "/warnz",
            interaction.channel,
            f"user={user}, reason={reason}",
        )

        if not can_warn_or_view_history(interaction.user):
            log_mod(
                "Permission Denied for /warnz",
                interaction.user,
                user,
            )

            await interaction.followup.send(
                embed=error_embed("You do not have permission to use this command."),
                ephemeral=True,
            )
            return

        target_obj, target_name, target_id = await resolve_user(
            interaction.guild,
            self.bot,
            user,
        )

        if not target_obj and not target_id:
            await interaction.followup.send(
                embed=error_embed(f"Could not find or resolve user: `{user}`"),
                ephemeral=True,
            )
            return

        actual_id = target_id or getattr(target_obj, "id", None)

        if not actual_id:
            await interaction.followup.send(
                embed=error_embed(f"Could not determine the user ID for `{user}`"),
                ephemeral=True,
            )
            return
        if not can_moderate_target(interaction.user, target_obj or actual_id):
            await interaction.followup.send(
                embed=error_embed(
                    "You cannot warn yourself, the server owner, or a member with an equal or higher role."
                ),
                ephemeral=True,
            )
            return

        try:
            approval = await queue_moderation_approval(
                self.bot,
                interaction.guild,
                interaction.user,
                "WARN",
                actual_id,
                target_name,
                reason,
            )
        except RuntimeError as error:
            await interaction.followup.send(
                embed=error_embed(str(error)), ephemeral=True
            )
            return
        if approval:
            await interaction.followup.send(
                embed=approval_queued_embed(approval), ephemeral=True
            )
            return

        embed = await self._issue_warning(
            guild=interaction.guild,
            moderator=interaction.user,
            target_obj=target_obj,
            target_name=target_name,
            actual_id=actual_id,
            reason=reason,
        )

        await interaction.followup.send(embed=embed)

    @commands.command(
        name="warnZ",
        aliases=["warnz"],
    )
    async def warnz_prefix(
        self,
        ctx: commands.Context,
        user_input: str | None = None,
        *,
        reason: str = "No reason specified",
    ):
        log_command(
            ctx.author,
            "!warnZ",
            ctx.channel,
            f"user={user_input}, reason={reason}",
        )

        if not can_warn_or_view_history(ctx.author):
            log_mod(
                "Permission Denied for !warnZ",
                ctx.author,
                user_input,
            )

            await ctx.send(
                embed=error_embed("You do not have permission to use this command.")
            )
            return

        if not user_input:
            await ctx.send(
                embed=error_embed(
                    "Please specify a Username, Mention, or User ID to warn."
                )
            )
            return

        target_obj, target_name, target_id = await resolve_user(
            ctx.guild,
            self.bot,
            user_input,
        )

        if not target_obj and not target_id:
            await ctx.send(
                embed=error_embed(f"Could not find or resolve user: `{user_input}`")
            )
            return

        actual_id = target_id or getattr(target_obj, "id", None)

        if not actual_id:
            await ctx.send(
                embed=error_embed("Could not determine the target user's ID.")
            )
            return
        if not can_moderate_target(ctx.author, target_obj or actual_id):
            await ctx.send(
                embed=error_embed(
                    "You cannot warn yourself, the server owner, or a member with an equal or higher role."
                )
            )
            return

        try:
            approval = await queue_moderation_approval(
                self.bot,
                ctx.guild,
                ctx.author,
                "WARN",
                actual_id,
                target_name,
                reason,
            )
        except RuntimeError as error:
            await ctx.send(embed=error_embed(str(error)))
            return
        if approval:
            await ctx.send(embed=approval_queued_embed(approval))
            return

        embed = await self._issue_warning(
            guild=ctx.guild,
            moderator=ctx.author,
            target_obj=target_obj,
            target_name=target_name,
            actual_id=actual_id,
            reason=reason,
        )

        await ctx.send(embed=embed)

    async def _remove_warning(
        self,
        *,
        guild: discord.Guild,
        moderator,
        target_obj,
        target_name: str,
        actual_id: int,
        warn_id: str,
        reason: str,
    ):
        reason = " ".join(reason.split())[:400]
        count_removed, records = await remove_user_warning(
            actual_id,
            warn_id=warn_id,
            guild_id=guild.id if guild else None,
        )

        if count_removed == 0:
            return None, None, 0, records

        removed_uuid = records[0].get("uuid") if records and len(records) == 1 else None

        log_mod(
            "Removed Warning",
            moderator,
            target_obj or target_name,
            reason=reason,
            extra=(
                f"Removed count: {count_removed}, "
                f"Warn ID: {warn_id}, "
                f"UUID: {removed_uuid}"
            ),
        )

        dm_sent = False

        if isinstance(target_obj, (discord.Member, discord.User)):
            try:
                dm_embed = discord.Embed(
                    title="Warning Removed",
                    description=(
                        "A warning issued on your account in "
                        f"**{guild.name}** has been removed."
                    ),
                    color=discord.Color.from_rgb(46, 204, 113),
                )

                if warn_id:
                    dm_embed.add_field(
                        name="WARNING ID / UUID",
                        value=f"`{removed_uuid or warn_id}`",
                        inline=True,
                    )
                else:
                    dm_embed.add_field(
                        name="WARNINGS CLEARED",
                        value=f"`{count_removed}` warning(s)",
                        inline=True,
                    )

                dm_embed.add_field(
                    name="REASON FOR REMOVAL",
                    value=reason,
                    inline=False,
                )
                dm_embed.add_field(
                    name="MODERATOR",
                    value=moderator.display_name,
                    inline=True,
                )
                dm_embed.set_footer(text=(f"{config.BOT_NAME} | Moderation Operations"))

                await target_obj.send(embed=dm_embed)
                dm_sent = True

                log_dm(
                    target_obj,
                    "Warning Removal Notice",
                    success=True,
                )
            except discord.Forbidden as exc:
                log_dm(
                    target_obj,
                    "Warning Removal Notice",
                    success=False,
                    error_detail=str(exc),
                )
            except discord.HTTPException as exc:
                log_dm(
                    target_obj,
                    "Warning Removal Notice",
                    success=False,
                    error_detail=str(exc),
                )
                log_exception(
                    "DM",
                    exc,
                    guild=guild,
                    user=target_obj,
                    context="Failed to deliver warning removal notice",
                )

        embed = discord.Embed(
            title="Warning Removed",
            description=(f"Warning record updated for **{target_name}**."),
            color=discord.Color.from_rgb(46, 204, 113),
        )
        embed.add_field(
            name="TARGET USER",
            value=f"**{target_name}** (`{actual_id}`)",
            inline=True,
        )
        embed.add_field(
            name="MODERATOR",
            value=moderator.mention,
            inline=True,
        )

        if warn_id:
            embed.add_field(
                name="REMOVED WARNING ID / UUID",
                value=f"`{removed_uuid or warn_id}`",
                inline=True,
            )
        else:
            embed.add_field(
                name="TOTAL REMOVED",
                value=f"`{count_removed}` warning(s)",
                inline=True,
            )

        embed.add_field(
            name="REASON",
            value=reason,
            inline=False,
        )
        embed.add_field(
            name="DIRECT MESSAGE STATUS",
            value=(
                "Delivered to Direct Messages"
                if dm_sent
                else "Failed to Deliver (Direct Messages Disabled)"
            ),
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Infraction Removed")

        return embed, removed_uuid, count_removed, records

    @app_commands.command(
        name="warnremovez",
        description="Remove warning(s) from a user and notify them via DM",
    )
    @app_commands.describe(
        user="Username, User Mention, or User ID",
        warn_id="Specific Warning ID or UUID",
        reason="Reason for removing the warning",
    )
    async def warnremovez_slash(
        self,
        interaction: discord.Interaction,
        user: app_commands.Range[str, 1, 100],
        warn_id: app_commands.Range[str, 1, 100] | None = None,
        reason: app_commands.Range[str, 1, 400] = "No reason specified",
    ):
        log_command(
            interaction.user,
            "/warnremovez",
            interaction.channel,
            f"user={user}, warn_id={warn_id}, reason={reason}",
        )

        if not can_warn_or_view_history(interaction.user):
            log_mod(
                "Permission Denied for /warnremovez",
                interaction.user,
                user,
            )

            await interaction.response.send_message(
                embed=error_embed("You do not have permission to use this command."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        if user and ("-" in user or (user.isdigit() and len(user) < 15)):
            found_inf = await get_infraction_by_uuid(user.strip(), interaction.guild.id)

            if found_inf:
                warn_id = user.strip()
                user = str(found_inf["user_id"])

        target_obj, target_name, target_id = await resolve_user(
            interaction.guild,
            self.bot,
            user,
        )

        actual_id = target_id or getattr(target_obj, "id", None)

        if not actual_id:
            await interaction.followup.send(
                embed=error_embed(f"Could not find or resolve user: `{user}`"),
                ephemeral=True,
            )
            return

        try:
            approval = await queue_moderation_approval(
                self.bot,
                interaction.guild,
                interaction.user,
                "WARNING_REMOVE",
                actual_id,
                target_name,
                reason,
                {"warn_id": warn_id},
            )
        except RuntimeError as error:
            await interaction.followup.send(
                embed=error_embed(str(error)), ephemeral=True
            )
            return
        if approval:
            await interaction.followup.send(
                embed=approval_queued_embed(approval), ephemeral=True
            )
            return

        result = await self._remove_warning(
            guild=interaction.guild,
            moderator=interaction.user,
            target_obj=target_obj,
            target_name=target_name,
            actual_id=actual_id,
            warn_id=warn_id,
            reason=reason,
        )

        embed, _, count_removed, _ = result

        if count_removed == 0:
            if warn_id:
                message = (
                    f"No warning found with ID/UUID `{warn_id}` for **{target_name}**."
                )
            else:
                message = (
                    "No active warning records found for "
                    f"**{target_name}** (`{actual_id}`)."
                )

            await interaction.followup.send(
                embed=error_embed(message),
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=embed)

    @commands.command(
        name="warnremoveZ",
        aliases=["warnremovez"],
    )
    async def warnremovez_prefix(
        self,
        ctx: commands.Context,
        user_input: str | None = None,
        warn_id_or_reason: str | None = None,
        *,
        reason: str = "No reason specified",
    ):
        log_command(
            ctx.author,
            "!warnremoveZ",
            ctx.channel,
            (
                f"user={user_input}, "
                f"warn_id_or_reason={warn_id_or_reason}, "
                f"reason={reason}"
            ),
        )

        if not can_warn_or_view_history(ctx.author):
            log_mod(
                "Permission Denied for !warnremoveZ",
                ctx.author,
                user_input,
            )

            await ctx.send(
                embed=error_embed("You do not have permission to use this command.")
            )
            return

        if not user_input:
            await ctx.send(
                embed=error_embed(
                    "Please specify a Username, Mention, or User ID "
                    "to remove warning from."
                )
            )
            return

        warn_id = None

        if warn_id_or_reason:
            if (
                warn_id_or_reason.isdigit()
                or len(warn_id_or_reason) >= 6
                or "-" in warn_id_or_reason
            ):
                warn_id = warn_id_or_reason
            else:
                if reason == "No reason specified":
                    reason = warn_id_or_reason
                else:
                    reason = f"{warn_id_or_reason} {reason}"

        if user_input and (
            "-" in user_input or (user_input.isdigit() and len(user_input) < 15)
        ):
            found_inf = await get_infraction_by_uuid(
                user_input.strip(),
                ctx.guild.id,
            )

            if found_inf:
                warn_id = user_input.strip()
                user_input = str(found_inf["user_id"])

        target_obj, target_name, target_id = await resolve_user(
            ctx.guild,
            self.bot,
            user_input,
        )

        actual_id = target_id or getattr(target_obj, "id", None)

        if not actual_id:
            await ctx.send(
                embed=error_embed(f"Could not find or resolve user: `{user_input}`")
            )
            return

        try:
            approval = await queue_moderation_approval(
                self.bot,
                ctx.guild,
                ctx.author,
                "WARNING_REMOVE",
                actual_id,
                target_name,
                reason,
                {"warn_id": warn_id},
            )
        except RuntimeError as error:
            await ctx.send(embed=error_embed(str(error)))
            return
        if approval:
            await ctx.send(embed=approval_queued_embed(approval))
            return

        result = await self._remove_warning(
            guild=ctx.guild,
            moderator=ctx.author,
            target_obj=target_obj,
            target_name=target_name,
            actual_id=actual_id,
            warn_id=warn_id,
            reason=reason,
        )

        embed, _, count_removed, _ = result

        if count_removed == 0:
            if warn_id:
                message = (
                    f"No warning found with ID/UUID `{warn_id}` for **{target_name}**."
                )
            else:
                message = (
                    "No active warning records found for "
                    f"**{target_name}** (`{actual_id}`)."
                )

            await ctx.send(embed=error_embed(message))
            return

        await ctx.send(embed=embed)

