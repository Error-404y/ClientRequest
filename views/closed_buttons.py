import asyncio

import discord

import config
from cogs.transcript import create_transcript
from utils.database import (
    close_ticket,
    get_ticket_owner,
    get_ticket_record,
    mark_ticket_deleted,
    reopen_ticket,
    set_ticket_control_message,
)
from utils.embeds import error as error_embed
from utils.embeds import success as success_embed
from utils.embeds import ticket_reopened
from utils.logger import log_exception, log_interaction, log_perm, log_ticket
from utils.permissions import can_setup, is_staff
from views.base import ReliableView


class ClosedTicketButtons(ReliableView):
    def __init__(self):
        super().__init__(timeout=None)

    async def restore_controls(self, interaction):
        for item in self.children:
            item.disabled = False
        if interaction.message:
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException as error:
                log_exception(
                    "VIEW",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context="Failed to restore archived ticket controls",
                )

    async def on_error(self, interaction, error, item):
        await self.restore_controls(interaction)
        await super().on_error(interaction, error, item)

    @discord.ui.button(
        label="Reopen Ticket", style=discord.ButtonStyle.success, custom_id="zer_reopen"
    )
    async def reopen(self, interaction, button):
        log_interaction(interaction.user, "zer_reopen", interaction.channel)
        if not is_staff(interaction.user):
            log_ticket(
                "Reopen Rejected (Not Staff)", interaction.channel, interaction.user
            )
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to reopen this ticket."),
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to disable closed ticket controls before reopening",
            )

        channel = interaction.channel
        ticket_record = await get_ticket_record(channel.id)
        reopened = await reopen_ticket(channel.id)
        if not reopened:
            await self.restore_controls(interaction)
            await interaction.followup.send(
                embed=error_embed(
                    "This ticket is already open or is no longer available."
                ),
                ephemeral=True,
            )
            return
        user_id = ticket_record["user_id"] if ticket_record else None
        member = None
        control_message = None
        try:
            if user_id:
                member = interaction.guild.get_member(user_id)
                if member is None:
                    try:
                        member = await interaction.guild.fetch_member(user_id)
                    except discord.NotFound:
                        member = None
                if member:
                    await channel.set_permissions(
                        member,
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                    )
                    log_perm(
                        channel,
                        member,
                        "Restored view_channel=True, send_messages=True, read_message_history=True",
                    )

            category = interaction.guild.get_channel(
                config.get_ticket_category_id(interaction.guild.id)
            )
            if category:
                await channel.edit(category=category)
                log_ticket(
                    "Restored Channel Properties",
                    channel,
                    interaction.user,
                    details=f"Moved to category {category.name}",
                )

            from views.ticket_buttons import TicketButtons

            current_record = await get_ticket_record(channel.id)
            claimed_by = current_record.get("claimed_by") if current_record else None
            view = TicketButtons(claimed_by=claimed_by)
            application = current_record.get("application") if current_record else None
            form_url = None
            if application == "Moderator Application":
                form_url = config.MODERATOR_FORM
            elif application == "Uploader Application":
                form_url = config.UPLOADER_FORM
            if form_url:
                view.add_item(
                    discord.ui.Button(
                        label="Application Form",
                        style=discord.ButtonStyle.link,
                        url=form_url,
                    )
                )

            controls_embed = discord.Embed(
                title="Ticket Controls Restored",
                description="This ticket is active again. Authorized staff can manage its assignment, priority, and lifecycle using the controls below.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            controls_embed.set_footer(
                text=f"{config.BOT_NAME} | Active Ticket Controls"
            )
            control_message = await channel.send(embed=controls_embed, view=view)
            await set_ticket_control_message(channel.id, control_message.id)
        except Exception as error:
            if control_message:
                try:
                    await control_message.delete()
                except discord.HTTPException as cleanup_error:
                    log_exception(
                        "TICKET",
                        cleanup_error,
                        guild=interaction.guild,
                        channel=channel,
                        user=interaction.user,
                        context="Failed to remove incomplete reopened ticket controls",
                    )
            await close_ticket(
                channel.id,
                ticket_record.get("closed_at")
                if ticket_record
                else discord.utils.utcnow().isoformat(),
                ticket_record.get("closed_by") if ticket_record else None,
                ticket_record.get("close_reason")
                if ticket_record
                else "Reopen recovery",
            )
            if member:
                try:
                    await channel.set_permissions(
                        member,
                        view_channel=False,
                        send_messages=False,
                    )
                except discord.HTTPException as cleanup_error:
                    log_exception(
                        "PERMISSION",
                        cleanup_error,
                        guild=interaction.guild,
                        channel=channel,
                        user=member,
                        context="Failed to restore closed ticket permissions after reopen rollback",
                    )
            archive = interaction.guild.get_channel(
                config.get_archive_category_id(interaction.guild.id)
            )
            if archive:
                try:
                    await channel.edit(category=archive)
                except discord.HTTPException as cleanup_error:
                    log_exception(
                        "TICKET",
                        cleanup_error,
                        guild=interaction.guild,
                        channel=channel,
                        user=interaction.user,
                        context="Failed to restore archive category after reopen rollback",
                    )
            await self.restore_controls(interaction)
            reference = log_exception(
                "TICKET",
                error,
                guild=interaction.guild,
                channel=channel,
                user=interaction.user,
                context="Ticket reopen rolled back",
            )
            await interaction.followup.send(
                embed=error_embed(
                    f"The ticket could not be reopened and was safely restored to its closed state. Error reference: `{reference}`"
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(embed=ticket_reopened(applicant=member))

        from utils.logger import ticket_reopen_report

        ticket_reopen_report(channel, interaction.user, user_id, interaction.client)

        try:
            await interaction.message.delete()
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=channel,
                user=interaction.user,
                context="Failed to remove obsolete closed ticket controls",
            )

    @discord.ui.button(
        label="Generate Transcript",
        style=discord.ButtonStyle.primary,
        custom_id="zer_transcript",
    )
    async def transcript(self, interaction, button):
        log_interaction(interaction.user, "zer_transcript", interaction.channel)
        if not is_staff(interaction.user):
            log_ticket(
                "Transcript Rejected (Not Staff)", interaction.channel, interaction.user
            )
            await interaction.response.send_message(
                embed=error_embed(
                    "You do not have permission to generate transcripts."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to disable transcript controls",
            )

        try:
            file_path = await create_transcript(interaction.channel)
            await interaction.followup.send(
                embed=success_embed(
                    "The transcript was generated successfully and is attached below."
                ),
                file=discord.File(file_path),
                ephemeral=True,
            )
        except Exception as error:
            reference = log_exception(
                "TRANSCRIPT",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Manual transcript generation failed",
            )
            await interaction.followup.send(
                embed=error_embed(
                    f"Failed to generate transcript. Error reference: `{reference}`"
                ),
                ephemeral=True,
            )

        for item in self.children:
            item.disabled = False
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to restore transcript controls",
            )

    @discord.ui.button(
        label="Delete Channel", style=discord.ButtonStyle.danger, custom_id="zer_delete"
    )
    async def delete(self, interaction, button):
        log_interaction(interaction.user, "zer_delete", interaction.channel)
        if not can_setup(interaction.user):
            log_ticket(
                "Delete Rejected (Not Owner)", interaction.channel, interaction.user
            )
            await interaction.response.send_message(
                embed=error_embed(
                    "Only the server owner or an authorized administrator can delete ticket channels permanently."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to disable ticket controls before deletion",
            )

        await interaction.followup.send(
            embed=success_embed(
                "Channel deletion was authorized and will begin in 5 seconds."
            ),
            ephemeral=True,
        )

        channel_name = interaction.channel.name
        channel_id = interaction.channel.id
        user_id = None
        if interaction.channel.topic and "ticket_owner:" in interaction.channel.topic:
            try:
                topic_part = interaction.channel.topic.split("|")[0].strip()
                user_id = int(topic_part.replace("ticket_owner:", "").strip())
            except ValueError:
                user_id = None

        if user_id is None:
            try:
                user_id = await get_ticket_owner(interaction.channel.id)
            except Exception as error:
                log_exception(
                    "DATABASE",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context="Failed to resolve ticket owner before deletion",
                )

        log_ticket("Deletion Scheduled (5s)", interaction.channel, interaction.user)

        await asyncio.sleep(5)

        try:
            await interaction.channel.delete()
            await mark_ticket_deleted(channel_id)
            from utils.logger import ticket_delete_report

            ticket_delete_report(
                channel_name, interaction.user, user_id, interaction.client
            )
        except Exception as error:
            reference = log_exception(
                "TICKET",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Ticket channel deletion failed",
            )
            await interaction.followup.send(
                embed=error_embed(
                    f"The channel could not be deleted. Error reference: `{reference}`"
                ),
                ephemeral=True,
            )
