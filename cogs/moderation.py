from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import add_infraction
from utils.embeds import error as error_embed
from utils.logger import log_dm, log_exception, log_interaction, log_mod
from utils.permissions import can_moderate_target, is_staff

UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
UNIT_NAMES = {"s": "seconds", "m": "minutes", "h": "hours"}


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="mutez", description="Temporarily timeout a server member"
    )
    @app_commands.describe(
        user="Member to timeout",
        time="Timeout length",
        unit="Use seconds, minutes, or hours",
        reason="Reason for the timeout",
    )
    @app_commands.choices(
        unit=[
            app_commands.Choice(name="Seconds", value="s"),
            app_commands.Choice(name="Minutes", value="m"),
            app_commands.Choice(name="Hours", value="h"),
        ]
    )
    async def mutez(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        time: app_commands.Range[int, 1, 2419200],
        unit: app_commands.Choice[str],
        reason: app_commands.Range[str, 3, 300] | None = None,
    ):
        guild = interaction.guild
        if guild is None or not config.is_guild_configured(guild.id):
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used in a configured server."
                ),
                ephemeral=True,
            )
            return
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to timeout members."),
                ephemeral=True,
            )
            return
        seconds = time * UNIT_SECONDS[unit.value]
        if seconds > 2_419_200:
            await interaction.response.send_message(
                embed=error_embed("Discord timeouts cannot exceed 28 days."),
                ephemeral=True,
            )
            return
        if user.bot:
            await interaction.response.send_message(
                embed=error_embed("Bots cannot be timed out."), ephemeral=True
            )
            return
        if not can_moderate_target(interaction.user, user):
            await interaction.response.send_message(
                embed=error_embed(
                    "You cannot timeout yourself, the server owner, or a member with an equal or higher role."
                ),
                ephemeral=True,
            )
            return
        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.moderate_members:
            await interaction.response.send_message(
                embed=error_embed("I require the Moderate Members permission."),
                ephemeral=True,
            )
            return
        if user.top_role >= bot_member.top_role:
            await interaction.response.send_message(
                embed=error_embed(
                    "My highest role must be above the selected member's highest role."
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        reason_text = reason or "No reason provided"
        duration_text = f"{time} {UNIT_NAMES[unit.value]}"
        audit_reason = (
            f"{reason_text} | Duration: {duration_text} | Moderator: "
            f"{interaction.user} ({interaction.user.id})"
        )
        log_interaction(
            interaction.user,
            "mutez",
            interaction.channel,
            details=f"Target: {user.id}, Duration: {duration_text}",
        )
        try:
            await user.timeout(timedelta(seconds=seconds), reason=audit_reason)
        except discord.HTTPException as error:
            reference = log_exception(
                "MODERATION",
                error,
                guild=guild,
                channel=interaction.channel,
                user=interaction.user,
                context=f"Timeout rejected for target {user.id}",
            )
            await interaction.followup.send(
                embed=error_embed(
                    f"Discord rejected the timeout because of permissions, role hierarchy, or an unavailable member. Error reference: `{reference}`"
                ),
                ephemeral=True,
            )
            return
        try:
            infraction_uuid = await add_infraction(
                user_id=user.id,
                moderator_id=interaction.user.id,
                action_type="TIMEOUT",
                reason=f"{reason_text} | Duration: {duration_text}",
                guild_id=guild.id,
            )
        except Exception as record_error:
            reference = log_exception(
                "DATABASE",
                record_error,
                guild=guild,
                channel=interaction.channel,
                user=interaction.user,
                context=f"Timeout record creation failed for target {user.id}",
            )
            reverted = False
            try:
                await user.timeout(
                    None,
                    reason=f"Timeout reverted after record failure | Moderator: {interaction.user} ({interaction.user.id})",
                )
                reverted = True
            except discord.HTTPException as rollback_error:
                log_exception(
                    "MODERATION",
                    rollback_error,
                    guild=guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context=f"Failed to revert unrecorded timeout for target {user.id}",
                )
            message = (
                "The timeout was reverted because its tracking record could not be created."
                if reverted
                else "The timeout was applied, but its tracking record could not be created. Remove it manually and inspect the error logs immediately."
            )
            await interaction.followup.send(
                embed=error_embed(f"{message} Error reference: `{reference}`"),
                ephemeral=True,
            )
            return
        log_mod(
            "TIMEOUT",
            interaction.user,
            user,
            reason_text,
            extra=f"guild={guild.id}, duration={duration_text}, uuid={infraction_uuid}",
        )
        embed = discord.Embed(
            title="Member Timeout Applied",
            description="The member has been temporarily restricted from communicating in this server.",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Member", value=f"{user.mention}\n`{user.id}`", inline=True
        )
        embed.add_field(
            name="Moderator",
            value=f"{interaction.user.mention}\n`{interaction.user.id}`",
            inline=True,
        )
        embed.add_field(name="Duration", value=duration_text, inline=True)
        embed.add_field(name="Reason", value=reason_text, inline=False)
        embed.add_field(
            name="Infraction UUID", value=f"`{infraction_uuid}`", inline=False
        )
        embed.add_field(
            name="Record Lookup",
            value="Authorized staff can inspect this action with `/findz` or `/infraction`.",
            inline=False,
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"{config.BOT_NAME} | Moderation Record")
        await interaction.followup.send(embed=embed, ephemeral=True)
        dm_embed = discord.Embed(
            title="You Have Been Timed Out",
            description=f"A temporary communication restriction was applied in **{guild.name}**.",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        dm_embed.add_field(name="Duration", value=duration_text, inline=True)
        dm_embed.add_field(name="Reason", value=reason_text, inline=False)
        dm_embed.add_field(name="Reference", value=f"`{infraction_uuid}`", inline=False)
        dm_embed.set_footer(text=f"{config.BOT_NAME} | Moderation Notice")
        try:
            await user.send(embed=dm_embed)
            log_dm(user, "Timeout Notice", success=True)
        except discord.Forbidden:
            log_dm(
                user,
                "Timeout Notice",
                success=False,
                error_detail="Direct Messages Disabled",
            )
        except discord.HTTPException as error:
            log_dm(user, "Timeout Notice", success=False, error_detail=str(error))
            log_exception(
                "DM",
                error,
                guild=guild,
                user=user,
                context="Failed to deliver timeout notice",
            )


async def setup(bot):
    await bot.add_cog(Moderation(bot))
