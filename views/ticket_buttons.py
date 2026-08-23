from datetime import datetime

import discord
import pytz

import config
from utils.database import get_ticket_owner, toggle_ticket_claim
from utils.embeds import error as error_embed
from utils.embeds import success as success_embed
from utils.embeds import ticket_claimed_dm
from utils.logger import (
    log_dm,
    log_exception,
    log_interaction,
    log_ticket,
    ticket_claim_report,
)
from utils.permissions import is_staff
from utils.ticket_actions import close_ticket_channel
from views.base import ReliableModal, ReliableView

timezone = pytz.timezone(config.TIMEZONE)


class CloseTicketModal(ReliableModal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Reason for closing",
        placeholder="Enter the reason for closing this ticket...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    def __init__(self, original_view):
        super().__init__()
        self.original_view = original_view

    async def on_submit(self, interaction: discord.Interaction):
        log_interaction(
            interaction.user,
            "CloseTicketModal",
            interaction.channel,
            details=f"Reason: {self.reason.value}",
        )
        await interaction.response.defer()

        for item in self.original_view.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self.original_view)
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to disable ticket controls before closing",
            )

        try:
            closed = await close_ticket_channel(
                channel=interaction.channel,
                moderator=interaction.user,
                reason=self.reason.value,
                bot=interaction.client,
            )
        except Exception:
            for item in self.original_view.children:
                item.disabled = False
            try:
                await interaction.message.edit(view=self.original_view)
            except discord.HTTPException as restore_error:
                log_exception(
                    "VIEW",
                    restore_error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context="Failed to restore ticket controls after close failure",
                )
            raise
        if not closed:
            for item in self.original_view.children:
                item.disabled = False
            try:
                await interaction.message.edit(view=self.original_view)
            except discord.HTTPException as restore_error:
                log_exception(
                    "VIEW",
                    restore_error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context="Failed to restore ticket controls after duplicate close",
                )
            await interaction.followup.send(
                embed=error_embed(
                    "This ticket is already closed or is no longer available."
                ),
                ephemeral=True,
            )


class PrioritySelectionView(ReliableView):
    def __init__(self, original_channel):
        super().__init__(timeout=60)
        self.original_channel = original_channel

    @discord.ui.button(
        label="Low", style=discord.ButtonStyle.success, custom_id="priority_low"
    )
    async def set_low(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.update_priority(interaction, "Low")

    @discord.ui.button(
        label="Medium", style=discord.ButtonStyle.primary, custom_id="priority_medium"
    )
    async def set_medium(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.update_priority(interaction, "Medium")

    @discord.ui.button(
        label="High", style=discord.ButtonStyle.danger, custom_id="priority_high"
    )
    async def set_high(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.update_priority(interaction, "High")

    async def update_priority(self, interaction: discord.Interaction, priority: str):
        log_interaction(
            interaction.user,
            f"priority_{priority.lower()}",
            self.original_channel,
            details=f"New Priority: {priority}",
        )
        from utils.database import set_ticket_priority

        updated = await set_ticket_priority(self.original_channel.id, priority)
        if not updated:
            await interaction.response.edit_message(
                content=None,
                embed=error_embed(
                    "This ticket is no longer open, so its priority was not changed."
                ),
                view=None,
            )
            return

        await interaction.response.edit_message(
            content=None,
            embed=success_embed(f"Ticket priority was set to **{priority}**."),
            view=None,
        )

        embed = discord.Embed(
            title="Ticket Priority Updated",
            description="This ticket's priority has been updated.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="New Priority", value=priority, inline=True)
        embed.add_field(name="Updated By", value=interaction.user.mention, inline=True)
        embed.set_footer(text=config.BOT_NAME)
        await self.original_channel.send(embed=embed)


class TicketButtons(ReliableView):
    def __init__(self, claimed_by=None):
        super().__init__(timeout=None)
        claim_button = discord.utils.get(self.children, custom_id="zer_claim")
        if claim_button:
            claim_button.label = "Unclaim Ticket" if claimed_by else "Claim Ticket"
            claim_button.style = (
                discord.ButtonStyle.secondary
                if claimed_by
                else discord.ButtonStyle.primary
            )

    @discord.ui.button(
        label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="zer_claim"
    )
    async def claim(self, interaction, button):
        log_interaction(interaction.user, "zer_claim", interaction.channel)
        if not is_staff(interaction.user):
            log_ticket(
                "Claim Rejected (Not Staff)", interaction.channel, interaction.user
            )
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to claim this ticket."),
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        changed_at = datetime.now(timezone).isoformat()
        result = await toggle_ticket_claim(
            interaction.channel.id, interaction.user.id, changed_at
        )
        if result["status"] == "cooldown":
            await interaction.followup.send(
                embed=error_embed(
                    f"Ticket assignment controls are temporarily locked. Try again in {result['remaining']} second(s)."
                ),
                ephemeral=True,
            )
            return
        if result["status"] in {"not_found", "not_open"}:
            await interaction.followup.send(
                embed=error_embed("This channel is not an open registered ticket."),
                ephemeral=True,
            )
            return

        channel = interaction.channel
        owner_id = None
        if channel.topic and "ticket_owner:" in channel.topic:
            parts = channel.topic.split("|")
            owner_part = parts[0].strip()
            try:
                owner_id = int(owner_part.replace("ticket_owner:", "").strip())
            except ValueError:
                owner_id = None
        if owner_id is None:
            try:
                owner_id = await get_ticket_owner(channel.id)
            except Exception as error:
                log_exception(
                    "DATABASE",
                    error,
                    guild=interaction.guild,
                    channel=channel,
                    user=interaction.user,
                    context="Failed to resolve ticket owner after assignment change",
                )

        claimed = result["status"] == "claimed"
        updated_view = TicketButtons(claimed_by=result["claimed_by"])
        for row in interaction.message.components:
            for component in getattr(row, "children", []):
                if getattr(component, "url", None):
                    updated_view.add_item(
                        discord.ui.Button(
                            label=component.label or "Open Link",
                            style=discord.ButtonStyle.link,
                            url=component.url,
                        )
                    )

        try:
            await interaction.message.edit(view=updated_view)
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=channel,
                user=interaction.user,
                context="Failed to update claimed ticket controls",
            )

        embed = discord.Embed(
            title="Ticket Assignment Updated",
            description=(
                "Responsibility for this ticket has been accepted."
                if claimed
                else "The current assignment has been released and the ticket is available to staff."
            ),
            color=discord.Color.green() if claimed else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Action",
            value="Claimed" if claimed else "Unclaimed",
            inline=True,
        )
        embed.add_field(
            name="Performed By",
            value=f"{interaction.user.mention}\n`{interaction.user.id}`",
            inline=True,
        )
        if not claimed:
            previous_id = result.get("previous_claimed_by")
            previous = (
                interaction.guild.get_member(previous_id) if previous_id else None
            )
            embed.add_field(
                name="Previous Assignment",
                value=(
                    f"{previous.mention}\n`{previous_id}`"
                    if previous
                    else f"User ID: `{previous_id}`"
                ),
                inline=True,
            )
        embed.add_field(
            name="Current Status",
            value="Assigned and under review"
            if claimed
            else "Awaiting staff assignment",
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Ticket Assignment Audit")
        await interaction.followup.send(embed=embed)

        log_ticket(
            "Ticket Claimed" if claimed else "Ticket Unclaimed",
            channel,
            interaction.user,
            details=f"Previous assignment: {result.get('previous_claimed_by')}",
        )

        if claimed:
            ticket_claim_report(channel, interaction.user, owner_id, interaction.client)

        if claimed and owner_id:
            try:
                owner = interaction.guild.get_member(owner_id)
                if owner is None:
                    owner = await interaction.guild.fetch_member(owner_id)

                if owner:
                    await owner.send(
                        embed=ticket_claimed_dm(
                            interaction.guild,
                            channel,
                            interaction.user,
                            interaction.client.user,
                        )
                    )
                    log_dm(owner, "Ticket Claimed Notice", success=True)
            except discord.Forbidden:
                log_dm(
                    owner_id,
                    "Ticket Claimed Notice",
                    success=False,
                    error_detail="Direct Messages Disabled",
                )
            except Exception as error:
                log_dm(
                    owner_id,
                    "Ticket Claimed Notice",
                    success=False,
                    error_detail=str(error),
                )
                log_exception(
                    "DM",
                    error,
                    guild=interaction.guild,
                    channel=channel,
                    user=owner_id,
                    context="Failed to notify applicant that ticket was claimed",
                )

    @discord.ui.button(
        label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="zer_close"
    )
    async def close(self, interaction, button):
        log_interaction(interaction.user, "zer_close", interaction.channel)
        if not is_staff(interaction.user):
            log_ticket(
                "Close Rejected (Not Staff)", interaction.channel, interaction.user
            )
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to close this ticket."),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(CloseTicketModal(self))

    @discord.ui.button(
        label="Set Priority",
        style=discord.ButtonStyle.secondary,
        custom_id="zer_priority",
    )
    async def set_priority(self, interaction, button):
        log_interaction(interaction.user, "zer_priority", interaction.channel)
        if not is_staff(interaction.user):
            log_ticket(
                "Set Priority Rejected (Not Staff)",
                interaction.channel,
                interaction.user,
            )
            await interaction.response.send_message(
                embed=error_embed(
                    "You do not have permission to change ticket priority."
                ),
                ephemeral=True,
            )
            return

        view = PrioritySelectionView(interaction.channel)
        embed = discord.Embed(
            title="Select Ticket Priority",
            description="Choose the priority level that best reflects the required response.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Ticket Priority")
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )
