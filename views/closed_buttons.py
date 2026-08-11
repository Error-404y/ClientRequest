import discord
import asyncio
import os
import config
from discord.ui import View, Button
from utils.permissions import (
    is_staff,
    is_owner
)
from utils.database import reopen_ticket, get_ticket_record, get_ticket_owner
from utils.embeds import (
    ticket_reopened,
    error
)
from cogs.transcript import create_transcript
from utils.logger import log_interaction, log_ticket, log_perm

class ClosedTicketButtons(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Reopen Ticket",
        style=discord.ButtonStyle.success,
        custom_id="zer_reopen"
    )
    async def reopen(self, interaction, button):
        log_interaction(interaction.user, "zer_reopen", interaction.channel)
        if not is_staff(interaction.user):
            log_ticket("Reopen Rejected (Not Staff)", interaction.channel, interaction.user)
            await interaction.response.send_message(
                embed=error("You do not have permission to reopen this ticket."),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        # Disable buttons to prevent double-clicks
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        channel = interaction.channel

        # Retrieve ticket owner ID safely from topic (e.g. ticket_owner:1234 or ticket_owner:1234 | claimed_by:5678)
        user_id = None
        member = None
        if channel.topic and "ticket_owner:" in channel.topic:
            try:
                topic_part = channel.topic.split("|")[0].strip()
                user_id = int(topic_part.replace("ticket_owner:", "").strip())
            except ValueError:
                pass

        # Database fallback
        if user_id is None:
            try:
                user_id = await get_ticket_owner(channel.id)
            except Exception:
                pass

        if user_id:
            member = interaction.guild.get_member(user_id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(user_id)
                except discord.HTTPException:
                    pass

            if member:
                try:
                    await channel.set_permissions(
                        member,
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True
                    )
                    log_perm(channel, member, "Restored view_channel=True, send_messages=True, read_message_history=True")
                except Exception as e:
                    print(f"Failed to restore permissions for ticket owner: {str(e)}")

        # Reopen in database
        await reopen_ticket(channel.id)

        # Move channel back and rename in background
        async def perform_background_reopen():
            edit_kwargs = {}
            category = interaction.guild.get_channel(config.TICKET_CATEGORY_ID)
            if category:
                edit_kwargs["category"] = category
            
            new_name = channel.name.replace("closed-", "", 1)
            if new_name != channel.name:
                edit_kwargs["name"] = new_name

            if edit_kwargs:
                try:
                    await channel.edit(**edit_kwargs)
                    log_ticket("Restored Channel Properties", channel, interaction.user, details=f"Moved to category {category.name if category else 'Default'}, Name: {new_name}")
                except Exception as e:
                    print(f"Failed to restore channel properties during reopen: {str(e)}")

        asyncio.create_task(perform_background_reopen())

        # Send reopen confirmation embed
        await interaction.followup.send(
            embed=ticket_reopened(applicant=member)
        )

        # Send reopen report to owner
        from utils.logger import ticket_reopen_report
        ticket_reopen_report(channel, interaction.user, user_id, interaction.client)

        # Re-inject the original close button view so the ticket can be closed again
        # Fetch ticket details to reconstruct the view with appropriate form link
        ticket_record = await get_ticket_record(channel.id)
        from views.ticket_buttons import TicketButtons
        view = TicketButtons()

        if ticket_record:
            application = ticket_record.get("application")
            form_url = config.MODERATOR_FORM if application == "Moderator Application" else config.UPLOADER_FORM
            form_button = discord.ui.Button(
                label="Application Form",
                style=discord.ButtonStyle.link,
                url=form_url
            )
            view.add_item(form_button)

            # Check if ticket was previously claimed and update the Claim Button status
            claimed_by = ticket_record.get("claimed_by")
            if claimed_by:
                for item in view.children:
                    if getattr(item, "custom_id", None) == "zer_claim":
                        item.disabled = True
                        try:
                            claimant = interaction.guild.get_member(claimed_by)
                            if claimant is None:
                                claimant = await interaction.guild.fetch_member(claimed_by)
                            if claimant:
                                item.label = f"Claimed by {claimant.display_name}"
                                item.style = discord.ButtonStyle.secondary
                        except Exception:
                            item.label = "Claimed"
                            item.style = discord.ButtonStyle.secondary

        await channel.send(
            view=view
        )

        # Clean up the closed buttons message since the ticket is now active again
        try:
            await interaction.message.delete()
        except Exception:
            pass

    @discord.ui.button(
        label="Generate Transcript",
        style=discord.ButtonStyle.primary,
        custom_id="zer_transcript"
    )
    async def transcript(self, interaction, button):
        log_interaction(interaction.user, "zer_transcript", interaction.channel)
        if not is_staff(interaction.user):
            log_ticket("Transcript Rejected (Not Staff)", interaction.channel, interaction.user)
            await interaction.response.send_message(
                embed=error("You do not have permission to generate transcripts."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Disable buttons temporarily during transcript generation
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        try:
            file_path = await create_transcript(interaction.channel)
            await interaction.followup.send(
                content="Transcript successfully generated. Download below:",
                file=discord.File(file_path),
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                embed=error(f"Failed to generate transcript: {str(e)}"),
                ephemeral=True
            )

        # Re-enable buttons on this view
        for item in self.children:
            item.disabled = False
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(
        label="Delete Channel",
        style=discord.ButtonStyle.danger,
        custom_id="zer_delete"
    )
    async def delete(self, interaction, button):
        log_interaction(interaction.user, "zer_delete", interaction.channel)
        if not is_owner(interaction.user):
            log_ticket("Delete Rejected (Not Owner)", interaction.channel, interaction.user)
            await interaction.response.send_message(
                embed=error("Only owners can delete ticket channels permanently."),
                ephemeral=True
            )
            return

        # Defer and disable
        await interaction.response.defer(ephemeral=True)
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

        await interaction.followup.send(
            content="Channel deletion initiated. Deleting channel in 5 seconds...",
            ephemeral=True
        )

        # Capture channel details before deletion
        channel_name = interaction.channel.name
        user_id = None
        if interaction.channel.topic and "ticket_owner:" in interaction.channel.topic:
            try:
                topic_part = interaction.channel.topic.split("|")[0].strip()
                user_id = int(topic_part.replace("ticket_owner:", "").strip())
            except ValueError:
                pass

        # Database fallback
        if user_id is None:
            try:
                user_id = await get_ticket_owner(interaction.channel.id)
            except Exception:
                pass

        log_ticket("Deletion Scheduled (5s)", interaction.channel, interaction.user)

        await asyncio.sleep(5)
        
        try:
            await interaction.channel.delete()
            from utils.logger import ticket_delete_report
            ticket_delete_report(channel_name, interaction.user, user_id, interaction.client)
        except Exception as e:
            print(f"Failed to delete channel: {str(e)}")
