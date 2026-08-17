import discord 
from views.base import ReliableView
from utils .permissions import can_ban 
from utils .database import add_infraction 
from utils .logger import log_exception, log_interaction ,log_mod 


class UnbanConfirmView (ReliableView ):
    def __init__ (self ,author_id :int ,target_user ,target_name :str ,reason :str =None ):
        super ().__init__ (timeout =120 )
        self .author_id =author_id 
        self .target_user =target_user 
        self .target_name =target_name 
        self .reason =reason 

    @discord .ui .button (
    label ="Confirm",
    style =discord .ButtonStyle .success ,
    custom_id ="unbanz_confirm"
    )
    async def confirm_unban (self ,interaction :discord .Interaction ,button :discord .ui .Button ):
        log_interaction (interaction .user ,"unbanz_confirm",interaction .channel ,details =f"Target: {self .target_name }")
        if not can_ban (interaction .user )or interaction .user .id !=self .author_id :
            log_mod ("Unban Confirm Rejected (Unauthorized)",interaction .user ,self .target_name )
            await interaction .response .send_message (
            "Unauthorized action. Permission denied.",
            ephemeral =True 
            )
            return 

        await interaction .response .defer ()

        try :
            if isinstance (self .target_user ,(discord .Member ,discord .User )):
                await interaction .guild .unban (
                self .target_user ,
                reason =self .reason or "Unbanned via /unbanZ"
                )
                user_id =self .target_user .id 
            elif isinstance (self .target_user ,int ):
                await interaction .guild .unban (
                discord .Object (id =self .target_user ),
                reason =self .reason or "Unbanned via /unbanZ"
                )
                user_id =self .target_user 
            elif hasattr (self .target_user ,"id"):
                await interaction .guild .unban (
                discord .Object (id =self .target_user .id ),
                reason =self .reason or "Unbanned via /unbanZ"
                )
                user_id =self .target_user .id 
            else :
                await interaction .followup .send (
                f"Unable to unban target: Invalid user resolution for {self .target_name }.",
                ephemeral =True 
                )
                return 

            log_mod ("unbanned",interaction .user ,self .target_user or self .target_name ,reason =self .reason or "Unbanned via /unbanZ")

            await add_infraction (
            user_id =user_id ,
            moderator_id =interaction .user .id ,
            action_type ="UNBAN",
            reason =self .reason or "Unbanned via /unbanZ",
            guild_id =interaction.guild.id if interaction.guild else None,
            )

            desc =f"**{self .target_name }** has successfully been unbanned!"
            if self .reason :
                desc +=f"\n\n{self .reason }"

            embed =discord .Embed (
            title ="Unban",
            description =desc ,
            color =discord .Color .from_rgb (255 ,255 ,255 )
            )

            await interaction .message .edit (embed =embed ,view =None )

        except discord .NotFound :
            log_mod ("Unban Failed (User Not Banned)",interaction .user ,self .target_name )
            await interaction .followup .send (
            f"User not found in ban registry: **{self .target_name }** is not currently banned.",
            ephemeral =True 
            )
        except discord .Forbidden as error:
            log_mod ("Unban Failed (Bot Lacks Permission)",interaction .user ,self .target_name )
            reference =log_exception(
                "MODERATION",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context=f"Unban permission denied for {self.target_name}",
            )
            await interaction .followup .send (
            f"Failed to unban user: Bot lacks required administrative permissions. Error reference: `{reference}`",
            ephemeral =True 
            )
        except Exception as error :
            log_mod (f"Unban Failed ({error })",interaction .user ,self .target_name )
            reference =log_exception(
                "MODERATION",
                error,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                context=f"Unban failed for {self.target_name}",
            )
            await interaction .followup .send (
            f"Failed to unban user. Error reference: `{reference}`",
            ephemeral =True 
            )

    @discord .ui .button (
    label ="Cancel",
    style =discord .ButtonStyle .secondary ,
    custom_id ="unbanz_cancel"
    )
    async def cancel_unban (self ,interaction :discord .Interaction ,button :discord .ui .Button ):
        log_interaction (interaction .user ,"unbanz_cancel",interaction .channel ,details =f"Cancelled unban for {self .target_name }")
        if not can_ban (interaction .user )or interaction .user .id !=self .author_id :
            await interaction .response .send_message (
            "Unauthorized action. Permission denied.",
            ephemeral =True 
            )
            return 

        embed =discord .Embed (
        title ="Unban",
        description =f"Unban operation canceled for **{self .target_name }**.",
        color =discord .Color .from_rgb (255 ,255 ,255 )
        )
        await interaction .response .edit_message (embed =embed ,view =None )
