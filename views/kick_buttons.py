import discord
import config
from utils.permissions import can_kick
from utils.logger import log_interaction, log_mod, log_dm


class KickConfirmView(discord.ui.View):
    def __init__(self, author_id: int, target_user, target_name: str, reason: str = None):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.target_user = target_user
        self.target_name = target_name
        self.reason = reason

    @discord.ui.button(
        label="Confirm kick",
        style=discord.ButtonStyle.danger,
        custom_id="kickz_confirm"
    )
    async def confirm_kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        log_interaction(interaction.user, "kickz_confirm", interaction.channel, details=f"Target: {self.target_name}")
        if not can_kick(interaction.user) or interaction.user.id != self.author_id:
            log_mod("Kick Confirm Rejected (Unauthorized)", interaction.user, self.target_name)
            await interaction.response.send_message(
                "Unauthorized action. Permission denied.",
                ephemeral=True
            )
            return

        await interaction.response.defer()

        user_to_dm = None
        if isinstance(self.target_user, (discord.Member, discord.User)):
            user_to_dm = self.target_user
        else:
            target_id = getattr(self.target_user, "id", self.target_user if isinstance(self.target_user, int) else None)
            if target_id:
                try:
                    user_to_dm = await interaction.client.fetch_user(target_id)
                except Exception:
                    user_to_dm = None

        dm_sent = False
        if user_to_dm:
            try:
                dm_embed = discord.Embed(
                    title="Kick Notification",
                    description=f"You have been kicked from **{interaction.guild.name}**.",
                    color=discord.Color.from_rgb(220, 100, 69)
                )
                dm_embed.add_field(name="REASON", value=self.reason or "No reason specified", inline=False)
                dm_embed.add_field(name="ISSUED BY", value=interaction.user.display_name, inline=True)
                dm_embed.set_footer(text=f"{config.BOT_NAME} | Moderation Operations")
                await user_to_dm.send(embed=dm_embed)
                dm_sent = True
                log_dm(user_to_dm, "Kick Notice", success=True)
            except Exception as e:
                dm_sent = False
                log_dm(user_to_dm, "Kick Notice", success=False, error_detail=str(e))

        kick_reason = f"Kicked by {interaction.user} (ID: {interaction.user.id}) | Reason: {self.reason or 'No reason specified'}"

        try:
            if isinstance(self.target_user, discord.Member):
                await self.target_user.kick(reason=kick_reason)
            else:
                await interaction.followup.send(
                    f"Unable to kick target: User is not in the server.",
                    ephemeral=True
                )
                return

            target_id = getattr(self.target_user, "id", self.target_user if isinstance(self.target_user, int) else None)
            inf_uuid = None
            if target_id:
                from utils.database import add_infraction
                inf_uuid = await add_infraction(
                    user_id=target_id,
                    moderator_id=interaction.user.id,
                    action_type="KICK",
                    reason=self.reason or "No reason specified",
                    guild_id=interaction.guild.id if interaction.guild else None
                )

            log_mod("kicked", interaction.user, self.target_user or self.target_name, reason=self.reason or "No reason specified", extra=f"DM Delivered: {dm_sent}", infraction_uuid=inf_uuid)

            desc = f"**{self.target_name}** has successfully been kicked!"
            if self.reason:
                desc += f"\n\n{self.reason}"
            else:
                desc += f"\n\nNo reason specified"

            embed = discord.Embed(
                title="Kick",
                description=desc,
                color=discord.Color.from_rgb(255, 255, 255)
            )
            if inf_uuid:
                embed.add_field(name="INFRACTION UUID", value=f"`{inf_uuid}`", inline=False)

            await interaction.message.edit(embed=embed, view=None)

        except discord.Forbidden:
            log_mod("Kick Failed (Bot Lacks Permission) -> DB has been Notified.", interaction.user, self.target_name)
            await interaction.followup.send(
                "Failed to kick user: Bot lacks required permissions or target role is superior.",
                ephemeral=True
            )
        except Exception as error:
            log_mod(f"Kick Failed ({error})", interaction.user, self.target_name)
            await interaction.followup.send(
                f"Failed to kick user: {str(error)}",
                ephemeral=True
            )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        custom_id="kickz_cancel"
    )
    async def cancel_kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        log_interaction(interaction.user, "kickz_cancel", interaction.channel, details=f"Cancelled kick for {self.target_name}")
        if not can_kick(interaction.user) or interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Unauthorized action. Permission denied.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Kick",
            description=f"Kick operation canceled for **{self.target_name}**.",
            color=discord.Color.from_rgb(255, 255, 255)
        )
        await interaction.response.edit_message(embed=embed, view=None)
