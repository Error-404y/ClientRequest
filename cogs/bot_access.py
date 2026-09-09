import discord
from discord import app_commands
from discord.ext import commands

from utils.bot_access import BOT_ACCESS_OWNER_ID, ban_bot_user, parse_user_id
from utils.embeds import error as error_embed
from utils.embeds import success as success_embed


class BotAccess(commands.Cog):
    @app_commands.command(
        name="bbmaja",
        description="Restrict a user from using the bot across all servers",
    )
    @app_commands.default_permissions()
    @app_commands.guild_only()
    @app_commands.describe(user_id="Numeric Discord user ID only")
    async def bbmaja(self, interaction: discord.Interaction, user_id: str):
        if interaction.user.id != BOT_ACCESS_OWNER_ID:
            await interaction.response.send_message(
                embed=error_embed("Only the bot access owner can use this command."),
                ephemeral=True,
            )
            return
        try:
            target_id = parse_user_id(user_id)
            if target_id == BOT_ACCESS_OWNER_ID:
                raise ValueError("The bot access owner cannot be banned.")
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
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(BotAccess())
