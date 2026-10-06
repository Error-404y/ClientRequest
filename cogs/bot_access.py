import discord
from discord import app_commands
from discord.ext import commands

from utils.bot_access import (
    BOT_ACCESS_OWNER_IDS,
    ban_bot_user,
    ban_embed,
    parse_user_id,
    unban_bot_user,
)
from utils.embeds import error as error_embed
from utils.embeds import success as success_embed


async def notify_access_change(client, target_id, banned):
    embed = (
        ban_embed()
        if banned
        else success_embed(
            "Your ban from using this bot has been removed. You can use the bot again in all servers where it is available."
        )
    )
    if not banned:
        embed.title = "Bot Access Restored"
    embed.add_field(
        name="What This Means",
        value="This is a bot access restriction, not a ban from your Discord server."
        if banned
        else "Normal server permissions and command requirements still apply.",
        inline=False,
    )
    embed.timestamp = discord.utils.utcnow()
    try:
        user = client.get_user(target_id) or await client.fetch_user(target_id)
        await user.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        return "Not delivered: Discord does not allow DMs to this user."
    except discord.NotFound:
        return "Not delivered: Discord could not find this user."
    except discord.HTTPException:
        return "Not delivered: Discord returned an error. The access change was saved."
    return "Delivered"


class BotAccess(commands.Cog):
    @app_commands.command(
        name="bbmaja",
        description="Restrict a user from using the bot across all servers",
    )
    @app_commands.default_permissions()
    @app_commands.guild_only()
    @app_commands.describe(user_id="Numeric Discord user ID only")
    async def bbmaja(self, interaction: discord.Interaction, user_id: str):
        if interaction.user.id not in BOT_ACCESS_OWNER_IDS:
            await interaction.response.send_message(
                embed=error_embed("Only a bot owner can use this command."),
                ephemeral=True,
            )
            return
        try:
            target_id = parse_user_id(user_id)
            if target_id in BOT_ACCESS_OWNER_IDS:
                raise ValueError("Bot owners cannot be banned.")
        except ValueError as error:
            await interaction.response.send_message(
                embed=error_embed(str(error)), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        created = await ban_bot_user(target_id, interaction.user.id)
        embed = success_embed(
            "This user can no longer use the bot in any server."
            if created
            else "This user is already banned from using the bot."
        )
        embed.title = "Bot Access Revoked" if created else "Bot Ban Already Active"
        embed.add_field(name="User ID", value=f"`{target_id}`", inline=False)
        embed.add_field(name="Scope", value="All servers", inline=True)
        embed.add_field(name="Duration", value="Permanent until removed", inline=True)
        delivery = (
            await notify_access_change(interaction.client, target_id, True)
            if created
            else "Not sent: the user was already banned."
        )
        embed.add_field(name="DM Notification", value=delivery, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="bbmajaubb",
        description="Restore a user's access to the bot across all servers",
    )
    @app_commands.default_permissions()
    @app_commands.guild_only()
    @app_commands.describe(user_id="Numeric Discord user ID only")
    async def bbmajaubb(self, interaction: discord.Interaction, user_id: str):
        if interaction.user.id not in BOT_ACCESS_OWNER_IDS:
            await interaction.response.send_message(
                embed=error_embed("Only a bot owner can use this command."),
                ephemeral=True,
            )
            return
        try:
            target_id = parse_user_id(user_id)
        except ValueError as error:
            await interaction.response.send_message(
                embed=error_embed(str(error)), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        removed = await unban_bot_user(target_id, interaction.user.id)
        embed = success_embed(
            "This user can use the bot again in all servers, subject to normal permissions."
            if removed
            else "This user is not banned from using the bot."
        )
        embed.title = "Bot Access Restored" if removed else "No Active Bot Ban"
        embed.add_field(name="User ID", value=f"`{target_id}`", inline=False)
        delivery = (
            await notify_access_change(interaction.client, target_id, False)
            if removed
            else "Not sent: there was no active ban."
        )
        embed.add_field(name="DM Notification", value=delivery, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(BotAccess())
