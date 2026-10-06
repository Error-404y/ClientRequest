import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import (
    get_ticket_controls,
    get_ticket_owner,
    get_ticket_record,
    mark_ticket_deleted,
    process_ticket_message,
    set_ticket_control_message,
    set_ticket_label,
    set_ticket_waiting_on,
    transfer_ticket_claim,
)
from utils.embeds import apply_ticket_label, ticket_claimed_dm
from utils.embeds import error as error_embed
from utils.logger import log_dm, log_exception, log_interaction, log_ticket
from utils.permissions import is_staff
from views.closed_buttons import ClosedTicketButtons
from views.dropdown import TicketPanel
from views.ticket_buttons import TicketButtons


class TicketControlRecovery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recovered = False

    def control_view(self, record):
        if record["status"] == "closed":
            return ClosedTicketButtons()
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
        return view

    @commands.Cog.listener()
    async def on_ready(self):
        if self.recovered:
            return
        after_id = 0
        while True:
            records = await get_ticket_controls(after_id=after_id, limit=500)
            if not records:
                break
            for record in records:
                after_id = record["id"]
                channel = self.bot.get_channel(record["channel_id"])
                if not isinstance(channel, discord.TextChannel):
                    guild = self.bot.get_guild(record["guild_id"])
                    if guild is not None:
                        await mark_ticket_deleted(record["channel_id"])
                        log_ticket(
                            "Missing Ticket Channel Reconciled",
                            record["channel_id"],
                            details=f"Guild ID: {record['guild_id']}",
                        )
                    continue
                if (
                    "waiting_changed_at" in record
                    and record.get("status") == "open"
                    and not record["waiting_changed_at"]
                ):
                    waiting_on = "staff"
                    changed_at = discord.utils.utcnow().isoformat()
                    try:
                        async for candidate in channel.history(limit=250):
                            if candidate.author.bot:
                                continue
                            if candidate.author.id == record.get("user_id"):
                                waiting_on = "staff"
                                changed_at = candidate.created_at.isoformat()
                                break
                            if is_staff(candidate.author):
                                waiting_on = "user"
                                changed_at = candidate.created_at.isoformat()
                                break
                    except discord.HTTPException as error:
                        log_exception(
                            "TICKET",
                            error,
                            guild=channel.guild,
                            channel=channel,
                            context="Legacy ticket response state recovery failed",
                        )
                    await set_ticket_waiting_on(
                        channel.id,
                        waiting_on,
                        changed_at,
                    )
                    record["waiting_on"] = waiting_on
                    record["waiting_changed_at"] = changed_at
                message_id = record["control_message_id"]
                if message_id:
                    await asyncio.sleep(0.25)
                    try:
                        await channel.fetch_message(message_id)
                    except discord.NotFound:
                        log_ticket(
                            "Stored Ticket Controls Missing",
                            channel,
                            details=f"Recreating controls for message {message_id}",
                        )
                    except discord.HTTPException as error:
                        log_exception(
                            "TICKET",
                            error,
                            guild=channel.guild,
                            channel=channel,
                            context="Stored ticket control verification failed",
                        )
                        continue
                    else:
                        continue
                try:
                    message = None
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
                if message is not None:
                    continue
                try:
                    recovered_embed = discord.Embed(
                        title=(
                            "Archived Ticket Controls Recovered"
                            if record["status"] == "closed"
                            else "Active Ticket Controls Recovered"
                        ),
                        description=(
                            "The ticket controls were restored after an interrupted lifecycle operation. Authorized staff can continue managing this ticket."
                        ),
                        color=(
                            discord.Color.orange()
                            if record["status"] == "closed"
                            else discord.Color.blurple()
                        ),
                        timestamp=discord.utils.utcnow(),
                    )
                    apply_ticket_label(recovered_embed, record["label"])
                    recovered_embed.set_footer(
                        text=f"{config.BOT_NAME} | Ticket Recovery"
                    )
                    message = await channel.send(
                        embed=recovered_embed,
                        view=self.control_view(record),
                    )
                    await set_ticket_control_message(channel.id, message.id)
                    log_ticket(
                        "Missing Ticket Controls Recreated",
                        channel,
                        details=f"Status: {record['status']}",
                    )
                except Exception as error:
                    log_exception(
                        "TICKET",
                        error,
                        guild=channel.guild,
                        channel=channel,
                        context="Missing ticket control recreation failed",
                    )
                    continue
        self.recovered = True

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        ticket = await get_ticket_record(message.channel.id)
        if ticket is None or ticket["status"] != "open":
            return
        staff_message = is_staff(message.author)
        if message.author.id != ticket["user_id"] and not staff_message:
            return
        result = await process_ticket_message(
            message.channel.id,
            message.author.id,
            staff_message,
            discord.utils.utcnow().isoformat(),
        )
        if not result.get("auto_claimed"):
            return
        current = await get_ticket_record(message.channel.id)
        if current and current["control_message_id"]:
            try:
                control = await message.channel.fetch_message(
                    current["control_message_id"]
                )
                await control.edit(view=self.control_view(current))
            except discord.HTTPException as error:
                log_exception(
                    "VIEW",
                    error,
                    guild=message.guild,
                    channel=message.channel,
                    user=message.author,
                    context="First staff reply auto-claim control update failed",
                )
        owner = message.guild.get_member(ticket["user_id"])
        if owner is None:
            try:
                owner = await message.guild.fetch_member(ticket["user_id"])
            except discord.HTTPException:
                owner = None
        if owner:
            try:
                await owner.send(
                    embed=ticket_claimed_dm(
                        message.guild,
                        message.channel,
                        message.author,
                        self.bot.user,
                    )
                )
                log_dm(owner, "First Reply Auto-Claim Notice", success=True)
            except discord.Forbidden:
                log_dm(
                    owner,
                    "First Reply Auto-Claim Notice",
                    success=False,
                    error_detail="Direct Messages Disabled",
                )
            except discord.HTTPException as error:
                log_exception(
                    "DM",
                    error,
                    guild=message.guild,
                    channel=message.channel,
                    user=owner,
                    context="First staff reply auto-claim notification failed",
                )
        embed = discord.Embed(
            title="Ticket Automatically Claimed",
            description=(
                f"{message.author.mention} was assigned because they were the first "
                "staff member to respond."
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Automatic Assignment")
        try:
            await message.channel.send(embed=embed)
        except discord.HTTPException as error:
            log_exception(
                "TICKET",
                error,
                guild=message.guild,
                channel=message.channel,
                user=message.author,
                context="First staff reply auto-claim notice failed",
            )

    @app_commands.command(
        name="waitingz",
        description="Set whether a ticket is waiting for the user or staff",
    )
    @app_commands.describe(state="Who is expected to reply next")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="Waiting for User", value="user"),
            app_commands.Choice(name="Waiting for Staff", value="staff"),
        ]
    )
    async def waitingz(
        self,
        interaction: discord.Interaction,
        state: app_commands.Choice[str],
    ):
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Only authorized staff can change ticket state."),
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("This command can only be used in a ticket channel."),
                ephemeral=True,
            )
            return
        updated = await set_ticket_waiting_on(
            interaction.channel.id,
            state.value,
            discord.utils.utcnow().isoformat(),
        )
        if not updated:
            await interaction.response.send_message(
                embed=error_embed("This channel is not an open registered ticket."),
                ephemeral=True,
            )
            return
        label = "Waiting for User" if state.value == "user" else "Waiting for Staff"
        embed = discord.Embed(
            title="Ticket Response State Updated",
            description=f"This ticket is now **{label}**.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Updated By", value=interaction.user.mention, inline=True)
        embed.set_footer(text=f"{config.BOT_NAME} | Ticket Workflow")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="transferz",
        description="Transfer a claimed ticket to another staff member",
    )
    @app_commands.describe(member="Staff member who should receive this ticket")
    async def transferz(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ):
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Only authorized staff can transfer tickets."),
                ephemeral=True,
            )
            return
        if not is_staff(member):
            await interaction.response.send_message(
                embed=error_embed("The selected member is not authorized staff."),
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=error_embed("This command can only be used in a ticket channel."),
                ephemeral=True,
            )
            return
        result = await transfer_ticket_claim(
            interaction.channel.id,
            interaction.user.id,
            member.id,
            discord.utils.utcnow().isoformat(),
        )
        if result["status"] == "unclaimed":
            await interaction.response.send_message(
                embed=error_embed(
                    "This ticket is currently unclaimed. Claim it before transferring it."
                ),
                ephemeral=True,
            )
            return
        if result["status"] == "same":
            await interaction.response.send_message(
                embed=error_embed("This ticket is already assigned to that staff member."),
                ephemeral=True,
            )
            return
        if result["status"] in {"not_found", "not_open"}:
            await interaction.response.send_message(
                embed=error_embed("This channel is not an open registered ticket."),
                ephemeral=True,
            )
            return
        ticket = await get_ticket_record(interaction.channel.id)
        if ticket and ticket["control_message_id"]:
            try:
                control = await interaction.channel.fetch_message(
                    ticket["control_message_id"]
                )
                await control.edit(view=self.control_view(ticket))
            except discord.HTTPException as error:
                log_exception(
                    "VIEW",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context="Transferred ticket control update failed",
                )
        previous_id = result["previous_claimed_by"]
        previous = interaction.guild.get_member(previous_id)
        embed = discord.Embed(
            title="Ticket Assignment Transferred",
            description=f"This ticket is now assigned to {member.mention}.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Previous Assignment",
            value=previous.mention if previous else f"`{previous_id}`",
            inline=True,
        )
        embed.add_field(name="New Assignment", value=member.mention, inline=True)
        embed.add_field(name="Transferred By", value=interaction.user.mention, inline=True)
        embed.set_footer(text=f"{config.BOT_NAME} | Ticket Assignment")
        await interaction.response.send_message(embed=embed)
        owner_id = await get_ticket_owner(interaction.channel.id)
        if owner_id:
            owner = interaction.guild.get_member(owner_id)
            if owner is None:
                try:
                    owner = await interaction.guild.fetch_member(owner_id)
                except discord.HTTPException:
                    owner = None
            if owner:
                notice = discord.Embed(
                    title="Ticket Assignment Updated",
                    description=(
                        f"Your ticket {interaction.channel.mention} has been transferred "
                        f"to {member.mention}."
                    ),
                    color=discord.Color.blurple(),
                    timestamp=discord.utils.utcnow(),
                )
                notice.set_footer(text=config.BOT_NAME)
                try:
                    await owner.send(embed=notice)
                    log_dm(owner, "Ticket Transfer Notice", success=True)
                except discord.Forbidden:
                    log_dm(
                        owner,
                        "Ticket Transfer Notice",
                        success=False,
                        error_detail="Direct Messages Disabled",
                    )
                except discord.HTTPException as error:
                    log_exception(
                        "DM",
                        error,
                        guild=interaction.guild,
                        channel=interaction.channel,
                        user=owner,
                        context="Ticket transfer notification failed",
                    )
        log_ticket(
            "Ticket Assignment Transferred",
            interaction.channel,
            interaction.user,
            details=f"Previous: {previous_id}, New: {member.id}",
        )

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
                embed=error_embed("Only authorized staff can change ticket labels."),
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
        synchronized = False
        if control_message and control_message.embeds:
            embed = discord.Embed.from_dict(control_message.embeds[0].to_dict())
            apply_ticket_label(embed, selected_label)
            try:
                await control_message.edit(embed=embed)
                synchronized = True
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
            description=(
                "The ticket label has been saved and synchronized with the main ticket panel."
                if synchronized
                else "The ticket label was saved, but the main ticket panel could not be updated."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        result.add_field(
            name="Ticket Label",
            value=selected_label or "Not assigned",
            inline=True,
        )
        result.add_field(name="Updated By", value=interaction.user.mention, inline=True)
        result.add_field(name="Ticket UUID", value=f"`{ticket['uuid']}`", inline=False)
        result.set_footer(text=f"{config.BOT_NAME} | Ticket Classification")
        await interaction.followup.send(embed=result)


async def setup(bot):
    bot.add_view(TicketPanel())
    bot.add_view(TicketButtons())
    bot.add_view(ClosedTicketButtons())
    await bot.add_cog(TicketControlRecovery(bot))
