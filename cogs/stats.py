import discord 
from discord .ext import commands 
import aiosqlite 
import config 
from utils .permissions import is_staff 
from utils .embeds import error 
from datetime import datetime 
import pytz 
from utils .logger import log_command, log_exception 

timezone =pytz .timezone ("Europe/Berlin")

def format_duration (seconds ):
    if seconds is None or seconds <0 :
        return "N/A"
    if seconds <60 :
        return f"{int (seconds )}s"
    minutes =seconds /60 
    if minutes <60 :
        return f"{int (minutes )}m {int (seconds %60 )}s"
    hours =minutes /60 
    if hours <24 :
        return f"{int (hours )}h {int (minutes %60 )}m"
    days =hours /24 
    return f"{int (days )}d {int (hours %24 )}h"

class Stats (commands .Cog ):
    def __init__ (self ,bot ):
        self .bot =bot 

    @commands .group (name ="stats",invoke_without_command =True )
    async def stats (self ,ctx ,member :discord .Member =None ):
        """Shows staff statistics for a moderator."""
        log_command (ctx .author ,"!stats",ctx .channel ,details =f"target={member .display_name if member else 'self'}")
        if not is_staff (ctx .author ):
            await ctx .send (embed =error ("You do not have permission to view staff statistics."))
            return 

        target_member =member or ctx .author 
        if not is_staff (target_member ):
            await ctx .send (embed =error (f"**{target_member .display_name }** is not a staff member."))
            return 

        gid =ctx .guild .id if ctx .guild else None 

        async with aiosqlite .connect (config .DATABASE )as db :

            if gid :
                cursor =await db .execute (
                "SELECT COUNT(*) FROM tickets WHERE claimed_by = ? AND guild_id = ?",
                (target_member .id ,gid )
                )
            else :
                cursor =await db .execute (
                "SELECT COUNT(*) FROM tickets WHERE claimed_by = ?",
                (target_member .id ,)
                )
            claimed_count =(await cursor .fetchone ())[0 ]

            if gid :
                cursor =await db .execute (
                "SELECT COUNT(*) FROM tickets WHERE closed_by = ? AND guild_id = ?",
                (target_member .id ,gid )
                )
            else :
                cursor =await db .execute (
                "SELECT COUNT(*) FROM tickets WHERE closed_by = ?",
                (target_member .id ,)
                )
            closed_count =(await cursor .fetchone ())[0 ]

            if gid :
                cursor =await db .execute (
                "SELECT created_at, claimed_at FROM tickets WHERE claimed_by = ? AND claimed_at IS NOT NULL AND guild_id = ?",
                (target_member .id ,gid )
                )
            else :
                cursor =await db .execute (
                "SELECT created_at, claimed_at FROM tickets WHERE claimed_by = ? AND claimed_at IS NOT NULL",
                (target_member .id ,)
                )
            claimed_rows =await cursor .fetchall ()

            total_claim_seconds =0 
            valid_claim_counts =0 
            for created_at_str ,claimed_at_str in claimed_rows :
                try :
                    created_at =datetime .fromisoformat (created_at_str )
                    claimed_at =datetime .fromisoformat (claimed_at_str )
                    diff =(claimed_at -created_at ).total_seconds ()
                    if diff >=0 :
                        total_claim_seconds +=diff 
                        valid_claim_counts +=1 
                except (TypeError, ValueError) as error:
                    log_exception(
                        "DATABASE",
                        error,
                        guild=ctx.guild,
                        channel=ctx.channel,
                        user=target_member,
                        context="Invalid ticket timestamps while calculating claim statistics",
                    )

            avg_claim_time =(
            format_duration (total_claim_seconds /valid_claim_counts )
            if valid_claim_counts >0 
            else "N/A"
            )

            if gid :
                cursor =await db .execute (
                "SELECT claimed_at, closed_at FROM tickets WHERE claimed_by = ? AND claimed_at IS NOT NULL AND closed_at IS NOT NULL AND guild_id = ?",
                (target_member .id ,gid )
                )
            else :
                cursor =await db .execute (
                "SELECT claimed_at, closed_at FROM tickets WHERE claimed_by = ? AND claimed_at IS NOT NULL AND closed_at IS NOT NULL",
                (target_member .id ,)
                )
            resolved_rows =await cursor .fetchall ()

            total_resolve_seconds =0 
            valid_resolve_counts =0 
            for claimed_at_str ,closed_at_str in resolved_rows :
                try :
                    claimed_at =datetime .fromisoformat (claimed_at_str )
                    closed_at =datetime .fromisoformat (closed_at_str )
                    diff =(closed_at -claimed_at ).total_seconds ()
                    if diff >=0 :
                        total_resolve_seconds +=diff 
                        valid_resolve_counts +=1 
                except (TypeError, ValueError) as error:
                    log_exception(
                        "DATABASE",
                        error,
                        guild=ctx.guild,
                        channel=ctx.channel,
                        user=target_member,
                        context="Invalid ticket timestamps while calculating resolution statistics",
                    )

            avg_resolve_time =(
            format_duration (total_resolve_seconds /valid_resolve_counts )
            if valid_resolve_counts >0 
            else "N/A"
            )

        embed =discord .Embed (
        title =f"Staff Stats - {target_member .display_name }",
        color =discord .Color .blurple ()
        )
        embed .set_thumbnail (url =target_member .display_avatar .url )
        embed .add_field (name ="Tickets Claimed",value =f" `{claimed_count }`",inline =True )
        embed .add_field (name ="Tickets Closed",value =f" `{closed_count }`",inline =True )
        embed .add_field (name ="Avg. Claim Response",value =f" `{avg_claim_time }`",inline =False )
        embed .add_field (name ="Avg. Resolution Time",value =f" `{avg_resolve_time }`",inline =False )
        embed .set_footer (text =f"{config.BOT_NAME} | Staff Management")

        await ctx .send (embed =embed )

    @stats .command (name ="leaderboard",aliases =["lb"])
    async def leaderboard (self ,ctx ):
        """Shows the staff leaderboard for ticket claims and closures."""
        log_command (ctx .author ,"!stats leaderboard",ctx .channel )
        if not is_staff (ctx .author ):
            await ctx .send (embed =error ("You do not have permission to view staff leaderboard."))
            return 

        gid =ctx .guild .id if ctx .guild else None 
        async with aiosqlite .connect (config .DATABASE )as db :

            if gid :
                cursor =await db .execute (
                "SELECT claimed_by, COUNT(*) as count FROM tickets WHERE claimed_by IS NOT NULL AND guild_id = ? GROUP BY claimed_by ORDER BY count DESC LIMIT 10",
                (gid ,)
                )
            else :
                cursor =await db .execute (
                "SELECT claimed_by, COUNT(*) as count FROM tickets WHERE claimed_by IS NOT NULL GROUP BY claimed_by ORDER BY count DESC LIMIT 10"
                )
            claims_rows =await cursor .fetchall ()

            if gid :
                cursor =await db .execute (
                "SELECT closed_by, COUNT(*) as count FROM tickets WHERE closed_by IS NOT NULL AND guild_id = ? GROUP BY closed_by ORDER BY count DESC LIMIT 10",
                (gid ,)
                )
            else :
                cursor =await db .execute (
                "SELECT closed_by, COUNT(*) as count FROM tickets WHERE closed_by IS NOT NULL GROUP BY closed_by ORDER BY count DESC LIMIT 10"
                )
            closures_rows =await cursor .fetchall ()

        claims_list =[]
        for index ,(user_id ,count )in enumerate (claims_rows ,start =1 ):
            member =ctx .guild .get_member (user_id )
            name =member .mention if member else f"User ID: {user_id }"
            claims_list .append (f"`#{index }` {name } - **{count }** claims")

        claims_str ="\n".join (claims_list )if claims_list else "*No claim data available.*"

        closures_list =[]
        for index ,(user_id ,count )in enumerate (closures_rows ,start =1 ):
            member =ctx .guild .get_member (user_id )
            name =member .mention if member else f"User ID: {user_id }"
            closures_list .append (f"`#{index }` {name } - **{count }** closures")

        closures_str ="\n".join (closures_list )if closures_list else "*No closure data available.*"

        embed =discord .Embed (
        title =f"{config.BOT_NAME} Staff Leaderboard",
        color =discord .Color .gold (),
        description ="Leaderboard of active staff members sorted by claims and closures."
        )
        embed .add_field (name ="Top Claims ",value =claims_str ,inline =False )
        embed .add_field (name ="Top Closures ",value =closures_str ,inline =False )
        embed .set_footer (text =f"{config.BOT_NAME} | Staff Management")

        await ctx .send (embed =embed )

async def setup (bot ):
    await bot .add_cog (Stats (bot ))
