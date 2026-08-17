import discord 
import asyncio 
import os 
import config 
from datetime import datetime 
import pytz 
from utils .database import close_ticket, reopen_ticket 
from utils .embeds import ticket_closed, ticket_closed_dm 
from views .closed_buttons import ClosedTicketButtons 
from cogs .transcript import create_transcript 
from utils .logger import log_dm ,log_ticket ,log_perm, log_exception, log_transcript 

timezone =pytz .timezone ("Europe/Berlin")

async def close_ticket_channel (channel ,moderator ,reason ,bot ):
    from utils .embeds import error 
    log_ticket ("Closing Initiated",channel ,moderator ,details =f"Reason: {reason }")
    original_name =channel .name
    original_category =channel .category

    
    closed = await close_ticket (
    channel .id ,
    datetime .now (timezone ).isoformat (),
    moderator .id ,
    reason 
    )

    if not closed:
        return False

    
    user_id =None 
    if channel .topic and "ticket_owner:"in channel .topic :
        try :
            topic_part =channel .topic .split ("|")[0 ].strip ()
            user_id =int (topic_part .replace ("ticket_owner:","").strip ())
        except ValueError :
            user_id =None

            
    if user_id is None :
        try :
            from utils .database import get_ticket_owner 
            user_id =await get_ticket_owner (channel .id )
        except Exception as error:
            log_exception(
                "DATABASE",
                error,
                guild=channel.guild,
                channel=channel,
                user=moderator,
                context="Failed to resolve ticket owner during close",
            )

    member =None 
    if user_id :
        try :
            member =channel .guild .get_member (user_id )
            if member is None :
                member =await channel .guild .fetch_member (user_id )
        except discord.HTTPException as error:
            log_exception(
                "DISCORD",
                error,
                guild=channel.guild,
                channel=channel,
                user=user_id,
                context="Failed to fetch ticket owner during close",
            )

    async def rollback_close():
        try:
            await reopen_ticket(channel.id)
        except Exception as rollback_error:
            log_exception(
                "DATABASE",
                rollback_error,
                guild=channel.guild,
                channel=channel,
                user=moderator,
                context="Failed to roll back ticket database state",
            )
        try:
            edit_kwargs ={}
            if channel.name !=original_name:
                edit_kwargs["name"] =original_name
            if channel.category !=original_category:
                edit_kwargs["category"] =original_category
            if edit_kwargs:
                await channel.edit(**edit_kwargs)
        except discord.HTTPException as rollback_error:
            log_exception(
                "TICKET",
                rollback_error,
                guild=channel.guild,
                channel=channel,
                user=moderator,
                context="Failed to restore ticket channel after close failure",
            )
        if member:
            try:
                await channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )
            except discord.HTTPException as rollback_error:
                log_exception(
                    "PERMISSION",
                    rollback_error,
                    guild=channel.guild,
                    channel=channel,
                    user=member,
                    context="Failed to restore ticket owner permissions after close failure",
                )

            
    transcript_file =None 
    zip_path =None 
    try :
        zip_path =await create_transcript (channel )
        if zip_path and os .path .exists (zip_path ):
            if os .path .getsize (zip_path )>8000000 :
                print ("Transcript zip exceeds 8MB, generating lightweight transcript...")
                zip_path =await create_transcript (channel ,lightweight =True )
            if zip_path and os .path .exists (zip_path ):
                transcript_file =discord .File (zip_path )
    except Exception as transcript_error:
        log_exception(
            "TRANSCRIPT",
            transcript_error,
            guild=channel.guild,
            channel=channel,
            user=moderator,
            context="Automatic close transcript generation failed",
        )

        
    dm_success =True 
    if member :
        try :
            dm_embed =ticket_closed_dm(
                channel.guild,
                channel,
                moderator,
                reason,
                transcript_file is not None,
                getattr(bot, "user", None),
            )
            if transcript_file :
                try :
                    await member .send (embed =dm_embed ,file =transcript_file )
                except discord .HTTPException as he :
                    if he .status ==413 or he .code ==40005 :
                        log_transcript(
                            "Standard transcript exceeded Discord upload limit",
                            channel,
                            details="Retrying with lightweight transcript",
                        )
                        zip_path =await create_transcript (channel ,lightweight =True )
                        if zip_path and os .path .exists (zip_path ):
                            fallback_file =discord .File (zip_path )
                            fallback_embed =ticket_closed_dm(
                                channel.guild,
                                channel,
                                moderator,
                                reason,
                                True,
                                getattr(bot, "user", None),
                            )
                            await member .send (embed =fallback_embed ,file =fallback_file )
                        else :
                            unavailable_embed =ticket_closed_dm(
                                channel.guild,
                                channel,
                                moderator,
                                reason,
                                False,
                                getattr(bot, "user", None),
                            )
                            await member .send (embed =unavailable_embed )
                    else :
                        raise he 
            else :
                await member .send (embed =dm_embed )
            log_dm (member ,f"Ticket #{channel .name } Close Notice",success =True )
        except discord .Forbidden :
            dm_success =False 
            log_dm (member ,f"Ticket #{channel .name } Close Notice",success =False ,error_detail ="Direct Messages Disabled")
        except Exception as dm_error:
            dm_success =False 
            log_dm (member ,f"Ticket #{channel .name } Close Notice",success =False ,error_detail =str (dm_error ))
            log_exception(
                "DM",
                dm_error,
                guild=channel.guild,
                channel=channel,
                user=member,
                context="Failed to deliver ticket close notice",
            )

            
    if member :
        try :
            await channel .set_permissions (
            member ,
            view_channel =False ,
            send_messages =False 
            )
            log_perm (channel ,member ,"Removed view_channel & send_messages")
        except discord.HTTPException as permission_error:
            await rollback_close()
            raise RuntimeError("Failed to remove ticket owner permissions during close") from permission_error

            
    try :
        if zip_path and os .path .exists (zip_path ):
            channel_transcript_file =discord .File (zip_path )
            await channel .send (
            embed =ticket_closed (moderator ,reason ,applicant =member ),
            file =channel_transcript_file 
            )
        else :
            await channel .send (
            embed =ticket_closed (moderator ,reason ,applicant =member )
            )
    except discord.HTTPException as audit_error:
        await rollback_close()
        raise RuntimeError("Failed to publish ticket close audit") from audit_error

        
    if not dm_success and member :
        try :
            await channel .send (
            embed =error (
            f"Could not send DM to applicant **{member .display_name }** (DMs are disabled). "
            f"The offline transcript has been attached above for staff review."
            )
            )
        except discord.HTTPException as warning_error:
            log_exception(
                "TICKET",
                warning_error,
                guild=channel.guild,
                channel=channel,
                user=moderator,
                context="Failed to publish applicant DM warning",
            )

            
    async def perform_background_close_tasks ():
        edit_kwargs ={}
        archive_category_id =config .get_archive_category_id (channel .guild .id )
        archive_category =channel .guild .get_channel (archive_category_id )
        if archive_category :
            edit_kwargs ["category"]=archive_category 
        else :
            log_ticket(
                "Archive Category Missing",
                channel,
                moderator,
                details=f"Category ID: {archive_category_id}",
            )


        if not channel .name .startswith ("closed-"):
            edit_kwargs ["name"]=f"closed-{channel .name }"

        if edit_kwargs :
            try :
                await channel .edit (**edit_kwargs )
                log_ticket ("Archived & Renamed Channel",channel ,moderator ,details =f"New category: Archive, Name: {edit_kwargs .get ('name',channel .name )}")
            except discord.HTTPException as channel_error:
                await rollback_close()
                raise RuntimeError("Failed to archive or rename ticket channel") from channel_error

                
        try :
            await channel .send (
            view =ClosedTicketButtons ()
            )
        except discord.HTTPException as controls_error:
            await rollback_close()
            raise RuntimeError("Failed to publish closed ticket controls") from controls_error

            
        try :
            from utils .logger import ticket_close_report 
            ticket_close_report (channel ,moderator ,user_id ,reason ,zip_path ,bot )
        except Exception as report_error:
            log_exception(
                "TICKET",
                report_error,
                guild=channel.guild,
                channel=channel,
                user=moderator,
                context="Failed to record ticket close report",
            )

    await perform_background_close_tasks()
    return True
