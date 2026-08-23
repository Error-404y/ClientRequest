import asyncio
from datetime import datetime

import discord
import pytz
from discord import app_commands
from discord.ext import commands

import config
from utils.database import (
    get_staff_availability,
    get_ticket_panels,
    register_ticket_panel,
    remove_ticket_panel,
    set_staff_availability,
)
from utils.embeds import error as error_embed
from utils.embeds import estimate_response_time, ticket_panel
from utils.logger import log_exception, log_interaction
from utils.permissions import is_staff

timezone = pytz.timezone(config.TIMEZONE)

STATUS_COLORS = {
    "Available": 0x2ECC71,
    "Busy": 0xF0B232,
    "Away": 0xE67E22,
    "On Break": 0x3498DB,
    "Do Not Assign": 0xE74C3C,
    "Offline": 0x747F8D,
}


class Availability(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.presence_refresh_tasks = {}
        self.ready_refreshed = False

    def cog_unload(self):
        for task in self.presence_refresh_tasks.values():
            task.cancel()

    async def delayed_presence_refresh(self, guild):
        try:
            await asyncio.sleep(10)
            await self.refresh_ticket_panels(guild)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_exception(
                "AVAILABILITY",
                error,
                guild=guild,
                context="Presence-based ticket panel refresh failed",
            )
        finally:
            self.presence_refresh_tasks.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        if before.status == after.status or not config.is_guild_configured(
            after.guild.id
        ):
            return
        records = await get_staff_availability(after.guild.id)
        available_ids = {
            record["user_id"]
            for record in records
            if record["status"] == "Available"
        }
        if after.id not in available_ids or not is_staff(after):
            return
        current = self.presence_refresh_tasks.get(after.guild.id)
        if current is None or current.done():
            self.presence_refresh_tasks[after.guild.id] = asyncio.create_task(
                self.delayed_presence_refresh(after.guild)
            )

    @commands.Cog.listener()
    async def on_ready(self):
        if self.ready_refreshed:
            return
        self.ready_refreshed = True
        for guild in self.bot.guilds:
            if config.is_guild_configured(guild.id):
                try:
                    await self.refresh_ticket_panels(guild)
                except Exception as error:
                    log_exception(
                        "AVAILABILITY",
                        error,
                        guild=guild,
                        context="Startup ticket panel availability refresh failed",
                    )

    def is_ticket_panel_message(self, message):
        for row in message.components:
            for component in getattr(row, "children", []):
                if getattr(component, "custom_id", None) == "zer_application_dropdown":
                    return True
        return False

    async def discover_ticket_panels(self, guild):
        guild_config = config.get_guild_config(guild.id)
        channel = guild.get_channel(guild_config["TICKET_PANEL_CHANNEL_ID"])
        if channel is None:
            return
        try:
            async for message in channel.history(limit=100):
                if (
                    self.bot.user
                    and message.author.id == self.bot.user.id
                    and self.is_ticket_panel_message(message)
                ):
                    await register_ticket_panel(
                        guild.id,
                        channel.id,
                        message.id,
                        message.created_at.astimezone(timezone).isoformat(),
                    )
        except discord.HTTPException as error:
            log_exception(
                "AVAILABILITY",
                error,
                guild=guild,
                channel=channel,
                context="Ticket panel discovery failed",
            )

    async def refresh_ticket_panels(self, guild):
        records = await get_staff_availability(guild.id)
        available_staff = sum(
            1
            for record in records
            if record["status"] == "Available"
            and (member := guild.get_member(record["user_id"])) is not None
            and is_staff(member)
            and member.status
            not in {discord.Status.offline, discord.Status.invisible}
        )
        response_time = estimate_response_time(available_staff)
        panels = await get_ticket_panels(guild.id)
        if not panels:
            await self.discover_ticket_panels(guild)
            panels = await get_ticket_panels(guild.id)
        refreshed = 0

        for panel in panels:
            channel = guild.get_channel(panel["channel_id"])
            if channel is None:
                await remove_ticket_panel(guild.id, panel["message_id"])
                continue
            try:
                message = await channel.fetch_message(panel["message_id"])
                await message.edit(
                    embed=ticket_panel(
                        self.bot,
                        guild=guild,
                        available_staff=available_staff,
                        response_time=response_time,
                    )
                )
                refreshed += 1
            except discord.NotFound:
                await remove_ticket_panel(guild.id, panel["message_id"])
            except discord.HTTPException as error:
                log_exception(
                    "AVAILABILITY",
                    error,
                    guild=guild,
                    channel=channel,
                    context="Ticket panel refresh failed",
                )

        return refreshed, available_staff, response_time

    @app_commands.command(
        name="availability", description="Set your current ticket-support availability"
    )
    @app_commands.describe(status="Your current availability for ticket assignments")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Available", value="Available"),
            app_commands.Choice(name="Busy", value="Busy"),
            app_commands.Choice(name="Away", value="Away"),
            app_commands.Choice(name="On Break", value="On Break"),
            app_commands.Choice(name="Do Not Assign", value="Do Not Assign"),
            app_commands.Choice(name="Offline", value="Offline"),
        ]
    )
    async def availability(
        self, interaction: discord.Interaction, status: app_commands.Choice[str]
    ):
        if interaction.guild is None or not config.is_guild_configured(
            interaction.guild.id
        ):
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used in a configured server."
                ),
                ephemeral=True,
            )
            return
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed(
                    "You do not have permission to update staff availability."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await set_staff_availability(
            interaction.guild.id,
            interaction.user.id,
            status.value,
            datetime.now(timezone).isoformat(),
        )
        refreshed, available_staff, response_time = await self.refresh_ticket_panels(
            interaction.guild
        )
        log_interaction(
            interaction.user,
            "availability",
            interaction.channel,
            details=f"Status: {status.value}, Panels refreshed: {refreshed}",
        )

        embed = discord.Embed(
            title="Availability Updated",
            description="Your staff availability has been updated successfully.",
            color=STATUS_COLORS[status.value],
        )
        embed.add_field(name="Your Status", value=f"**{status.value}**", inline=True)
        embed.add_field(
            name="Available Staff", value=f"**{available_staff}**", inline=True
        )
        embed.add_field(
            name="Estimated Response", value=f"**{response_time}**", inline=True
        )
        embed.add_field(
            name="Ticket Panels Refreshed", value=str(refreshed), inline=False
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Staff Operations")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="availabilitylist",
        description="Display the current ticket-support availability team",
    )
    async def availabilitylist(self, interaction: discord.Interaction):
        if interaction.guild is None or not config.is_guild_configured(
            interaction.guild.id
        ):
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used in a configured server."
                ),
                ephemeral=True,
            )
            return
        if not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed(
                    "You do not have permission to view staff availability."
                ),
                ephemeral=True,
            )
            return

        records = await get_staff_availability(interaction.guild.id)
        records = [
            record
            for record in records
            if (member := interaction.guild.get_member(record["user_id"])) is not None
            and is_staff(member)
        ]
        available_staff = sum(
            1
            for record in records
            if record["status"] == "Available"
            and (member := interaction.guild.get_member(record["user_id"]))
            is not None
            and is_staff(member)
            and member.status
            not in {discord.Status.offline, discord.Status.invisible}
        )
        embed = discord.Embed(
            title=f"{config.BOT_NAME} Staff Availability",
            description=f"Available staff: **{available_staff}**\nEstimated response time: **{estimate_response_time(available_staff)}**",
            color=0x5865F2,
        )

        if not records:
            embed.add_field(
                name="Current Team",
                value="No staff availability has been submitted yet.",
                inline=False,
            )
        else:
            lines = []
            for record in records:
                member = interaction.guild.get_member(record["user_id"])
                name = member.mention if member else f"User `{record['user_id']}`"
                status_text = record["status"]
                if record["status"] == "Available" and member.status in {
                    discord.Status.offline,
                    discord.Status.invisible,
                }:
                    status_text = "Available (Discord Offline)"
                lines.append(f"{name} — **{status_text}**")
            embed.add_field(
                name="Current Team", value="\n".join(lines)[:1024], inline=False
            )

        embed.set_footer(text=f"{config.BOT_NAME} | Staff Operations")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Availability(bot))
