import discord
from discord.ui import Select, View
from datetime import datetime
import pytz
import config
from utils.database import (
    create_ticket_record,
    get_next_ticket_number
)
from utils.embeds import (
    ticket_created,
    error
)
from utils.logger import ticket_report, log_interaction, log_ticket, log_perm
from views.ticket_buttons import TicketButtons

timezone = pytz.timezone("Europe/Berlin")

class ApplicationDropdown(Select):
    def __init__(self, options_list=None):
        if options_list is None:
            options_list = ["Partnership", "Player Reports", "Billing/Issues", "Moderator Application", "Uploader Application"]
        
        options = [
            discord.SelectOption(label=opt, value=opt) for opt in options_list
        ]

        super().__init__(
            placeholder="Select ticket type",
            options=options,
            custom_id="zer_application_dropdown"
        )

    async def callback(self, interaction):
        # Defer immediately to prevent interaction timeouts during channel creation
        await interaction.response.defer(ephemeral=True)

        user = interaction.user
        guild = interaction.guild
        guild_id = guild.id if guild else config.GUILD_ID
        application = self.values[0]

        ticket_category_id = config.get_ticket_category_id(guild_id)

        log_interaction(user, "zer_application_dropdown", interaction.channel, details=f"Selected Application: {application}")

        # Strict duplicate check: scan for existing channels with the user's ID in name or topic
        for channel in guild.text_channels:
            if channel.category_id == ticket_category_id:
                if channel.topic == f"ticket_owner:{user.id}" or str(user.id) in channel.name:
                    log_ticket("Creation Aborted (Duplicate Ticket)", channel, user)
                    await interaction.followup.send(
                        embed=error("You already have an open application ticket."),
                        ephemeral=True
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
                embed=error("The ticket category could not be resolved. Contact administration."),
                ephemeral=True
            )
            return

        number = await get_next_ticket_number()
        channel_name = f"{prefix}-{number:03d}"

        # Initialize overrides
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )
        }

        # Add owner user permissions (SETUP_USER_ID)
        if config.SETUP_USER_ID:
            setup_member = guild.get_member(config.SETUP_USER_ID)
            if setup_member:
                overwrites[setup_member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_channels=True,
                    read_message_history=True
                )

        # Add owners permissions
        owner_roles = config.get_owner_roles(guild_id)
        for role_id in owner_roles:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    manage_channels=True,
                    read_message_history=True
                )

        # Add moderators permissions
        mod_role_id = config.get_mod_role(guild_id)
        mod_role = guild.get_role(mod_role_id)
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        # Add trial moderators permissions
        trial_mod_role_id = config.get_trial_mod_role(guild_id)
        trial_mod_role = guild.get_role(trial_mod_role_id)
        if trial_mod_role:
            overwrites[trial_mod_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        try:
            # Create the ticket text channel
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"ticket_owner:{user.id}"
            )
            log_ticket("Text Channel Created", channel, user, details=f"Category: {category.name}")
            log_perm(channel, user, "view_channel=True, send_messages=True, read_message_history=True")
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error("I do not have sufficient permissions to create text channels on this server."),
                ephemeral=True
            )
            return
        except Exception as e:
            await interaction.followup.send(
                embed=error(f"An unexpected error occurred during ticket channel creation: {str(e)}"),
                ephemeral=True
            )
            return

        # Write to database record
        ticket_uuid = await create_ticket_record(
            channel.id,
            guild.id,
            user.id,
            application,
            datetime.now(timezone).isoformat()
        )

        view = TicketButtons()
        if form:
            form_button = discord.ui.Button(
                label="Application Form",
                style=discord.ButtonStyle.link,
                url=form
            )
            view.add_item(form_button)

        await channel.send(
            content=user.mention,
            embed=ticket_created(user, application, form, ticket_uuid),
            view=view
        )

        ticket_report(user, application, channel, bot=interaction.client)

        await interaction.followup.send(
            f"Your ticket has been created: {channel.mention}",
            ephemeral=True
        )

class TicketPanel(View):
    def __init__(self, options_list=None):
        super().__init__(timeout=None)
        self.add_item(ApplicationDropdown(options_list=options_list))
