import discord

from views.dropdown import ApplicationDropdown



class TicketPanel(discord.ui.View):

    def __init__(self):

        super().__init__(
            timeout=None
        )


        self.add_item(
            ApplicationDropdown()
        )
