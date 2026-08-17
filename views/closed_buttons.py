import discord 
import asyncio 
import os 
import config 
from discord .ui import View ,Button 
from utils .permissions import (
is_staff ,
is_owner 
)
from utils .database import reopen_ticket ,get_ticket_record ,get_ticket_owner, mark_ticket_deleted
from utils .embeds import (
ticket_reopened ,
error 
)
from cogs .transcript import create_transcript 
from utils .logger import log_interaction ,log_ticket ,log_perm 
from utils.logger import log_exception
from views.base import ReliableView

class ClosedTicketButtons (ReliableView ):
    def __init__ (self ):
        super ().__init__ (timeout =None )

    @discord .ui .button (
    label ="Reopen Ticket",
    style =discord .ButtonStyle .success ,
    custom_id ="zer_reopen"
    )
    async def reopen (self ,interaction ,button ):
        log_interaction (interaction .user ,"zer_reopen",interaction .channel )
        if not is_staff (interaction .user ):
            log_ticket ("Reopen Rejected (Not Staff)",interaction .channel ,interaction .user )
            await interaction .response .send_message (
            embed =error ("You do not have permission to reopen this ticket."),
            ephemeral =True 
            )
            return 

        await interaction .response .defer ()

        
        for item in self .children :
            item .disabled =True 
        try :
            await interaction .message .edit (view =self )
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to disable closed ticket controls before reopening",
            )

        channel =interaction .channel 

        
        user_id =None 
        member =None 
        if channel .topic and "ticket_owner:"in channel .topic :
            try :
                topic_part =channel .topic .split ("|")[0 ].strip ()
                user_id =int (topic_part .replace ("ticket_owner:","").strip ())
            except ValueError :
                user_id =None

                
        if user_id is None :
            try :
                user_id =await get_ticket_owner (channel .id )
            except Exception as error:
                log_exception(
                    "DATABASE",
                    error,
                    guild=interaction.guild,
                    channel=channel,
                    user=interaction.user,
                    context="Failed to resolve ticket owner during reopen",
                )

        if user_id :
            member =interaction .guild .get_member (user_id )
            if member is None :
                try :
                    member =await interaction .guild .fetch_member (user_id )
                except discord .HTTPException as error:
                    log_exception(
                        "DISCORD",
                        error,
                        guild=interaction.guild,
                        channel=channel,
                        user=user_id,
                        context="Failed to fetch ticket owner during reopen",
                    )

            if member :
                try :
                    await channel .set_permissions (
                    member ,
                    view_channel =True ,
                    send_messages =True ,
                    read_message_history =True 
                    )
                    log_perm (channel ,member ,"Restored view_channel=True, send_messages=True, read_message_history=True")
                except discord.HTTPException as error:
                    log_exception(
                        "PERMISSION",
                        error,
                        guild=interaction.guild,
                        channel=channel,
                        user=member,
                        context="Failed to restore ticket owner permissions",
                    )
                    raise

        
        async def perform_background_reopen ():
            edit_kwargs ={}
            category = interaction.guild.get_channel(config.get_ticket_category_id(interaction.guild.id))
            if category :
                edit_kwargs ["category"]=category 

            new_name =channel .name .replace ("closed-","",1 )
            if new_name !=channel .name :
                edit_kwargs ["name"]=new_name 

            if edit_kwargs :
                try :
                    await channel .edit (**edit_kwargs )
                    log_ticket ("Restored Channel Properties",channel ,interaction .user ,details =f"Moved to category {category .name if category else 'Default'}, Name: {new_name }")
                except discord.HTTPException as error:
                    log_exception(
                        "TICKET",
                        error,
                        guild=interaction.guild,
                        channel=channel,
                        user=interaction.user,
                        context="Failed to restore reopened ticket channel properties",
                    )
                    raise

        await perform_background_reopen()
        await reopen_ticket (channel .id )

        
        await interaction .followup .send (
        embed =ticket_reopened (applicant =member )
        )

        
        from utils .logger import ticket_reopen_report 
        ticket_reopen_report (channel ,interaction .user ,user_id ,interaction .client )

        
        
        ticket_record =await get_ticket_record (channel .id )
        from views .ticket_buttons import TicketButtons 
        view =TicketButtons ()

        if ticket_record :
            application =ticket_record .get ("application")
            form_url = None
            if application == "Moderator Application":
                form_url = config.MODERATOR_FORM
            elif application == "Uploader Application":
                form_url = config.UPLOADER_FORM
            if form_url:
                form_button =discord .ui .Button (
                label ="Application Form",
                style =discord .ButtonStyle .link ,
                url =form_url 
                )
                view .add_item (form_button )

            
            claimed_by =ticket_record .get ("claimed_by")
            if claimed_by :
                for item in view .children :
                    if getattr (item ,"custom_id",None )=="zer_claim":
                        item .disabled =True 
                        try :
                            claimant =interaction .guild .get_member (claimed_by )
                            if claimant is None :
                                claimant =await interaction .guild .fetch_member (claimed_by )
                            if claimant :
                                item .label =f"Claimed by {claimant .display_name }"
                                item .style =discord .ButtonStyle .secondary 
                        except discord.HTTPException as error:
                            log_exception(
                                "DISCORD",
                                error,
                                guild=interaction.guild,
                                channel=channel,
                                user=claimed_by,
                                context="Failed to resolve claimant while reopening ticket",
                            )
                            item .label ="Claimed"
                            item .style =discord .ButtonStyle .secondary 

        await channel .send (
        view =view 
        )

        
        try :
            await interaction .message .delete ()
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=channel,
                user=interaction.user,
                context="Failed to remove obsolete closed ticket controls",
            )

    @discord .ui .button (
    label ="Generate Transcript",
    style =discord .ButtonStyle .primary ,
    custom_id ="zer_transcript"
    )
    async def transcript (self ,interaction ,button ):
        log_interaction (interaction .user ,"zer_transcript",interaction .channel )
        if not is_staff (interaction .user ):
            log_ticket ("Transcript Rejected (Not Staff)",interaction .channel ,interaction .user )
            await interaction .response .send_message (
            embed =error ("You do not have permission to generate transcripts."),
            ephemeral =True 
            )
            return 

        await interaction .response .defer (ephemeral =True )

        
        for item in self .children :
            item .disabled =True 
        try :
            await interaction .message .edit (view =self )
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to disable transcript controls",
            )

        try :
            file_path =await create_transcript (interaction .channel )
            await interaction .followup .send (
            content ="Transcript successfully generated. Download below:",
            file =discord .File (file_path ),
            ephemeral =True 
            )
        except Exception as error:
            reference = log_exception(
                "TRANSCRIPT",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Manual transcript generation failed",
            )
            await interaction .followup .send (
            embed =error (f"Failed to generate transcript. Error reference: `{reference}`"),
            ephemeral =True 
            )

            
        for item in self .children :
            item .disabled =False 
        try :
            await interaction .message .edit (view =self )
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to restore transcript controls",
            )

    @discord .ui .button (
    label ="Delete Channel",
    style =discord .ButtonStyle .danger ,
    custom_id ="zer_delete"
    )
    async def delete (self ,interaction ,button ):
        log_interaction (interaction .user ,"zer_delete",interaction .channel )
        if not is_owner (interaction .user ):
            log_ticket ("Delete Rejected (Not Owner)",interaction .channel ,interaction .user )
            await interaction .response .send_message (
            embed =error ("Only owners can delete ticket channels permanently."),
            ephemeral =True 
            )
            return 

            
        await interaction .response .defer (ephemeral =True )
        for item in self .children :
            item .disabled =True 
        try :
            await interaction .message .edit (view =self )
        except discord.HTTPException as error:
            log_exception(
                "VIEW",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Failed to disable ticket controls before deletion",
            )

        await interaction .followup .send (
        content ="Channel deletion initiated. Deleting channel in 5 seconds...",
        ephemeral =True 
        )

        
        channel_name =interaction .channel .name 
        channel_id = interaction.channel.id
        user_id =None 
        if interaction .channel .topic and "ticket_owner:"in interaction .channel .topic :
            try :
                topic_part =interaction .channel .topic .split ("|")[0 ].strip ()
                user_id =int (topic_part .replace ("ticket_owner:","").strip ())
            except ValueError :
                user_id =None

                
        if user_id is None :
            try :
                user_id =await get_ticket_owner (interaction .channel .id )
            except Exception as error:
                log_exception(
                    "DATABASE",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context="Failed to resolve ticket owner before deletion",
                )

        log_ticket ("Deletion Scheduled (5s)",interaction .channel ,interaction .user )

        await asyncio .sleep (5 )

        try :
            await interaction .channel .delete ()
            await mark_ticket_deleted(channel_id)
            from utils .logger import ticket_delete_report 
            ticket_delete_report (channel_name ,interaction .user ,user_id ,interaction .client )
        except Exception as error:
            reference = log_exception(
                "TICKET",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context="Ticket channel deletion failed",
            )
            await interaction.followup.send(
                f"The channel could not be deleted. Error reference: `{reference}`",
                ephemeral=True,
            )
