import discord 

from discord .ext import commands 

import config 

from utils .logger import log 





class Setup (commands .Cog ):


    def __init__ (self ,bot ):

        self .bot =bot 





    @commands .Cog .listener ()
    async def on_ready (self ):
        pass 









async def setup (bot ):

    await bot .add_cog (

    Setup (bot )

    )
