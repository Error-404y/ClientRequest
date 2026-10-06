import discord
from discord import app_commands
from discord.ext import commands

from cogs.moderation_common import resolve_banned_user, resolve_user
from utils.embeds import error as error_embed
from utils.logger import log_command
from utils.permissions import can_ban, can_kick
from views.ban_buttons import BanConfirmView
from views.kick_buttons import KickConfirmView
from views.unban_buttons import UnbanConfirmView


class ModerationActionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _build_confirmation(
        self,
        guild,
        actor,
        user_input,
        reason,
        action,
    ):
        settings = {
            "ban": (can_ban, resolve_user, BanConfirmView),
            "unban": (can_ban, resolve_banned_user, UnbanConfirmView),
            "kick": (can_kick, resolve_user, KickConfirmView),
        }
        permission_check, resolver, view_type = settings[action]
        if not permission_check(actor):
            return None, None, "You do not have permission to use this command."
        target_obj, target_name, target_id = await resolver(
            guild,
            self.bot,
            user_input,
        )
        if not target_obj and not target_id:
            target_label = "banned user" if action == "unban" else "user"
            return (
                None,
                None,
                f"Could not find or resolve {target_label}: `{user_input}`",
            )
        description = f"Are you sure you want to {action} **{target_name}**?"
        if reason:
            description += f"\n\n{reason}"
        embed = discord.Embed(
            title=action.title(),
            description=description,
            color=discord.Color.from_rgb(255, 255, 255),
        )
        view = view_type(
            author_id=actor.id,
            target_user=target_obj or target_id,
            target_name=target_name,
            reason=reason,
        )
        return embed, view, None


    @app_commands.command(
        name="banz",
        description="Ban a user from the server with confirmation",
    )
    @app_commands.describe(
        user="Username, User Mention, or User ID to ban",
        reason="Reason for the ban (optional)",
    )
    async def banz_slash(
        self,
        interaction: discord.Interaction,
        user: app_commands.Range[str, 1, 100],
        reason: app_commands.Range[str, 1, 400] | None = None,
    ):
        log_command(
            interaction.user,
            "/banz",
            interaction.channel,
            f"user={user}, reason={reason}",
        )
        embed, view, error = await self._build_confirmation(
            interaction.guild,
            interaction.user,
            user,
            reason,
            "ban",
        )
        if error:
            await interaction.response.send_message(
                embed=error_embed(error),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embed, view=view)

    @commands.command(
        name="banZ",
        aliases=["banz"],
    )
    async def banz_prefix(
        self,
        ctx: commands.Context,
        user_input: str | None = None,
        *,
        reason: str | None = None,
    ):
        log_command(
            ctx.author,
            "!banZ",
            ctx.channel,
            f"user={user_input}, reason={reason}",
        )
        if not user_input:
            await ctx.send(
                embed=error_embed(
                    "Please specify a Username, Mention, or User ID to ban."
                )
            )
            return
        embed, view, error = await self._build_confirmation(
            ctx.guild,
            ctx.author,
            user_input,
            reason,
            "ban",
        )
        if error:
            await ctx.send(embed=error_embed(error))
            return
        await ctx.send(embed=embed, view=view)

    @app_commands.command(
        name="unbanz",
        description="Unban a user from the server with confirmation",
    )
    @app_commands.describe(
        user="Username, User Mention, or User ID to unban",
        reason="Reason for unbanning (optional)",
    )
    async def unbanz_slash(
        self,
        interaction: discord.Interaction,
        user: app_commands.Range[str, 1, 100],
        reason: app_commands.Range[str, 1, 400] | None = None,
    ):
        log_command(
            interaction.user,
            "/unbanz",
            interaction.channel,
            f"user={user}, reason={reason}",
        )
        embed, view, error = await self._build_confirmation(
            interaction.guild,
            interaction.user,
            user,
            reason,
            "unban",
        )
        if error:
            await interaction.response.send_message(
                embed=error_embed(error),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embed, view=view)

    @commands.command(
        name="unbanZ",
        aliases=["unbanz"],
    )
    async def unbanz_prefix(
        self,
        ctx: commands.Context,
        user_input: str | None = None,
        *,
        reason: str | None = None,
    ):
        log_command(
            ctx.author,
            "!unbanZ",
            ctx.channel,
            f"user={user_input}, reason={reason}",
        )
        if not user_input:
            await ctx.send(
                embed=error_embed("Please specify a Username or User ID to unban.")
            )
            return
        embed, view, error = await self._build_confirmation(
            ctx.guild,
            ctx.author,
            user_input,
            reason,
            "unban",
        )
        if error:
            await ctx.send(embed=error_embed(error))
            return
        await ctx.send(embed=embed, view=view)

    @app_commands.command(
        name="kickz",
        description="Kick a user from the server with confirmation",
    )
    @app_commands.describe(
        user="Username, User Mention, or User ID to kick",
        reason="Reason for the kick (optional)",
    )
    async def kickz_slash(
        self,
        interaction: discord.Interaction,
        user: app_commands.Range[str, 1, 100],
        reason: app_commands.Range[str, 1, 400] | None = None,
    ):
        log_command(
            interaction.user,
            "/kickz",
            interaction.channel,
            f"user={user}, reason={reason}",
        )
        embed, view, error = await self._build_confirmation(
            interaction.guild,
            interaction.user,
            user,
            reason,
            "kick",
        )
        if error:
            await interaction.response.send_message(
                embed=error_embed(error),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embed, view=view)

    @commands.command(
        name="kickZ",
        aliases=["kickz"],
    )
    async def kickz_prefix(
        self,
        ctx: commands.Context,
        user_input: str | None = None,
        *,
        reason: str | None = None,
    ):
        log_command(
            ctx.author,
            "!kickZ",
            ctx.channel,
            f"user={user_input}, reason={reason}",
        )
        if not user_input:
            await ctx.send(
                embed=error_embed(
                    "Please specify a Username, Mention, or User ID to kick."
                )
            )
            return
        embed, view, error = await self._build_confirmation(
            ctx.guild,
            ctx.author,
            user_input,
            reason,
            "kick",
        )
        if error:
            await ctx.send(embed=error_embed(error))
            return
        await ctx.send(embed=embed, view=view)

