import discord
from discord.ext import commands

import config
from utils.database import get_ticket_controls, set_ticket_control_message
from utils.logger import log_exception
from views.closed_buttons import ClosedTicketButtons
from views.dropdown import TicketPanel
from views.ticket_buttons import TicketButtons


class TicketControlRecovery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recovered = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.recovered:
            return
        for record in await get_ticket_controls():
            channel = self.bot.get_channel(record["channel_id"])
            if not isinstance(channel, discord.TextChannel):
                continue
            message = None
            message_id = record["control_message_id"]
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
            if message is None:
                try:
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
            if message:
                try:
                    if record["status"] == "closed":
                        await message.edit(view=ClosedTicketButtons())
                        continue
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
                    await message.edit(view=view)
                except discord.HTTPException as error:
                    log_exception(
                        "TICKET",
                        error,
                        guild=channel.guild,
                        channel=channel,
                        context="Ticket control state restoration failed",
                    )
        self.recovered = True


async def setup(bot):
    bot.add_view(TicketPanel())
    bot.add_view(TicketButtons())
    bot.add_view(ClosedTicketButtons())
    await bot.add_cog(TicketControlRecovery(bot))
