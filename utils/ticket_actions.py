import discord 
import asyncio 
import os 
import config 
from datetime import datetime 
import pytz 
from utils .database import close_ticket 
from utils .embeds import ticket_closed 
from views .closed_buttons import ClosedTicketButtons 
from cogs .transcript import create_transcript 
from utils .logger import log_dm ,log_ticket ,log_perm 

timezone =pytz .timezone ("Europe/Berlin")

async def close_ticket_channel (channel ,moderator ,reason ,bot ):
    from utils .embeds import error 
    log_ticket ("Closing Initiated",channel ,moderator ,details =f"Reason: {reason }")

    
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
            pass 

            
    if user_id is None :
        try :
            from utils .database import get_ticket_owner 
            user_id =await get_ticket_owner (channel .id )
        except Exception as e :
            print (f"Failed to query ticket owner from DB: {str (e )}")

    member =None 
    if user_id :
        try :
            member =channel .guild .get_member (user_id )
            if member is None :
                member =await channel .guild .fetch_member (user_id )
        except Exception :
            pass 

            
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
    except Exception as e :
        print (f"Failed to generate transcript on close: {str (e )}")

        
    dm_success =True 
    if member :
        try :
            dm_content =(
            f"Hello! Your application ticket (**#{channel .name }**) in **{channel .guild .name }** has been closed.\n\n"
            f"**Closed By:** {moderator .display_name }\n"
            f"**Reason:** {reason }\n\n"
            f"Attached is a complete offline transcript of your ticket."
            )
            if transcript_file :
                try :
                    await member .send (content =dm_content ,file =transcript_file )
                except discord .HTTPException as he :
                    if he .status ==413 or he .code ==40005 :
                        print ("Failed to send standard transcript zip due to size. Retrying with lightweight transcript...")
                        zip_path =await create_transcript (channel ,lightweight =True )
                        if zip_path and os .path .exists (zip_path ):
                            fallback_file =discord .File (zip_path )
                            await member .send (content =dm_content ,file =fallback_file )
                        else :
                            await member .send (content =dm_content )
                    else :
                        raise he 
            else :
                await member .send (content =dm_content )
            log_dm (member ,f"Ticket #{channel .name } Close Notice",success =True )
        except discord .Forbidden :
            dm_success =False 
            log_dm (member ,f"Ticket #{channel .name } Close Notice",success =False ,error_detail ="Direct Messages Disabled")
            print (f"Could not send DM to {member .display_name } (DMs are disabled).")
        except Exception as e :
            dm_success =False 
            log_dm (member ,f"Ticket #{channel .name } Close Notice",success =False ,error_detail =str (e ))
            print (f"Failed to send DM to applicant: {str (e )}")

            
    if member :
        try :
            await channel .set_permissions (
            member ,
            view_channel =False ,
            send_messages =False 
            )
            log_perm (channel ,member ,"Removed view_channel & send_messages")
        except Exception as e :
            print (f"Failed to remove permissions for ticket owner: {str (e )}")

            
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
    except discord .NotFound :
        print (f"Channel {channel .id } was deleted during close process. Aborting.")
        return 
    except Exception as e :
        print (f"Failed to send close audit embed: {str (e )}")

        
    if not dm_success and member :
        try :
            await channel .send (
            embed =error (
            f"Could not send DM to applicant **{member .display_name }** (DMs are disabled). "
            f"The offline transcript has been attached above for staff review."
            )
            )
        except discord .NotFound :
            print (f"Channel {channel .id } was deleted during close process. Aborting.")
            return 
        except Exception as e :
            print (f"Failed to send DM warning: {str (e )}")

            
    async def perform_background_close_tasks ():
        edit_kwargs ={}
        archive_category_id =config .get_archive_category_id (channel .guild .id )
        archive_category =channel .guild .get_channel (archive_category_id )
        if archive_category :
            edit_kwargs ["category"]=archive_category 
        else :
            print (f"Archive category {archive_category_id } not found.")


        if not channel .name .startswith ("closed-"):
            edit_kwargs ["name"]=f"closed-{channel .name }"

        if edit_kwargs :
            try :
                await channel .edit (**edit_kwargs )
                log_ticket ("Archived & Renamed Channel",channel ,moderator ,details =f"New category: Archive, Name: {edit_kwargs .get ('name',channel .name )}")
            except discord .NotFound :
                print (f"Channel {channel .id } was deleted during close process. Aborting.")
                return 
            except Exception as e :
                print (f"Failed to move/rename channel: {str (e )}")

                
        try :
            await channel .send (
            view =ClosedTicketButtons ()
            )
        except discord .NotFound :
            print (f"Channel {channel .id } was deleted during close process. Aborting.")
            return 
        except Exception as e :
            print (f"Failed to send closed ticket controls: {str (e )}")

            
        try :
            from utils .logger import ticket_close_report 
            ticket_close_report (channel ,moderator ,user_id ,reason ,zip_path ,bot )
        except Exception as e :
            print (f"Failed to send close report: {str (e )}")

    await perform_background_close_tasks()
    return True
