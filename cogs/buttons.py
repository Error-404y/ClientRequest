import discord 

from discord .ext import commands 

import config 

import asyncio 





from utils .permissions import is_owner ,is_staff ,is_moderator 
from utils.logger import log_exception
from views.base import ReliableView






class TicketButtons (ReliableView ):


    def __init__ (self ):

        super ().__init__ (timeout =None )






    @discord .ui .button (

    label ="Close",

    style =discord .ButtonStyle .gray ,

    custom_id ="zer_ticket_close"

    )

    async def close (

    self ,

    interaction :discord .Interaction ,

    button :discord .ui .Button 

    ):


        if not is_staff (interaction .user ):


            await interaction .response .send_message (

            "You cannot close this ticket.",

            ephemeral =True 

            )

            return 





        if interaction .channel .name .startswith (

        "closed-"

        ):


            await interaction .response .send_message (

            "This ticket is already closed.",

            ephemeral =True 

            )

            return 






            

        if interaction .channel .topic :


            if interaction .channel .topic .startswith (

            "ticket_owner:"

            ):


                user_id =int (

                interaction .channel .topic .replace (

                "ticket_owner:",

                ""

                )

                )



                try :


                    user =interaction .guild .get_member (

                    user_id 

                    )



                    if user is None :


                        user =await interaction .guild .fetch_member (

                        user_id 

                        )



                    await interaction .channel .set_permissions (

                    user ,

                    view_channel =False 

                    )


                except Exception as error:
                    log_exception(
                        "PERMISSION",
                        error,
                        guild=interaction.guild,
                        channel=interaction.channel,
                        user=interaction.user,
                        context="Legacy close control failed to remove ticket owner permissions",
                    )





        await interaction .channel .edit (

        name =f"closed-{interaction .channel .name }"

        )





        embed =discord .Embed (

        title ="Ticket Closed",

        description =f"""

Ticket closed by:

{interaction .user .mention }



The user has been removed from this ticket.



Owners can reopen this ticket.

""",

        color =discord .Color .orange ()

        )





        await interaction .response .send_message (

        embed =embed ,

        view =ClosedTicketButtons ()

        )









    @discord .ui .button (

    label ="Transcript",

    style =discord .ButtonStyle .blurple ,

    custom_id ="zer_ticket_transcript"

    )

    async def transcript (

    self ,

    interaction ,

    button 

    ):


        if not is_owner (interaction .user ):


            await interaction .response .send_message (

            "Only owners can create transcripts.",

            ephemeral =True 

            )

            return 





        await interaction .response .send_message (

        "Creating transcript...",

        ephemeral =True 

        )




        from cogs .transcript import create_transcript 



        file =await create_transcript (

        interaction .channel 

        )



        await interaction .followup .send (

        "Transcript created:",

        file =discord .File (file ),

        ephemeral =True 

        )










class ClosedTicketButtons (ReliableView ):


    def __init__ (self ):

        super ().__init__ (timeout =None )








    @discord .ui .button (

    label ="Reopen",

    style =discord .ButtonStyle .green ,

    custom_id ="zer_ticket_reopen"

    )

    async def reopen (

    self ,

    interaction :discord .Interaction ,

    button :discord .ui .Button 

    ):



        if not is_owner (interaction .user ):


            await interaction .response .send_message (

            "Only owners can reopen tickets.",

            ephemeral =True 

            )

            return 





        await interaction .response .defer ()





        


        if interaction .channel .name .startswith (

        "closed-"

        ):


            await interaction .channel .edit (

            name =interaction .channel .name .replace (

            "closed-",

            "",

            1 

            )

            )







            


        restored =False 



        if interaction .channel .topic :


            if interaction .channel .topic .startswith (

            "ticket_owner:"

            ):



                user_id =int (

                interaction .channel .topic .replace (

                "ticket_owner:",

                ""

                )

                )



                try :


                    user =interaction .guild .get_member (

                    user_id 

                    )



                    if user is None :


                        user =await interaction .guild .fetch_member (

                        user_id 

                        )





                    await interaction .channel .set_permissions (

                    user ,

                    view_channel =True ,

                    send_messages =True ,

                    read_message_history =True 

                    )



                    restored =True 



                except Exception as error:
                    log_exception(
                        "TICKET",
                        error,
                        guild=interaction.guild,
                        channel=interaction.channel,
                        user=interaction.user,
                        context="Legacy reopen control failed",
                    )








        if restored :


            await interaction .followup .send (

            "Ticket reopened.\n\nUser has been added back inside."

            )


        else :


            await interaction .followup .send (

            "Ticket reopened.\n\nUser could not be restored."

            )









    @discord .ui .button (

    label ="Delete",

    style =discord .ButtonStyle .red ,

    custom_id ="zer_ticket_delete"

    )

    async def delete (

    self ,

    interaction ,

    button 

    ):



        if not is_owner (interaction .user ):


            await interaction .response .send_message (

            "Only owners can delete tickets.",

            ephemeral =True 

            )

            return 





        await interaction .response .send_message (

        "Ticket deleting in 5 seconds...",

        ephemeral =True 

        )



        await asyncio .sleep (5 )



        await interaction .channel .delete ()







class Buttons (commands .Cog ):


    def __init__ (self ,bot ):

        self .bot =bot 





    async def cog_load (self ):


        self .bot .add_view (

        TicketButtons ()

        )


        self .bot .add_view (

        ClosedTicketButtons ()

        )







async def setup (bot ):

    await bot .add_cog (

    Buttons (bot )

    )
