from datetime import datetime

import pytz
from discord.ext import commands

import config
from utils.database import get_available_staff_count, register_ticket_panel
from utils.embeds import error, estimate_response_time, ticket_panel
from utils.logger import log_command, log_ticket
from utils.permissions import can_setup
from views.closed_buttons import ClosedTicketButtons
from views.dropdown import TicketPanel
from views.ticket_buttons import TicketButtons


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="setup")
    async def setup_ticket(self, ctx):
        log_command(ctx.author, "!setup", ctx.channel)
        if ctx.guild is None:
            await ctx.send(embed=error("This command can only be used inside a configured server."))
            return
        if not can_setup(ctx.author):
            log_ticket("Setup Aborted (Permission Denied)", ctx.channel, ctx.author)
            await ctx.send(embed=error("You cannot use this command."))
            return

        try:
            guild_config = config.get_guild_config(ctx.guild.id)
        except ValueError:
            await ctx.send(embed=error("This server is not configured."))
            return

        channel = self.bot.get_channel(guild_config["TICKET_PANEL_CHANNEL_ID"])
        if channel is None:
            await ctx.send(embed=error("Ticket panel channel not found."))
            return

        available_staff = await get_available_staff_count(ctx.guild.id)
        panel_message = await channel.send(
            embed=ticket_panel(
                self.bot,
                guild=ctx.guild,
                available_staff=available_staff,
                response_time=estimate_response_time(available_staff),
            ),
            view=TicketPanel(options_list=guild_config["TICKET_OPTIONS"]),
        )
        await register_ticket_panel(
            ctx.guild.id,
            channel.id,
            panel_message.id,
            datetime.now(pytz.timezone("Europe/Berlin")).isoformat(),
        )
        log_ticket("Ticket Panel Posted", channel, ctx.author)
        await ctx.send("Ticket panel created.")


async def setup(bot):
    bot.add_view(TicketPanel())
    bot.add_view(TicketButtons())
    bot.add_view(ClosedTicketButtons())
    await bot.add_cog(Tickets(bot))
