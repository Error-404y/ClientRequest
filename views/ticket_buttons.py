import discord 
import asyncio 
import os 
import config 
from discord .ui import View ,Button 
from utils .permissions import is_staff 
from utils .database import claim_ticket 
from utils .embeds import error 
from views .closed_buttons import ClosedTicketButtons 
from datetime import datetime 
import pytz 
from utils .ticket_actions import close_ticket_channel 
from utils .logger import ticket_claim_report ,log_interaction ,log_ticket ,log_dm 

timezone =pytz .timezone ("Europe/Berlin")

class CloseTicketModal (discord .ui .Modal ,title ="Close Ticket"):
    reason =discord .ui .TextInput (
    label ="Reason for closing",
    placeholder ="Enter the reason for closing this ticket...",
    style =discord .TextStyle .paragraph ,
    required =True ,
    max_length =500 
    )

    def __init__ (self ,original_view ):
        super ().__init__ ()
        self .original_view =original_view 

    async def on_submit (self ,interaction :discord .Interaction ):
        log_interaction (interaction .user ,"CloseTicketModal",interaction .channel ,details =f"Reason: {self .reason .value }")
        await interaction .response .defer ()

        
        for item in self .original_view .children :
            item .disabled =True 
        try :
            await interaction .message .edit (view =self .original_view )
        except Exception :
            pass 

            
        await close_ticket_channel (
        channel =interaction .channel ,
        moderator =interaction .user ,
        reason =self .reason .value ,
        bot =interaction .client 
        )


class PrioritySelectionView (discord .ui .View ):
    def __init__ (self ,original_channel ):
        super ().__init__ (timeout =60 )
        self .original_channel =original_channel 

    @discord .ui .button (label ="Low",style =discord .ButtonStyle .success ,custom_id ="priority_low")
    async def set_low (self ,interaction :discord .Interaction ,button :discord .ui .Button ):
        await self .update_priority (interaction ,"Low")

    @discord .ui .button (label ="Medium",style =discord .ButtonStyle .primary ,custom_id ="priority_medium")
    async def set_medium (self ,interaction :discord .Interaction ,button :discord .ui .Button ):
        await self .update_priority (interaction ,"Medium")

    @discord .ui .button (label ="High",style =discord .ButtonStyle .danger ,custom_id ="priority_high")
    async def set_high (self ,interaction :discord .Interaction ,button :discord .ui .Button ):
        await self .update_priority (interaction ,"High")

    async def update_priority (self ,interaction :discord .Interaction ,priority :str ):
        log_interaction (interaction .user ,f"priority_{priority .lower ()}",self .original_channel ,details =f"New Priority: {priority }")
        from utils .database import set_ticket_priority 
        await set_ticket_priority (self .original_channel .id ,priority )

        current_name =self .original_channel .name 
        is_closed =current_name .startswith ("closed-")
        if is_closed :
            name_without_closed =current_name .replace ("closed-","",1 )
        else :
            name_without_closed =current_name 

        for p in ["low-","medium-","high-"]:
            if name_without_closed .startswith (p ):
                name_without_closed =name_without_closed .replace (p ,"",1 )
                break 

        prefix_map ={
        "Low":"low-",
        "Medium":"medium-",
        "High":"high-"
        }
        new_prefix =prefix_map .get (priority ,"")
        new_name =f"{new_prefix }{name_without_closed }"
        if is_closed :
            new_name =f"closed-{new_name }"

            
        async def rename_bg ():
            try :
                await self .original_channel .edit (name =new_name )
                log_ticket ("Priority Rename Completed",self .original_channel ,interaction .user ,details =f"New channel name: {new_name }")
            except Exception as e :
                print (f"Failed to rename channel to {new_name }: {str (e )}")
        await rename_bg()

        await interaction .response .edit_message (content =f"Priority successfully set to **{priority }**.",view =None )

        embed =discord .Embed (
        title ="Ticket Priority Updated",
        description =f"This ticket's priority has been updated.",
        color =discord .Color .blue ()
        )
        embed .add_field (name ="New Priority",value =priority ,inline =True )
        embed .add_field (name ="Updated By",value =interaction .user .mention ,inline =True )
        embed .set_footer (text =config.BOT_NAME )
        await self .original_channel .send (embed =embed )


class TicketButtons (View ):
    def __init__ (self ):
        super ().__init__ (timeout =None )

    @discord .ui .button (
    label ="Claim Ticket",
    style =discord .ButtonStyle .primary ,
    custom_id ="zer_claim"
    )
    async def claim (self ,interaction ,button ):
        log_interaction (interaction .user ,"zer_claim",interaction .channel )
        if not is_staff (interaction .user ):
            log_ticket ("Claim Rejected (Not Staff)",interaction .channel ,interaction .user )
            await interaction .response .send_message (
            embed =error ("You do not have permission to claim this ticket."),
            ephemeral =True 
            )
            return 

            
        await interaction .response .defer ()

        
        claimed_at =datetime .now (timezone ).isoformat ()
        claimed = await claim_ticket(interaction.channel.id, interaction.user.id, claimed_at)
        if not claimed:
            await interaction.followup.send("This ticket has already been claimed or is no longer open.", ephemeral=True)
            return

        
        channel =interaction .channel 
        owner_id =None 
        if channel .topic and "ticket_owner:"in channel .topic :
            parts =channel .topic .split ("|")
            owner_part =parts [0 ].strip ()
            channel_topic =f"{owner_part } | claimed_by:{interaction .user .id }"
            try :
                owner_id =int (owner_part .replace ("ticket_owner:","").strip ())
            except ValueError :
                pass 
        else :
            channel_topic =f"claimed_by:{interaction .user .id }"

            
        async def edit_topic_bg ():
            try :
                await channel .edit (topic =channel_topic )
            except Exception :
                pass 
        await edit_topic_bg()

        
        button .disabled =True 
        button .label =f"Claimed by {interaction .user .display_name }"
        button .style =discord .ButtonStyle .secondary 

        try :
            await interaction .message .edit (view =self )
        except Exception :
            pass 

            
        await interaction .followup .send (
        f"This ticket has been claimed by {interaction .user .mention }."
        )

        
        ticket_claim_report (channel ,interaction .user ,owner_id ,interaction .client )

        
        if owner_id :
            try :
                owner =interaction .guild .get_member (owner_id )
                if owner is None :
                    owner =await interaction .guild .fetch_member (owner_id )

                if owner :
                    await owner .send (
                    f"Your application ticket in **{interaction .guild .name }** has been claimed by **{interaction .user .display_name }** and is now under review."
                    )
                    log_dm (owner ,"Ticket Claimed Notice",success =True )
            except discord .Forbidden :
                log_dm (owner_id ,"Ticket Claimed Notice",success =False ,error_detail ="Direct Messages Disabled")
                print (f"Could not send DM to applicant {owner_id } (DMs are disabled).")
            except Exception as e :
                log_dm (owner_id ,"Ticket Claimed Notice",success =False ,error_detail =str (e ))
                print (f"Failed to DM applicant: {str (e )}")

    @discord .ui .button (
    label ="Close Ticket",
    style =discord .ButtonStyle .danger ,
    custom_id ="zer_close"
    )
    async def close (self ,interaction ,button ):
        log_interaction (interaction .user ,"zer_close",interaction .channel )
        if not is_staff (interaction .user ):
            log_ticket ("Close Rejected (Not Staff)",interaction .channel ,interaction .user )
            await interaction .response .send_message (
            embed =error ("You do not have permission to close this ticket."),
            ephemeral =True 
            )
            return 

            
        await interaction .response .send_modal (CloseTicketModal (self ))

    @discord .ui .button (
    label ="Set Priority",
    style =discord .ButtonStyle .secondary ,
    custom_id ="zer_priority"
    )
    async def set_priority (self ,interaction ,button ):
        log_interaction (interaction .user ,"zer_priority",interaction .channel )
        if not is_staff (interaction .user ):
            log_ticket ("Set Priority Rejected (Not Staff)",interaction .channel ,interaction .user )
            await interaction .response .send_message (
            embed =error ("You do not have permission to change ticket priority."),
            ephemeral =True 
            )
            return 

            
        view =PrioritySelectionView (interaction .channel )
        await interaction .response .send_message (
        content ="Select the priority level for this ticket:",
        view =view ,
        ephemeral =True 
        )
