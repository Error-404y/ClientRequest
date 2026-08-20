from datetime import datetime

import discord
import pytz
from discord import app_commands
from discord.ext import commands

import config
from utils.database import add_setup_admin, purge_guild_data, register_ticket_panel, remove_setup_admin, reset_guild_settings, save_guild_settings
from utils.embeds import estimate_response_time, ticket_panel
from utils.logger import log_exception, log_interaction, log_ticket
from utils.permissions import can_manage_setup_admins, can_setup
from views.dropdown import TicketPanel

timezone = pytz.timezone(config.TIMEZONE)


def parse_ticket_options(value):
    if not value:
        return list(config.DEFAULT_TICKET_OPTIONS)
    options = []
    for item in value.split(","):
        cleaned = " ".join(item.strip().split())
        if 2 <= len(cleaned) <= 50 and cleaned.casefold() not in {entry.casefold() for entry in options}:
            options.append(cleaned)
    return options[:10] or list(config.DEFAULT_TICKET_OPTIONS)


def setup_permission_report(guild, needs_role_creation=False):
    member = guild.me
    if member is None:
        return ["Bot member could not be resolved"]
    permissions = member.guild_permissions
    required = {
        "Manage Channels": permissions.manage_channels,
        "View Channels": permissions.view_channel,
        "Send Messages": permissions.send_messages,
        "Embed Links": permissions.embed_links,
        "Attach Files": permissions.attach_files,
        "Read Message History": permissions.read_message_history,
    }
    if needs_role_creation:
        required["Manage Roles"] = permissions.manage_roles
    return [name for name, available in required.items() if not available]


def public_install_permissions():
    return discord.Permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        manage_channels=True,
        manage_roles=True,
        manage_messages=True,
        kick_members=True,
        ban_members=True,
        moderate_members=True,
    )


def resource_report(guild, settings):
    if not settings or not settings.get("SETUP_COMPLETE"):
        return ["Setup is not complete"]
    resources = {
        "Ticket category": (settings.get("TICKET_CATEGORY_ID"), discord.CategoryChannel),
        "Archive category": (settings.get("TICKET_ARCHIVE_CATEGORY_ID"), discord.CategoryChannel),
        "Ticket panel channel": (settings.get("TICKET_PANEL_CHANNEL_ID"), discord.TextChannel),
        "Logging channel": (settings.get("LOG_CHANNEL_ID"), discord.TextChannel),
    }
    issues = []
    for name, (resource_id, expected_type) in resources.items():
        resource = guild.get_channel(resource_id) if resource_id else None
        if not isinstance(resource, expected_type):
            issues.append(f"{name} is missing")
    panel_channel = guild.get_channel(settings.get("TICKET_PANEL_CHANNEL_ID", 0))
    if isinstance(panel_channel, discord.TextChannel):
        panel_category = panel_channel.category
        if panel_channel.name != "ticket" or panel_category is None or panel_category.name != "ticket-system":
            issues.append("Ticket panel must be #ticket inside the ticket-system category")
    staff_role_id = settings.get("MOD_ROLE", 0)
    if not staff_role_id or guild.get_role(staff_role_id) is None:
        issues.append("Staff role is missing")
    return issues


def welcome_embed(guild):
    embed = discord.Embed(
        title=f"Welcome to {config.BOT_NAME}",
        description=(
            "A professional ticket-management and staff-operations system is now available on this server. "
            "The server owner or an authorized administrator can complete the entire configuration in less than two minutes."
        ),
        color=0x5865F2,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Start Setup",
        value="Run `/setup start`. You may select an existing staff role or allow the bot to create one automatically.",
        inline=False,
    )
    embed.add_field(
        name="Automatic Configuration",
        value="The setup creates a `ticket-system` category with a `ticket` panel channel, plus the private ticket category, archive category, logging channel, and initial ticket menu.",
        inline=False,
    )
    embed.add_field(
        name="Setup Access",
        value="The server owner, Discord administrators, and delegated setup administrators can use every `/setup` command.",
        inline=False,
    )
    embed.add_field(
        name="Owner Administration",
        value="Only the server owner can use `/add admin` and `/remove admin`. Select a username or enter a Discord user ID.",
        inline=False,
    )
    embed.add_field(
        name="Custom Ticket Types",
        value="Run `/setup tickets` and enter names such as `Partnership, Issues, Player Reports, Questions`. Up to ten custom types are supported.",
        inline=False,
    )
    embed.add_field(
        name="Command Directory",
        value="Run `/help` at any time to view every available slash command and its purpose.",
        inline=False,
    )
    links = []
    if config.SUPPORT_SERVER_URL:
        links.append(f"[Support Server]({config.SUPPORT_SERVER_URL})")
    if config.PRIVACY_POLICY_URL:
        links.append(f"[Privacy Policy]({config.PRIVACY_POLICY_URL})")
    if config.TERMS_OF_SERVICE_URL:
        links.append(f"[Terms of Service]({config.TERMS_OF_SERVICE_URL})")
    if links:
        embed.add_field(name="Resources", value=" | ".join(links), inline=False)
    embed.set_footer(text=f"{config.BOT_NAME} | Server Onboarding")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


class ResetSetupView(discord.ui.View):
    def __init__(self, requester_id, guild_id):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.guild_id = guild_id

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the administrator who requested this reset can confirm it.", ephemeral=True)
            return
        await reset_guild_settings(self.guild_id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="The bot configuration has been reset. Existing Discord channels were retained. Run `/setup start` to configure the server again.",
            embed=None,
            view=self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the administrator who requested this reset can cancel it.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Setup reset cancelled.", embed=None, view=self)

    async def on_error(self, interaction, error, item):
        reference = log_exception(
            "ONBOARDING",
            error,
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            context="Setup reset interaction failed",
        )
        message = f"The reset action could not be completed. Error reference: `{reference}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class Onboarding(commands.Cog):
    setup_group = app_commands.Group(name="setup", description="Configure the ticket system for this server")
    add_group = app_commands.Group(name="add", description="Add access to the ticket system")
    remove_group = app_commands.Group(name="remove", description="Remove access from the ticket system")

    def __init__(self, bot):
        self.bot = bot
        self.ready_processed = False

    async def welcome_channel(self, guild):
        member = guild.me
        candidates = []
        if guild.system_channel:
            candidates.append(guild.system_channel)
        candidates.extend(channel for channel in guild.text_channels if channel not in candidates)
        for channel in candidates:
            permissions = channel.permissions_for(member) if member else None
            if permissions and permissions.view_channel and permissions.send_messages and permissions.embed_links:
                return channel
        return None

    async def deliver_welcome(self, guild):
        settings = config.GUILDS.get(guild.id) or config.normalize_guild_config(
            guild.id,
            {"NAME": guild.name, "SETUP_COMPLETE": False, "WELCOME_SENT": False},
        )
        if settings.get("WELCOME_SENT"):
            return False
        delivered = False
        channel = await self.welcome_channel(guild)
        try:
            if channel:
                await channel.send(embed=welcome_embed(guild))
                delivered = True
            elif guild.owner:
                await guild.owner.send(embed=welcome_embed(guild))
                delivered = True
        except discord.HTTPException as error:
            log_exception("ONBOARDING", error, guild=guild, context="Welcome message delivery failed")
        settings["NAME"] = guild.name
        settings["WELCOME_SENT"] = delivered
        await save_guild_settings(guild.id, settings)
        return delivered

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self.deliver_welcome(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        await purge_guild_data(guild.id)

    @commands.Cog.listener()
    async def on_ready(self):
        if self.ready_processed:
            return
        self.ready_processed = True
        for guild in self.bot.guilds:
            if guild.id not in config.GUILDS or not config.GUILDS[guild.id].get("WELCOME_SENT"):
                await self.deliver_welcome(guild)

    async def require_setup_admin(self, interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return False
        if not can_setup(interaction.user):
            await interaction.response.send_message(
                "Setup access is limited to the server owner, Discord administrators, and delegated setup administrators.",
                ephemeral=True,
            )
            return False
        return True

    async def require_admin_manager(self, interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)
            return False
        if not can_manage_setup_admins(interaction.user):
            await interaction.response.send_message(
                "Only the server owner can manage setup administrators.",
                ephemeral=True,
            )
            return False
        return True

    async def resolve_admin_member(self, guild, member, user_id):
        if member:
            return member
        if not user_id:
            return None
        cleaned = user_id.strip().removeprefix("<@").removesuffix(">").lstrip("!")
        if not cleaned.isdigit():
            return None
        member_id = int(cleaned)
        resolved = guild.get_member(member_id)
        if resolved:
            return resolved
        try:
            return await guild.fetch_member(member_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def resolve_staff_role(self, guild, requested_role, current_settings):
        if requested_role and requested_role != guild.default_role:
            return requested_role
        current_role_id = current_settings.get("MOD_ROLE", 0)
        current_role = guild.get_role(current_role_id) if current_role_id else None
        if current_role:
            return current_role
        existing = discord.utils.find(lambda role: role.name.casefold() == f"{config.BOT_NAME} staff".casefold(), guild.roles)
        if existing:
            return existing
        return await guild.create_role(
            name=f"{config.BOT_NAME} Staff",
            color=discord.Color.from_rgb(88, 101, 242),
            mentionable=True,
            reason=f"{config.BOT_NAME} automatic setup",
        )

    async def ensure_category(self, guild, channel_id, name, overwrites):
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.CategoryChannel):
            return channel
        existing = discord.utils.find(lambda item: item.name.casefold() == name.casefold(), guild.categories)
        if existing:
            return existing
        return await guild.create_category(name=name, overwrites=overwrites, reason=f"{config.BOT_NAME} automatic setup")

    async def ensure_text_channel(self, guild, channel_id, name, category, overwrites, topic):
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel
        existing = discord.utils.find(lambda item: item.name.casefold() == name.casefold(), guild.text_channels)
        if existing:
            return existing
        return await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            topic=topic,
            reason=f"{config.BOT_NAME} automatic setup",
        )

    async def configure_server(self, guild, staff_role, ticket_types):
        current = dict(config.GUILDS.get(guild.id) or config.normalize_guild_config(guild.id, {"NAME": guild.name}))
        requested_role = staff_role if staff_role and staff_role != guild.default_role else None
        role_will_be_created = requested_role is None and guild.get_role(current.get("MOD_ROLE", 0)) is None
        missing_permissions = setup_permission_report(guild, needs_role_creation=role_will_be_created)
        if missing_permissions:
            return None, missing_permissions

        staff_role = await self.resolve_staff_role(guild, staff_role, current)
        bot_member = guild.me
        private_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        panel_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
            staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True, read_message_history=True),
        }

        ticket_category = await self.ensure_category(
            guild,
            current.get("TICKET_CATEGORY_ID", 0),
            f"{config.BOT_NAME} Tickets",
            private_overwrites,
        )
        archive_category = await self.ensure_category(
            guild,
            current.get("TICKET_ARCHIVE_CATEGORY_ID", 0),
            f"{config.BOT_NAME} Archive",
            private_overwrites,
        )
        panel_category = await self.ensure_category(
            guild,
            0,
            "ticket-system",
            panel_overwrites,
        )
        panel_channel = await self.ensure_text_channel(
            guild,
            current.get("TICKET_PANEL_CHANNEL_ID", 0),
            "ticket",
            panel_category,
            panel_overwrites,
            f"{config.BOT_NAME} public ticket portal",
        )
        log_channel = await self.ensure_text_channel(
            guild,
            current.get("LOG_CHANNEL_ID", 0),
            "ticket-logs",
            archive_category,
            private_overwrites,
            f"{config.BOT_NAME} staff logs",
        )

        await ticket_category.set_permissions(guild.default_role, view_channel=False, reason=f"{config.BOT_NAME} setup")
        await archive_category.set_permissions(guild.default_role, view_channel=False, reason=f"{config.BOT_NAME} setup")
        await panel_category.set_permissions(
            guild.default_role,
            view_channel=True,
            send_messages=False,
            read_message_history=True,
            reason=f"{config.BOT_NAME} setup",
        )
        if panel_channel.name != "ticket" or panel_channel.category_id != panel_category.id:
            await panel_channel.edit(
                name="ticket",
                category=panel_category,
                reason=f"{config.BOT_NAME} panel organization",
            )
        await panel_channel.set_permissions(
            guild.default_role,
            view_channel=True,
            send_messages=False,
            read_message_history=True,
            reason=f"{config.BOT_NAME} setup",
        )
        managed_channels = [
            ticket_category,
            archive_category,
            panel_category,
            panel_channel,
            log_channel,
            *ticket_category.channels,
            *archive_category.channels,
            *panel_category.channels,
        ]
        old_staff_role = guild.get_role(current.get("MOD_ROLE", 0))
        for managed_channel in dict.fromkeys(managed_channels):
            await managed_channel.set_permissions(
                staff_role,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                reason=f"{config.BOT_NAME} staff configuration",
            )
            if old_staff_role and old_staff_role.id != staff_role.id:
                await managed_channel.set_permissions(
                    old_staff_role,
                    overwrite=None,
                    reason=f"{config.BOT_NAME} staff role replacement",
                )

        current.update(
            {
                "NAME": guild.name,
                "TICKET_CATEGORY_ID": ticket_category.id,
                "TICKET_PANEL_CHANNEL_ID": panel_channel.id,
                "TICKET_ARCHIVE_CATEGORY_ID": archive_category.id,
                "LOG_CHANNEL_ID": log_channel.id,
                "OWNER_ROLES": [staff_role.id],
                "MOD_ROLE": staff_role.id,
                "TRIAL_MOD_ROLE": staff_role.id,
                "WARN_HISTORY_ROLE_ID": staff_role.id,
                "TICKET_OPTIONS": parse_ticket_options(ticket_types) if ticket_types is not None else current.get("TICKET_OPTIONS", config.DEFAULT_TICKET_OPTIONS),
                "SETUP_COMPLETE": True,
                "WELCOME_SENT": True,
            }
        )
        settings = await save_guild_settings(guild.id, current)

        available_staff = 0
        panel_message = await panel_channel.send(
            embed=ticket_panel(
                self.bot,
                guild=guild,
                available_staff=available_staff,
                response_time=estimate_response_time(available_staff),
            ),
            view=TicketPanel(options_list=settings["TICKET_OPTIONS"]),
        )
        await register_ticket_panel(
            guild.id,
            panel_channel.id,
            panel_message.id,
            datetime.now(timezone).isoformat(),
        )
        return settings, []

    @setup_group.command(name="start", description="Automatically configure the complete ticket system")
    @app_commands.describe(
        staff_role="Existing role that should manage tickets, or leave empty to create one",
        ticket_types="Optional comma-separated ticket types",
    )
    async def setup_start(
        self,
        interaction: discord.Interaction,
        staff_role: discord.Role | None = None,
        ticket_types: app_commands.Range[str, 2, 500] | None = None,
    ):
        if not await self.require_setup_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            settings, missing_permissions = await self.configure_server(interaction.guild, staff_role, ticket_types)
        except discord.Forbidden as error:
            reference = log_exception("ONBOARDING", error, guild=interaction.guild, user=interaction.user, context="Automatic setup forbidden")
            await interaction.followup.send(
                f"Automatic setup was blocked by Discord permissions. Error reference: `{reference}`",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            reference = log_exception("ONBOARDING", error, guild=interaction.guild, user=interaction.user, context="Automatic setup failed")
            await interaction.followup.send(
                f"Automatic setup could not be completed. Error reference: `{reference}`",
                ephemeral=True,
            )
            return

        if missing_permissions:
            await interaction.followup.send(
                "Setup cannot continue until the bot receives these permissions:\n" + "\n".join(f"- {name}" for name in missing_permissions),
                ephemeral=True,
            )
            return

        log_interaction(interaction.user, "setup start", interaction.channel, details=f"Guild: {interaction.guild.id}")
        embed = discord.Embed(
            title="Ticket System Ready",
            description="The server is fully configured and the ticket panel is now available.",
            color=0x2ECC71,
        )
        embed.add_field(name="Ticket Panel", value=f"<#{settings['TICKET_PANEL_CHANNEL_ID']}>", inline=True)
        embed.add_field(name="Ticket Category", value=f"<#{settings['TICKET_CATEGORY_ID']}>", inline=True)
        embed.add_field(name="Archive Category", value=f"<#{settings['TICKET_ARCHIVE_CATEGORY_ID']}>", inline=True)
        embed.add_field(name="Logging Channel", value=f"<#{settings['LOG_CHANNEL_ID']}>", inline=True)
        embed.add_field(name="Staff Role", value=f"<@&{settings['MOD_ROLE']}>", inline=True)
        embed.add_field(name="Ticket Types", value="\n".join(settings["TICKET_OPTIONS"]), inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Setup Complete")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @setup_group.command(name="status", description="Check configuration and required permissions")
    async def setup_status(self, interaction: discord.Interaction):
        if not await self.require_setup_admin(interaction):
            return
        settings = config.GUILDS.get(interaction.guild.id)
        missing_permissions = setup_permission_report(interaction.guild)
        resource_issues = resource_report(interaction.guild, settings)
        embed = discord.Embed(
            title="Setup Status",
            color=0x2ECC71 if not missing_permissions and not resource_issues else 0xF0B232,
        )
        embed.add_field(name="Configuration", value="Complete" if settings and settings.get("SETUP_COMPLETE") else "Not complete", inline=True)
        embed.add_field(name="Permissions", value="Ready" if not missing_permissions else "Attention required", inline=True)
        embed.add_field(name="Resources", value="Ready" if not resource_issues else "Attention required", inline=True)
        if settings:
            embed.add_field(name="Ticket Panel", value=f"<#{settings['TICKET_PANEL_CHANNEL_ID']}>" if settings["TICKET_PANEL_CHANNEL_ID"] else "Not configured", inline=False)
            embed.add_field(name="Staff Role", value=f"<@&{settings['MOD_ROLE']}>" if settings["MOD_ROLE"] else "Not configured", inline=False)
            embed.add_field(name="Delegated Setup Administrators", value=str(len(settings.get("SETUP_ADMIN_USERS", []))), inline=False)
        if missing_permissions:
            embed.add_field(name="Missing Permissions", value="\n".join(missing_permissions), inline=False)
        if resource_issues:
            embed.add_field(name="Resource Issues", value="\n".join(resource_issues), inline=False)
            embed.add_field(name="Recommended Action", value="Run `/setup repair` after resolving any missing permissions.", inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Configuration Check")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @setup_group.command(name="staff", description="Change the role that can access and manage tickets")
    @app_commands.describe(staff_role="Role that should manage tickets")
    async def setup_staff(self, interaction: discord.Interaction, staff_role: discord.Role):
        if not await self.require_setup_admin(interaction):
            return
        if staff_role == interaction.guild.default_role:
            await interaction.response.send_message("The everyone role cannot be used as the staff role.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            settings, missing_permissions = await self.configure_server(interaction.guild, staff_role, None)
        except discord.HTTPException as error:
            reference = log_exception("ONBOARDING", error, guild=interaction.guild, user=interaction.user, context="Staff role update failed")
            await interaction.followup.send(f"The staff role could not be updated. Error reference: `{reference}`", ephemeral=True)
            return
        if missing_permissions:
            await interaction.followup.send(
                "The staff role cannot be updated until these permissions are granted:\n" + "\n".join(f"- {name}" for name in missing_permissions),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"The staff role is now <@&{settings['MOD_ROLE']}>. Existing managed ticket resources were updated and a fresh panel was published.",
            ephemeral=True,
        )

    @setup_group.command(name="tickets", description="Change the ticket types shown in the panel")
    @app_commands.describe(ticket_types="Comma-separated ticket types, with a maximum of ten")
    async def setup_tickets(self, interaction: discord.Interaction, ticket_types: app_commands.Range[str, 2, 500]):
        if not await self.require_setup_admin(interaction):
            return
        current = config.GUILDS.get(interaction.guild.id)
        if not current or not current.get("SETUP_COMPLETE"):
            await interaction.response.send_message("Run `/setup start` before changing ticket types.", ephemeral=True)
            return
        staff_role = interaction.guild.get_role(current.get("MOD_ROLE", 0))
        await interaction.response.defer(ephemeral=True)
        try:
            settings, missing_permissions = await self.configure_server(interaction.guild, staff_role, ticket_types)
        except discord.HTTPException as error:
            reference = log_exception("ONBOARDING", error, guild=interaction.guild, user=interaction.user, context="Ticket type update failed")
            await interaction.followup.send(f"Ticket types could not be updated. Error reference: `{reference}`", ephemeral=True)
            return
        if missing_permissions:
            await interaction.followup.send(
                "Ticket types cannot be updated until these permissions are granted:\n" + "\n".join(f"- {name}" for name in missing_permissions),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            "Ticket types updated successfully:\n" + "\n".join(f"- {name}" for name in settings["TICKET_OPTIONS"]),
            ephemeral=True,
        )

    @setup_group.command(name="admins", description="List members with delegated setup access")
    async def setup_admins(self, interaction: discord.Interaction):
        if not await self.require_setup_admin(interaction):
            return
        settings = config.GUILDS.get(interaction.guild.id, {})
        admin_ids = settings.get("SETUP_ADMIN_USERS", [])
        value = "\n".join(f"<@{user_id}> (`{user_id}`)" for user_id in admin_ids) if admin_ids else "No delegated setup administrators."
        embed = discord.Embed(
            title="Setup Administrators",
            description=value,
            color=0x5865F2,
        )
        embed.add_field(
            name="Permanent Access",
            value="The server owner and members with Discord Administrator permission always have setup access.",
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Access Management")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @add_group.command(name="admin", description="Allow a member to configure the ticket system")
    @app_commands.describe(member="Select a server member by username", user_id="Alternatively enter the member's Discord user ID")
    async def add_admin(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        user_id: app_commands.Range[str, 17, 20] | None = None,
    ):
        if not await self.require_admin_manager(interaction):
            return
        target = await self.resolve_admin_member(interaction.guild, member, user_id)
        if target is None:
            await interaction.response.send_message("Select a valid server member or enter a valid Discord user ID.", ephemeral=True)
            return
        if target.bot:
            await interaction.response.send_message("Bot accounts cannot be setup administrators.", ephemeral=True)
            return
        added, settings = await add_setup_admin(interaction.guild.id, target.id)
        if not added:
            await interaction.response.send_message(f"{target.mention} already has delegated setup access.", ephemeral=True)
            return
        log_interaction(interaction.user, "add admin", interaction.channel, details=f"Target: {target.id}")
        embed = discord.Embed(
            title="Setup Administrator Added",
            description=f"{target.mention} can now configure {interaction.guild.name} with all `/setup` commands.",
            color=0x2ECC71,
        )
        embed.add_field(name="Member", value=f"{target} (`{target.id}`)", inline=False)
        embed.add_field(name="Delegated Administrators", value=str(len(settings["SETUP_ADMIN_USERS"])), inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Access Management")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @remove_group.command(name="admin", description="Remove a member's delegated setup access")
    @app_commands.describe(member="Select a server member by username", user_id="Alternatively enter the member's Discord user ID")
    async def remove_admin(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        user_id: app_commands.Range[str, 17, 20] | None = None,
    ):
        if not await self.require_admin_manager(interaction):
            return
        target = await self.resolve_admin_member(interaction.guild, member, user_id)
        if target is None:
            await interaction.response.send_message("Select a valid server member or enter a valid Discord user ID.", ephemeral=True)
            return
        removed, settings = await remove_setup_admin(interaction.guild.id, target.id)
        if not removed:
            await interaction.response.send_message(f"{target.mention} does not have delegated setup access.", ephemeral=True)
            return
        log_interaction(interaction.user, "remove admin", interaction.channel, details=f"Target: {target.id}")
        embed = discord.Embed(
            title="Setup Administrator Removed",
            description=f"{target.mention} can no longer use setup commands unless Discord permissions independently grant access.",
            color=0xED4245,
        )
        embed.add_field(name="Member", value=f"{target} (`{target.id}`)", inline=False)
        embed.add_field(name="Delegated Administrators", value=str(len(settings["SETUP_ADMIN_USERS"])), inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Access Management")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @setup_group.command(name="repair", description="Recreate missing ticket resources and publish a fresh panel")
    async def setup_repair(self, interaction: discord.Interaction):
        if not await self.require_setup_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        current = config.GUILDS.get(interaction.guild.id, {})
        staff_role = interaction.guild.get_role(current.get("MOD_ROLE", 0))
        try:
            settings, missing_permissions = await self.configure_server(interaction.guild, staff_role, None)
        except discord.HTTPException as error:
            reference = log_exception("ONBOARDING", error, guild=interaction.guild, user=interaction.user, context="Setup repair failed")
            await interaction.followup.send(f"Repair failed. Error reference: `{reference}`", ephemeral=True)
            return
        if missing_permissions:
            await interaction.followup.send(
                "Repair requires these permissions:\n" + "\n".join(f"- {name}" for name in missing_permissions),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Configuration repaired successfully. A fresh panel was published in <#{settings['TICKET_PANEL_CHANNEL_ID']}>.",
            ephemeral=True,
        )

    @setup_group.command(name="reset", description="Reset bot configuration without deleting Discord channels")
    async def setup_reset(self, interaction: discord.Interaction):
        if not await self.require_setup_admin(interaction):
            return
        embed = discord.Embed(
            title="Confirm Configuration Reset",
            description="This removes the stored server configuration and registered panel records. Existing Discord channels and ticket history will remain untouched.",
            color=0xED4245,
        )
        await interaction.response.send_message(
            embed=embed,
            view=ResetSetupView(interaction.user.id, interaction.guild.id),
            ephemeral=True,
        )

    @app_commands.command(name="help", description="Learn how to configure and use the ticket system")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"{config.BOT_NAME} Slash Command Directory",
            description="Every available slash command is listed below. Discord will enforce the required access when a command is used.",
            color=0x5865F2,
        )
        lines = []
        for command in self.bot.tree.walk_commands():
            if isinstance(command, app_commands.Group):
                continue
            lines.append(f"`/{command.qualified_name}` — {command.description}")
        lines.sort(key=str.casefold)
        sections = []
        current = []
        current_length = 0
        for line in lines:
            added_length = len(line) + (1 if current else 0)
            if current and current_length + added_length > 1000:
                sections.append("\n".join(current))
                current = []
                current_length = 0
            current.append(line)
            current_length += added_length
        if current:
            sections.append("\n".join(current))
        for index, section in enumerate(sections, start=1):
            title = "Available Commands" if index == 1 else f"Available Commands {index}"
            embed.add_field(name=title, value=section, inline=False)
        embed.add_field(
            name="Setup Access",
            value="The server owner, Discord administrators, and delegated setup administrators can configure the bot. Only the server owner can add or remove delegated administrators.",
            inline=False,
        )
        embed.add_field(name="Ticket Creation", value="Members create private tickets through the configured ticket-panel dropdown.", inline=False)
        if config.SUPPORT_SERVER_URL:
            embed.add_field(name="Support", value=f"[Open the official support server]({config.SUPPORT_SERVER_URL})", inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | {len(lines)} Slash Commands")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="invite", description="Get the official bot installation link and permission summary")
    async def invite_command(self, interaction: discord.Interaction):
        client_id = self.bot.user.id if self.bot.user else self.bot.application_id
        if not client_id:
            await interaction.response.send_message("The installation link is temporarily unavailable.", ephemeral=True)
            return
        install_url = discord.utils.oauth_url(
            client_id,
            permissions=public_install_permissions(),
            scopes=("bot", "applications.commands"),
        )
        embed = discord.Embed(
            title=f"Install {config.BOT_NAME}",
            description=f"[Add the bot to your server]({install_url}) and run `/setup start` to complete the configuration.",
            color=0x5865F2,
        )
        embed.add_field(
            name="Requested Access",
            value="Ticket channel management, embeds, attachments, message history, staff-role management, and enabled moderation actions.",
            inline=False,
        )
        embed.add_field(name="Security", value="The installation does not request the Administrator permission.", inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Official Installation")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="privacy", description="View the bot's data and privacy summary")
    async def privacy_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Data and Privacy Summary",
            description="The bot stores only operational information required to provide ticket and moderation features.",
            color=0x5865F2,
        )
        embed.add_field(name="Stored Data", value="Server configuration, Discord IDs, ticket lifecycle records, moderation records, staff availability, and diagnostic references.", inline=False)
        embed.add_field(name="Ticket Content", value="Ticket messages are read when generating transcripts or evaluating inactivity. Transcript files are created only as part of ticket operations.", inline=False)
        embed.add_field(name="Administrator Control", value="Server administrators can reset configuration with `/setup reset`. A public Privacy Policy and Terms of Service must be linked before App Directory release.", inline=False)
        if config.PRIVACY_POLICY_URL:
            embed.add_field(name="Privacy Policy", value=f"[Read the full Privacy Policy]({config.PRIVACY_POLICY_URL})", inline=False)
        if config.TERMS_OF_SERVICE_URL:
            embed.add_field(name="Terms of Service", value=f"[Read the Terms of Service]({config.TERMS_OF_SERVICE_URL})", inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Privacy")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Onboarding(bot))
