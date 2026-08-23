import discord

from utils.database import add_infraction, remove_infraction_by_uuid
from utils.embeds import error as error_embed
from utils.logger import log_exception, log_interaction, log_mod
from utils.permissions import can_ban
from views.base import ReliableView


class UnbanConfirmView(ReliableView):
    def __init__(
        self, author_id: int, target_user, target_name: str, reason: str | None = None
    ):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.target_user = target_user
        self.target_name = target_name
        self.reason = " ".join(reason.split())[:400] if reason else None

    @discord.ui.button(
        label="Confirm", style=discord.ButtonStyle.success, custom_id="unbanz_confirm"
    )
    async def confirm_unban(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        log_interaction(
            interaction.user,
            "unbanz_confirm",
            interaction.channel,
            details=f"Target: {self.target_name}",
        )
        if not can_ban(interaction.user) or interaction.user.id != self.author_id:
            log_mod(
                "Unban Confirm Rejected (Unauthorized)",
                interaction.user,
                self.target_name,
            )
            await interaction.response.send_message(
                embed=error_embed("Unauthorized action. Permission denied."),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        action_completed = False

        try:
            user_id = getattr(
                self.target_user,
                "id",
                self.target_user if isinstance(self.target_user, int) else None,
            )
            if not user_id:
                await interaction.followup.send(
                    embed=error_embed(
                        f"Unable to unban target: Invalid user resolution for {self.target_name}."
                    ),
                    ephemeral=True,
                )
                return
            infraction_uuid = await add_infraction(
                user_id=user_id,
                moderator_id=interaction.user.id,
                action_type="UNBAN",
                reason=self.reason or "Unbanned via /unbanZ",
                guild_id=interaction.guild.id,
            )
            if isinstance(self.target_user, (discord.Member, discord.User)):
                unban_target = self.target_user
            elif isinstance(self.target_user, int):
                unban_target = discord.Object(id=self.target_user)
            elif hasattr(self.target_user, "id"):
                unban_target = discord.Object(id=self.target_user.id)
            else:
                await remove_infraction_by_uuid(
                    infraction_uuid, interaction.guild.id
                )
                return
            try:
                await interaction.guild.unban(
                    unban_target, reason=self.reason or "Unbanned via /unbanZ"
                )
                action_completed = True
            except Exception:
                await remove_infraction_by_uuid(
                    infraction_uuid, interaction.guild.id
                )
                raise

            log_mod(
                "unbanned",
                interaction.user,
                self.target_user or self.target_name,
                reason=self.reason or "Unbanned via /unbanZ",
            )

            desc = f"**{self.target_name}** has successfully been unbanned!"
            if self.reason:
                desc += f"\n\n{self.reason}"

            embed = discord.Embed(
                title="Unban",
                description=desc,
                color=discord.Color.from_rgb(255, 255, 255),
            )
            embed.add_field(
                name="INFRACTION UUID",
                value=f"`{infraction_uuid}`",
                inline=False,
            )

            await interaction.message.edit(embed=embed, view=None)

        except discord.NotFound as error:
            if action_completed:
                reference = log_exception(
                    "MODERATION",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context=f"Unban confirmation message missing for {self.target_name}",
                )
                await interaction.followup.send(
                    embed=error_embed(
                        f"The unban succeeded, but the original confirmation message no longer exists. Error reference: `{reference}`"
                    ),
                    ephemeral=True,
                )
                return
            log_mod(
                "Unban Failed (User Not Banned)", interaction.user, self.target_name
            )
            await interaction.followup.send(
                embed=error_embed(
                    f"User not found in ban registry: **{self.target_name}** is not currently banned."
                ),
                ephemeral=True,
            )
        except discord.Forbidden as error:
            log_mod(
                "Unban Failed (Bot Lacks Permission)",
                interaction.user,
                self.target_name,
            )
            reference = log_exception(
                "MODERATION",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context=f"Unban permission denied for {self.target_name}",
            )
            await interaction.followup.send(
                embed=error_embed(
                    (
                        f"The unban succeeded, but the confirmation message could not be updated. Error reference: `{reference}`"
                        if action_completed
                        else f"Failed to unban user: Bot lacks required administrative permissions. Error reference: `{reference}`"
                    )
                ),
                ephemeral=True,
            )
        except Exception as error:
            log_mod(f"Unban Failed ({error})", interaction.user, self.target_name)
            reference = log_exception(
                "MODERATION",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context=f"Unban failed for {self.target_name}",
            )
            await interaction.followup.send(
                embed=error_embed(
                    (
                        f"The unban succeeded, but a follow-up operation failed. Error reference: `{reference}`"
                        if action_completed
                        else f"Failed to unban user. Error reference: `{reference}`"
                    )
                ),
                ephemeral=True,
            )

    @discord.ui.button(
        label="Cancel", style=discord.ButtonStyle.secondary, custom_id="unbanz_cancel"
    )
    async def cancel_unban(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        log_interaction(
            interaction.user,
            "unbanz_cancel",
            interaction.channel,
            details=f"Cancelled unban for {self.target_name}",
        )
        if not can_ban(interaction.user) or interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=error_embed("Unauthorized action. Permission denied."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Unban",
            description=f"Unban operation canceled for **{self.target_name}**.",
            color=discord.Color.from_rgb(255, 255, 255),
        )
        await interaction.response.edit_message(embed=embed, view=None)
