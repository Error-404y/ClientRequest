import asyncio
from datetime import datetime

import discord
import pytz
from discord.ui import Select

import config
from utils.database import (
    auto_assign_ticket,
    create_ticket_record,
    get_next_ticket_number,
    get_staff_availability,
    mark_ticket_deleted,
    set_ticket_control_message,
)
from utils.embeds import error as error_embed
from utils.embeds import ticket_claimed_dm, ticket_created
from utils.logger import (
    log_dm,
    log_exception,
    log_interaction,
    log_perm,
    log_ticket,
    ticket_claim_report,
    ticket_report,
)
from utils.permissions import is_staff
from views.base import ReliableView
from views.ticket_buttons import TicketButtons

timezone = pytz.timezone(config.TIMEZONE)


class ApplicationDropdown(Select):
    def __init__(self, options_list=None):

        if options_list is None:
            options_list = [
                "Partnership",
                "Player Reports",
                "Billing/Issues",
                "Moderator Application",
                "Uploader Application",
            ]

        options = [discord.SelectOption(label=opt, value=opt) for opt in options_list]

        super().__init__(
            placeholder="Select ticket type",
            options=options,
            custom_id="zer_application_dropdown",
        )

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                embed=error_embed("Tickets can only be created inside a server."),
                ephemeral=True,
            )
            return
        locks = getattr(interaction.client, "ticket_creation_locks", None)
        if locks is None:
            locks = {}
            interaction.client.ticket_creation_locks = locks
        lock = locks.setdefault((guild.id, interaction.user.id), asyncio.Lock())
        async with lock:
            await self.create_ticket(interaction)

    async def create_ticket(self, interaction):
        user = interaction.user
        guild = interaction.guild

        guild_id = guild.id

        application = self.values[0]

        try:
            guild_config = config.get_guild_config(guild_id)
        except ValueError:
            await interaction.followup.send(
                embed=error_embed("This server is not configured."), ephemeral=True
            )
            return
        ticket_category_id = guild_config["TICKET_CATEGORY_ID"]

        log_interaction(
            user,
            "zer_application_dropdown",
            interaction.channel,
            details=(f"Selected Application: {application}"),
        )

        for channel in guild.text_channels:
            if channel.category_id != ticket_category_id:
                continue

            if channel.topic and channel.topic.startswith(f"ticket_owner:{user.id}"):
                log_ticket("Creation Aborted (Duplicate Ticket)", channel, user)

                await interaction.followup.send(
                    embed=error_embed("You already have an open application ticket."),
                    ephemeral=True,
                )

                return

        form = None

        if application == "Moderator Application":
            prefix = "mod"
            form = config.MODERATOR_FORM

        elif application == "Uploader Application":
            prefix = "uploader"
            form = config.UPLOADER_FORM

        elif application == "Partnership":
            prefix = "partnership"

        elif application == "Player Reports":
            prefix = "report"

        elif application == "Billing/Issues" or application == "Issues":
            prefix = "issues"

        elif application == "Questions":
            prefix = "question"

        else:
            prefix = "ticket"

        category = guild.get_channel(ticket_category_id)

        if category is None:
            await interaction.followup.send(
                embed=error_embed(
                    "The ticket category could not be resolved. Contact administration."
                ),
                ephemeral=True,
            )

            return

        number = await get_next_ticket_number(guild_id)

        channel_name = f"{prefix}-{number:03d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }

        owner_roles = config.get_owner_roles(guild_id)

        for role_id in owner_roles:
            role = guild.get_role(role_id)

            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_channels=True,
                    read_message_history=True,
                )

        mod_role_id = config.get_mod_role(guild_id)

        mod_role = guild.get_role(mod_role_id)

        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

        trial_mod_role_id = config.get_trial_mod_role(guild_id)

        trial_mod_role = guild.get_role(trial_mod_role_id)

        if trial_mod_role:
            overwrites[trial_mod_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"ticket_owner:{user.id}",
            )

            log_ticket(
                "Text Channel Created",
                channel,
                user,
                details=(f"Category: {category.name}"),
            )

            log_perm(
                channel,
                user,
                ("view_channel=True, send_messages=True, read_message_history=True"),
            )

        except discord.Forbidden as exc:
            reference = log_exception(
                "PERMISSION",
                exc,
                guild=guild,
                channel=interaction.channel,
                user=user,
                context="Ticket channel creation was forbidden",
            )

            await interaction.followup.send(
                embed=error_embed(
                    "I do not have sufficient permissions "
                    f"to create text channels on this server. Error reference: `{reference}`"
                ),
                ephemeral=True,
            )

            return

        except Exception as exc:
            reference = log_exception(
                "TICKET",
                exc,
                guild=guild,
                channel=interaction.channel,
                user=user,
                context="Ticket channel creation failed",
            )

            await interaction.followup.send(
                embed=error_embed(
                    "An unexpected error occurred during ticket channel creation. "
                    f"Error reference: `{reference}`"
                ),
                ephemeral=True,
            )

            return

        try:
            ticket_uuid = await create_ticket_record(
                channel.id,
                guild.id,
                user.id,
                application,
                datetime.now(timezone).isoformat(),
            )

        except Exception as exc:
            try:
                await channel.delete(reason=("Ticket database record creation failed"))

            except discord.HTTPException as delete_error:
                log_exception(
                    "TICKET",
                    delete_error,
                    guild=guild,
                    channel=channel,
                    user=user,
                    context="Failed to remove orphaned ticket channel after database error",
                )

            await interaction.followup.send(
                embed=error_embed(
                    "The ticket could not be registered "
                    "in the database. Please contact "
                    "administration."
                ),
                ephemeral=True,
            )

            log_ticket(
                "Ticket Database Creation Failed", channel, user, details=str(exc)
            )

            log_exception(
                "DATABASE",
                exc,
                guild=guild,
                channel=channel,
                user=user,
                context="Ticket database record creation failed",
            )

            return

        assigned_id = None
        if guild_config.get("AUTO_ASSIGN_TICKETS"):
            availability = await get_staff_availability(guild.id)
            candidates = []
            for record in availability:
                member = guild.get_member(record["user_id"])
                if member and record["status"] == "Available" and is_staff(member):
                    candidates.append(member.id)
            assigned_id = await auto_assign_ticket(
                channel.id,
                guild.id,
                candidates,
                datetime.now(timezone).isoformat(),
            )

        view = TicketButtons(claimed_by=assigned_id)

        if form:
            form_button = discord.ui.Button(
                label="Application Form", style=discord.ButtonStyle.link, url=form
            )

            view.add_item(form_button)

        try:
            control_message = await channel.send(
                content=user.mention,
                embed=ticket_created(user, application, form, ticket_uuid),
                view=view,
            )
            await set_ticket_control_message(channel.id, control_message.id)
            if assigned_id:
                assigned_member = guild.get_member(assigned_id)
                assignment_embed = discord.Embed(
                    title="Ticket Automatically Assigned",
                    description="The assignment system selected an available staff member with the lowest active workload.",
                    color=discord.Color.green(),
                    timestamp=discord.utils.utcnow(),
                )
                assignment_embed.add_field(
                    name="Assigned Staff",
                    value=(
                        f"{assigned_member.mention}\n`{assigned_id}`"
                        if assigned_member
                        else f"User ID: `{assigned_id}`"
                    ),
                    inline=True,
                )
                assignment_embed.add_field(
                    name="Status", value="Assigned and under review", inline=True
                )
                assignment_embed.set_footer(
                    text=f"{config.BOT_NAME} | Automatic Assignment"
                )
                await channel.send(embed=assignment_embed)
                if assigned_member:
                    ticket_claim_report(
                        channel, assigned_member, user.id, interaction.client
                    )
                    try:
                        await user.send(
                            embed=ticket_claimed_dm(
                                guild,
                                channel,
                                assigned_member,
                                interaction.client.user,
                            )
                        )
                        log_dm(user, "Automatic Ticket Assignment", success=True)
                    except discord.Forbidden:
                        log_dm(
                            user,
                            "Automatic Ticket Assignment",
                            success=False,
                            error_detail="Direct Messages Disabled",
                        )
                    except discord.HTTPException as dm_error:
                        log_dm(
                            user,
                            "Automatic Ticket Assignment",
                            success=False,
                            error_detail=str(dm_error),
                        )
                        log_exception(
                            "DM",
                            dm_error,
                            guild=guild,
                            channel=channel,
                            user=user,
                            context="Failed to deliver automatic ticket assignment notice",
                        )
        except discord.HTTPException as exc:
            await mark_ticket_deleted(channel.id)
            try:
                await channel.delete(reason="Ticket initialization failed")
            except discord.HTTPException as delete_error:
                log_exception(
                    "TICKET",
                    delete_error,
                    guild=guild,
                    channel=channel,
                    user=user,
                    context="Failed to remove ticket channel after initialization error",
                )
            reference = log_exception(
                "TICKET",
                exc,
                guild=guild,
                channel=channel,
                user=user,
                context="Failed to publish initial ticket message",
            )
            await interaction.followup.send(
                embed=error_embed(
                    f"The ticket could not be initialized. Error reference: `{reference}`"
                ),
                ephemeral=True,
            )
            return

        ticket_report(user, application, channel, bot=interaction.client)

        created_embed = discord.Embed(
            title="Ticket Created",
            description=f"Your private support ticket is ready: {channel.mention}",
            color=discord.Color.green(),
        )
        created_embed.add_field(
            name="Ticket UUID", value=f"`{ticket_uuid}`", inline=False
        )
        created_embed.set_footer(text=f"{config.BOT_NAME} | Support Portal")
        await interaction.followup.send(embed=created_embed, ephemeral=True)


class TicketPanel(ReliableView):
    def __init__(self, options_list=None):

        super().__init__(timeout=None)

        self.add_item(ApplicationDropdown(options_list=options_list))
