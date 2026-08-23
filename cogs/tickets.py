import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import (
    get_ticket_controls,
    get_ticket_record,
    set_ticket_control_message,
    set_ticket_label,
)
from utils.embeds import apply_ticket_label
from utils.embeds import error as error_embed
from utils.logger import log_exception, log_interaction, log_ticket
from utils.permissions import is_staff
from views.closed_buttons import ClosedTicketButtons
from views.dropdown import TicketPanel
from views.ticket_buttons import TicketButtons


class TicketControlRecovery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recovered = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.recovered:
            return
        for record in await get_ticket_controls():
            channel = self.bot.get_channel(record["channel_id"])
            if not isinstance(channel, discord.TextChannel):
                continue
            message = None
            message_id = record["control_message_id"]
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
            if message is None:
                try:
                    async for candidate in channel.history(
                        limit=100, oldest_first=record["status"] == "open"
                    ):
                        if candidate.author.id != self.bot.user.id:
                            continue
                        custom_ids = {
                            getattr(component, "custom_id", None)
                            for row in candidate.components
                            for component in getattr(row, "children", [])
                        }
                        target_custom_id = (
                            "zer_claim" if record["status"] == "open" else "zer_reopen"
                        )
                        if target_custom_id in custom_ids:
                            message = candidate
                            await set_ticket_control_message(channel.id, candidate.id)
                            break
                except discord.HTTPException as error:
                    log_exception(
                        "TICKET",
                        error,
                        guild=channel.guild,
                        channel=channel,
                        context="Ticket control recovery failed",
                    )
                    continue
            if message:
                try:
                    if message.embeds:
                        embed = discord.Embed.from_dict(message.embeds[0].to_dict())
                        apply_ticket_label(embed, record["label"])
                    else:
                        embed = None
                    if record["status"] == "closed":
                        await message.edit(embed=embed, view=ClosedTicketButtons())
                        continue
                    view = TicketButtons(record["claimed_by"])
                    form = None
                    if record["application"] == "Moderator Application":
                        form = config.MODERATOR_FORM
                    elif record["application"] == "Uploader Application":
                        form = config.UPLOADER_FORM
                    if form:
                        view.add_item(
                            discord.ui.Button(
                                label="Application Form",
                                style=discord.ButtonStyle.link,
                                url=form,
                            )
                        )
                    await message.edit(embed=embed, view=view)
                except discord.HTTPException as error:
                    log_exception(
                        "TICKET",
                        error,
                        guild=channel.guild,
                        channel=channel,
                        context="Ticket control state restoration failed",
                    )
        self.recovered = True

    @app_commands.command(
        name="labelz", description="Assign a classification label to an open ticket"
    )
    @app_commands.describe(label="Classification applied to this ticket")
    @app_commands.choices(
        label=[
            app_commands.Choice(name="Billing", value="Billing"),
            app_commands.Choice(name="Technical", value="Technical"),
            app_commands.Choice(name="Urgent", value="Urgent"),
            app_commands.Choice(
                name="Waiting for Customer", value="Waiting for Customer"
            ),
            app_commands.Choice(name="Escalated", value="Escalated"),
            app_commands.Choice(name="Clear Label", value="__clear__"),
        ]
    )
    async def labelz(
        self,
        interaction: discord.Interaction,
        label: app_commands.Choice[str],
    ):
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed(
                    "Only authorized staff can change ticket labels."
                ),
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used in a registered ticket channel."
                ),
                ephemeral=True,
            )
            return
        ticket = await get_ticket_record(interaction.channel.id)
        if (
            ticket is None
            or ticket["guild_id"] != interaction.guild_id
            or ticket["status"] != "open"
        ):
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used in an open registered ticket channel."
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        selected_label = None if label.value == "__clear__" else label.value
        if not await set_ticket_label(interaction.channel.id, selected_label):
            await interaction.followup.send(
                embed=error_embed(
                    "The ticket is no longer open, so its label was not changed."
                ),
                ephemeral=True,
            )
            return
        control_message = None
        if ticket["control_message_id"]:
            try:
                control_message = await interaction.channel.fetch_message(
                    ticket["control_message_id"]
                )
            except discord.HTTPException:
                control_message = None
        if control_message is None:
            try:
                async for candidate in interaction.channel.history(
                    limit=100, oldest_first=True
                ):
                    if candidate.author.id != interaction.client.user.id:
                        continue
                    custom_ids = {
                        getattr(component, "custom_id", None)
                        for row in candidate.components
                        for component in getattr(row, "children", [])
                    }
                    if "zer_claim" in custom_ids:
                        control_message = candidate
                        await set_ticket_control_message(
                            interaction.channel.id, candidate.id
                        )
                        break
            except discord.HTTPException as error:
                log_exception(
                    "TICKET",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context="Ticket label control message lookup failed",
                )
        if control_message and control_message.embeds:
            embed = discord.Embed.from_dict(control_message.embeds[0].to_dict())
            apply_ticket_label(embed, selected_label)
            try:
                await control_message.edit(embed=embed)
            except discord.HTTPException as error:
                log_exception(
                    "TICKET",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context="Ticket label main embed update failed",
                )
        log_interaction(
            interaction.user,
            "labelz",
            interaction.channel,
            details=f"Ticket Label: {selected_label or 'Cleared'}",
        )
        log_ticket(
            "Ticket Label Updated",
            interaction.channel,
            interaction.user,
            details=f"Label: {selected_label or 'Not assigned'}",
        )
        result = discord.Embed(
            title="Ticket Classification Updated",
            description="The ticket label has been saved and synchronized with the main ticket panel.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        result.add_field(
            name="Ticket Label",
            value=selected_label or "Not assigned",
            inline=True,
        )
        result.add_field(
            name="Updated By", value=interaction.user.mention, inline=True
        )
        result.add_field(
            name="Ticket UUID", value=f"`{ticket['uuid']}`", inline=False
        )
        result.set_footer(text=f"{config.BOT_NAME} | Ticket Classification")
        await interaction.followup.send(embed=result)


async def setup(bot):
    bot.add_view(TicketPanel())
    bot.add_view(TicketButtons())
    bot.add_view(ClosedTicketButtons())
    await bot.add_cog(TicketControlRecovery(bot))
