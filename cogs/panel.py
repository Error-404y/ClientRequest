import discord 

from views .dropdown import ApplicationDropdown 
from views.base import ReliableView



class TicketPanel (ReliableView ):

    def __init__ (self ):

        super ().__init__ (
        timeout =None 
        )


        self .add_item (
        ApplicationDropdown ()
        )
