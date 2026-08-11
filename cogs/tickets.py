import discord

from discord.ext import commands

from views.dropdown import TicketPanel

from views.ticket_buttons import TicketButtons

from views.closed_buttons import ClosedTicketButtons

from utils.permissions import can_setup

from utils.embeds import ticket_panel, error

from utils.logger import log_command, log_ticket

import config





class Tickets(commands.Cog):


    def __init__(self, bot):

        self.bot = bot





    @commands.command(
        name="setup"
    )
    async def setup_ticket(
        self,
        ctx
    ):
        log_command(ctx.author, "!setup", ctx.channel)
        if not can_setup(ctx.author):
            log_ticket("Setup Aborted (Permission Denied)", ctx.channel, ctx.author)
            await ctx.send(
                embed=error(
                    "You cannot use this command."
                )
            )
            return

        guild_id = ctx.guild.id if ctx.guild else config.GUILD_ID
        guild_cfg = config.get_guild_config(guild_id)
        panel_channel_id = guild_cfg["TICKET_PANEL_CHANNEL_ID"]
        options_list = guild_cfg["TICKET_OPTIONS"]

        channel = self.bot.get_channel(panel_channel_id)

        if channel is None:
            await ctx.send(
                embed=error(
                    "Ticket panel channel not found."
                )
            )
            return

        await channel.send(
            embed=ticket_panel(self.bot, guild=ctx.guild),
            view=TicketPanel(options_list=options_list)
        )

        log_ticket("Ticket Panel Posted", channel, ctx.author)

        await ctx.send(
            "Ticket panel created."
        )










    @commands.Cog.listener()

    async def on_ready(

        self

    ):


        self.bot.add_view(

            TicketPanel()

        )


        self.bot.add_view(

            TicketButtons()

        )


        self.bot.add_view(

            ClosedTicketButtons()

        )





async def setup(bot):

    await bot.add_cog(

        Tickets(bot)

    )
