from datetime import datetime

import discord
import pytz
from discord import app_commands
from discord.ext import commands

import config
from utils.logger import log_interaction, log_ticket
from utils.permissions import is_staff

timezone = pytz.timezone("Europe/Berlin")

STATUS_STYLES = {
    "Rolling Out": {
        "color": 0x5865F2,
        "label": "Deployment in progress",
        "message": "The planned update is now being introduced. Work is actively progressing and further information will be shared when the next milestone is reached.",
    },
    "Under Review": {
        "color": 0xF0B232,
        "label": "Review in progress",
        "message": "The latest information is being reviewed by the responsible team. Services remain available while the details are assessed and verified.",
    },
    "Action Required": {
        "color": 0xE67E22,
        "label": "Response required",
        "message": "Additional information or action is required before progress can continue. Please review the announcement below and follow the provided next step.",
    },
    "Completed": {
        "color": 0x2ECC71,
        "label": "Update completed",
        "message": "The announced update has been completed successfully. The updated systems are now available and the deployment has entered final verification.",
    },
}


class Updates(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def build_embed(self, guild, interaction, status, headline, details, expected_time, next_step):
        style = STATUS_STYLES[status]
        embed = discord.Embed(
            title=headline,
            description=f"{style['message']}\n\n{details}",
            color=style["color"],
            timestamp=datetime.now(timezone),
        )
        icon_url = self.bot.user.display_avatar.url if self.bot.user else None
        embed.set_author(name=f"{config.BOT_NAME} | System Operations", icon_url=icon_url)
        embed.add_field(name="Current Status", value=f"**{style['label']}**", inline=True)
        embed.add_field(name="Environment", value=guild.name, inline=True)
        embed.add_field(name="Published By", value=f"{interaction.user.display_name}\n`{interaction.user.id}`", inline=True)
        if expected_time:
            embed.add_field(name="Expected Timeline", value=expected_time, inline=False)
        if next_step:
            embed.add_field(name="Next Step", value=next_step, inline=False)
        embed.add_field(
            name="Communication",
            value="New information will be published in this channel when the update reaches its next stage. Please use the ticket panel below if individual assistance is required.",
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Official System Update")
        return embed

    def message_contains_ticket_panel(self, message):
        for row in message.components:
            for component in getattr(row, "children", []):
                if getattr(component, "custom_id", None) == "zer_application_dropdown":
                    return True
        return False

    async def resolve_panel_channel(self, guild):
        guild_config = config.GUILDS.get(guild.id)
        if guild_config:
            channel_id = guild_config["TICKET_PANEL_CHANNEL_ID"]
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.HTTPException:
                    channel = None
            if channel is not None and hasattr(channel, "send"):
                return channel

        for channel in guild.text_channels:
            permissions = channel.permissions_for(guild.me)
            if not permissions.view_channel or not permissions.read_message_history or not permissions.send_messages:
                continue
            try:
                async for message in channel.history(limit=100):
                    if self.bot.user and message.author.id == self.bot.user.id and self.message_contains_ticket_panel(message):
                        return channel
            except discord.HTTPException:
                continue
        return None

    @app_commands.command(name="updatez", description="Broadcast a professional update to every ticket panel")
    @app_commands.describe(
        status="Current stage of the update",
        headline="Short title describing the update",
        details="Clear explanation of what is changing or being reviewed",
        expected_time="Optional completion time or next review window",
        next_step="Optional action that will happen next",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Rolling Out", value="Rolling Out"),
            app_commands.Choice(name="Under Review", value="Under Review"),
            app_commands.Choice(name="Action Required", value="Action Required"),
            app_commands.Choice(name="Completed", value="Completed"),
        ]
    )
    async def updatez(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str],
        headline: app_commands.Range[str, 3, 100],
        details: app_commands.Range[str, 10, 1000],
        expected_time: app_commands.Range[str, 2, 100] | None = None,
        next_step: app_commands.Range[str, 3, 300] | None = None,
    ):
        log_interaction(interaction.user, "updatez", interaction.channel, details=f"Global status: {status.value}")

        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used inside a configured server.", ephemeral=True)
            return
        if interaction.guild.id not in config.GUILDS:
            await interaction.response.send_message("This server is not configured for global updates.", ephemeral=True)
            return
        if not is_staff(interaction.user):
            await interaction.response.send_message("You do not have permission to publish global updates.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        successful = []
        unavailable = []
        failed = []

        for guild in self.bot.guilds:
            guild_id = guild.id
            panel_channel = await self.resolve_panel_channel(guild)
            if panel_channel is None:
                unavailable.append(f"{guild.name} (`{guild_id}`): no accessible ticket panel was found")
                continue

            embed = self.build_embed(
                guild,
                interaction,
                status.value,
                headline,
                details,
                expected_time,
                next_step,
            )
            try:
                message = await panel_channel.send(embed=embed)
            except discord.HTTPException as error:
                failed.append(f"{guild.name} (`{guild_id}`): {error}")
                log_ticket("Global Update Failed", panel_channel, interaction.user, details=str(error))
                continue

            successful.append(f"{guild.name}: {panel_channel.mention}")
            log_ticket(
                "Global Update Published",
                panel_channel,
                interaction.user,
                details=f"Status: {status.value}, Message ID: {message.id}, Guild ID: {guild_id}",
            )

        lines = [f"Published successfully to {len(successful)} of {len(self.bot.guilds)} connected servers."]
        if successful:
            lines.append("\nSuccessful\n" + "\n".join(successful))
        if unavailable:
            lines.append("\nUnavailable\n" + "\n".join(unavailable))
        if failed:
            lines.append("\nFailed\n" + "\n".join(failed))
        await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)


async def setup(bot):
    await bot.add_cog(Updates(bot))
