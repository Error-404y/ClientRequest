import discord

from utils.embeds import error as error_embed
from utils.logger import log_exception


async def notify_interaction_failure(interaction, reference):
    message = f"The operation could not be completed. Error reference: `{reference}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=error_embed(message), ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=error_embed(message), ephemeral=True
            )
    except discord.HTTPException as error:
        log_exception(
            "INTERACTION",
            error,
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            context=f"Failed to deliver error reference {reference}",
        )


class ReliableView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        custom_id = getattr(item, "custom_id", type(item).__name__)
        reference = log_exception(
            "VIEW",
            error,
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            context=f"Component callback: {custom_id}",
        )
        await notify_interaction_failure(interaction, reference)


class ReliableModal(discord.ui.Modal):
    async def on_error(self, interaction, error):
        reference = log_exception(
            "MODAL",
            error,
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            context=f"Modal submission: {self.title}",
        )
        await notify_interaction_failure(interaction, reference)
