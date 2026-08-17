import re 
from datetime import datetime 

import discord 
from discord import app_commands 
from discord .ext import commands 

import config 

from utils .permissions import (
can_ban ,
can_kick ,
can_warn_or_view_history ,
)
from utils .embeds import error 
from utils .database import (
add_infraction ,
get_user_infractions ,
remove_user_warning ,
increment_user_activity ,
get_user_stats ,
get_infraction_by_uuid ,
get_ticket_by_uuid ,
remove_infraction_by_uuid ,
)
from utils .logger import (
log_command ,
log_mod ,
log_dm ,
log_filter ,
log_exception,
)
from views .ban_buttons import BanConfirmView 
from views .unban_buttons import UnbanConfirmView 
from views .kick_buttons import KickConfirmView 


async def resolve_user (
guild :discord .Guild ,
bot :commands .Bot ,
user_input :str ,
):

    if not user_input :
        return None ,"",None 

    clean_input =user_input .strip ()

    mention_match =re .match (r"^<@!?(\d+)>$",clean_input )

    if mention_match :
        user_id =int (mention_match .group (1 ))

        member =guild .get_member (user_id )if guild else None 
        if member :
            return member ,member .name ,user_id 

        try :
            fetched =await bot .fetch_user (user_id )
            return fetched ,fetched .name ,user_id 
        except discord.HTTPException as error:
            log_exception(
                "DISCORD",
                error,
                guild=guild,
                user=user_id,
                context="Failed to resolve mentioned moderation target",
            )
            return user_id ,str (user_id ),user_id 

    if clean_input .isdigit ():
        user_id =int (clean_input )

        member =guild .get_member (user_id )if guild else None 
        if member :
            return member ,member .name ,user_id 

        try :
            fetched =await bot .fetch_user (user_id )
            return fetched ,fetched .name ,user_id 
        except discord.HTTPException as error:
            log_exception(
                "DISCORD",
                error,
                guild=guild,
                user=user_id,
                context="Failed to resolve numeric moderation target",
            )
            return user_id ,clean_input ,user_id 

    if guild :
        search_term =clean_input .lstrip ("@").lower ()


        for member in guild .members :
            if (
            member .name .lower ()==search_term 
            or (
            member .global_name 
            and member .global_name .lower ()==search_term 
            )
            or member .display_name .lower ()==search_term 
            or str (member ).lower ()==search_term 
            ):
                return member ,member .name ,member .id 


        for member in guild .members :
            if (
            search_term in member .name .lower ()
            or (
            member .global_name 
            and search_term in member .global_name .lower ()
            )
            or search_term in member .display_name .lower ()
            ):
                return member ,member .name ,member .id 

    return None ,clean_input .lstrip ("@"),None 


async def resolve_banned_user (
guild :discord .Guild ,
bot :commands .Bot ,
user_input :str ,
):

    if not user_input :
        return None ,"",None 

    clean_input =user_input .strip ()
    user_id =None 

    mention_match =re .match (r"^<@!?(\d+)>$",clean_input )

    if mention_match :
        user_id =int (mention_match .group (1 ))
    elif clean_input .isdigit ():
        user_id =int (clean_input )

    ban_entries =[]

    if guild :
        try :
            ban_entries =[entry async for entry in guild .bans ()]
        except discord.HTTPException as error:
            log_exception(
                "MODERATION",
                error,
                guild=guild,
                context="Failed to retrieve server ban list",
            )
            ban_entries =[]

    if user_id :
        for entry in ban_entries :
            if entry .user .id ==user_id :
                return entry .user ,entry .user .name ,entry .user .id 

        try :
            fetched =await bot .fetch_user (user_id )
            return fetched ,fetched .name ,user_id 
        except discord.HTTPException as error:
            log_exception(
                "DISCORD",
                error,
                guild=guild,
                user=user_id,
                context="Failed to resolve banned user",
            )
            return user_id ,str (user_id ),user_id 

    search_term =clean_input .lstrip ("@").lower ()

    for entry in ban_entries :
        user =entry .user 

        if (
        user .name .lower ()==search_term 
        or (
        user .global_name 
        and user .global_name .lower ()==search_term 
        )
        or str (user ).lower ()==search_term 
        ):
            return user ,user .name ,user .id 

    return None ,clean_input .lstrip ("@"),None 


class BanCog (commands .Cog ):
    def __init__ (self ,bot :commands .Bot ):
        self .bot =bot 





    @commands .Cog .listener ()
    async def on_message (self ,message :discord .Message ):
        if message .author .bot :
            return 

        if not message .guild :
            return 

        content_lower =message .content .lower ()
        has_bad_word =False 
        detected_words =[]
        bad_words_list =getattr (config ,"BAD_WORDS",[])

        for bad_word in bad_words_list :
            pattern =r"\b"+re .escape (bad_word )+r"\b"

            if re .search (pattern ,content_lower ):
                has_bad_word =True 
                detected_words .append (bad_word )

        await increment_user_activity (
        message .author .id ,
        guild_id =message .guild .id ,
        has_bad_word =has_bad_word ,
        )

        if has_bad_word :
            log_filter (
            message .author ,
            detected_words ,
            message .channel ,
            )

            await add_infraction (
            user_id =message .author .id ,
            moderator_id =self .bot .user .id ,
            action_type ="BAD_WORD",
            reason =(
            "Used bad word(s): "
            +", ".join (detected_words )
            ),
            guild_id =message .guild .id ,
            )





    @app_commands .command (
    name ="banz",
    description ="Ban a user from the server with confirmation",
    )
    @app_commands .describe (
    user ="Username, User Mention, or User ID to ban",
    reason ="Reason for the ban (optional)",
    )
    async def banz_slash (
    self ,
    interaction :discord .Interaction ,
    user :str ,
    reason :str =None ,
    ):
        log_command (
        interaction .user ,
        "/banz",
        interaction .channel ,
        f"user={user }, reason={reason }",
        )

        if not can_ban (interaction .user ):
            log_mod (
            "Permission Denied for /banz",
            interaction .user ,
            user ,
            )

            await interaction .response .send_message (
            embed =error (
            "You do not have permission to use this command."
            ),
            ephemeral =True ,
            )
            return 

        target_obj ,target_name ,target_id =await resolve_user (
        interaction .guild ,
        self .bot ,
        user ,
        )

        if not target_obj and not target_id :
            await interaction .response .send_message (
            embed =error (
            f"Could not find or resolve user: `{user }`"
            ),
            ephemeral =True ,
            )
            return 

        description =(
        f"Are you sure you want to ban **{target_name }**?"
        )

        if reason :
            description +=f"\n\n{reason }"

        embed =discord .Embed (
        title ="Ban",
        description =description ,
        color =discord .Color .from_rgb (255 ,255 ,255 ),
        )

        view =BanConfirmView (
        author_id =interaction .user .id ,
        target_user =target_obj or target_id ,
        target_name =target_name ,
        reason =reason ,
        )

        await interaction .response .send_message (
        embed =embed ,
        view =view ,
        )

    @commands .command (
    name ="banZ",
    aliases =["banz"],
    )
    async def banz_prefix (
    self ,
    ctx :commands .Context ,
    user_input :str =None ,
    *,
    reason :str =None ,
    ):
        log_command (
        ctx .author ,
        "!banZ",
        ctx .channel ,
        f"user={user_input }, reason={reason }",
        )

        if not can_ban (ctx .author ):
            log_mod (
            "Permission Denied for !banZ",
            ctx .author ,
            user_input ,
            )

            await ctx .send (
            embed =error (
            "You do not have permission to use this command."
            )
            )
            return 

        if not user_input :
            await ctx .send (
            embed =error (
            "Please specify a Username, Mention, or User ID to ban."
            )
            )
            return 

        target_obj ,target_name ,target_id =await resolve_user (
        ctx .guild ,
        self .bot ,
        user_input ,
        )

        if not target_obj and not target_id :
            await ctx .send (
            embed =error (
            f"Could not find or resolve user: `{user_input }`"
            )
            )
            return 

        description =(
        f"Are you sure you want to ban **{target_name }**?"
        )

        if reason :
            description +=f"\n\n{reason }"

        embed =discord .Embed (
        title ="Ban",
        description =description ,
        color =discord .Color .from_rgb (255 ,255 ,255 ),
        )

        view =BanConfirmView (
        author_id =ctx .author .id ,
        target_user =target_obj or target_id ,
        target_name =target_name ,
        reason =reason ,
        )

        await ctx .send (
        embed =embed ,
        view =view ,
        )





    @app_commands .command (
    name ="unbanz",
    description ="Unban a user from the server with confirmation",
    )
    @app_commands .describe (
    user ="Username, User Mention, or User ID to unban",
    reason ="Reason for unbanning (optional)",
    )
    async def unbanz_slash (
    self ,
    interaction :discord .Interaction ,
    user :str ,
    reason :str =None ,
    ):
        log_command (
        interaction .user ,
        "/unbanz",
        interaction .channel ,
        f"user={user }, reason={reason }",
        )

        if not can_ban (interaction .user ):
            log_mod (
            "Permission Denied for /unbanz",
            interaction .user ,
            user ,
            )

            await interaction .response .send_message (
            embed =error (
            "You do not have permission to use this command."
            ),
            ephemeral =True ,
            )
            return 

        target_obj ,target_name ,target_id =await resolve_banned_user (
        interaction .guild ,
        self .bot ,
        user ,
        )

        if not target_obj and not target_id :
            await interaction .response .send_message (
            embed =error (
            f"Could not resolve banned user: `{user }`"
            ),
            ephemeral =True ,
            )
            return 

        description =(
        f"Are you sure you want to unban **{target_name }**?"
        )

        if reason :
            description +=f"\n\n{reason }"

        embed =discord .Embed (
        title ="Unban",
        description =description ,
        color =discord .Color .from_rgb (255 ,255 ,255 ),
        )

        view =UnbanConfirmView (
        author_id =interaction .user .id ,
        target_user =target_obj or target_id ,
        target_name =target_name ,
        reason =reason ,
        )

        await interaction .response .send_message (
        embed =embed ,
        view =view ,
        )

    @commands .command (
    name ="unbanZ",
    aliases =["unbanz"],
    )
    async def unbanz_prefix (
    self ,
    ctx :commands .Context ,
    user_input :str =None ,
    *,
    reason :str =None ,
    ):
        log_command (
        ctx .author ,
        "!unbanZ",
        ctx .channel ,
        f"user={user_input }, reason={reason }",
        )

        if not can_ban (ctx .author ):
            log_mod (
            "Permission Denied for !unbanZ",
            ctx .author ,
            user_input ,
            )

            await ctx .send (
            embed =error (
            "You do not have permission to use this command."
            )
            )
            return 

        if not user_input :
            await ctx .send (
            embed =error (
            "Please specify a Username or User ID to unban."
            )
            )
            return 

        target_obj ,target_name ,target_id =await resolve_banned_user (
        ctx .guild ,
        self .bot ,
        user_input ,
        )

        if not target_obj and not target_id :
            await ctx .send (
            embed =error (
            f"Could not resolve banned user: `{user_input }`"
            )
            )
            return 

        description =(
        f"Are you sure you want to unban **{target_name }**?"
        )

        if reason :
            description +=f"\n\n{reason }"

        embed =discord .Embed (
        title ="Unban",
        description =description ,
        color =discord .Color .from_rgb (255 ,255 ,255 ),
        )

        view =UnbanConfirmView (
        author_id =ctx .author .id ,
        target_user =target_obj or target_id ,
        target_name =target_name ,
        reason =reason ,
        )

        await ctx .send (
        embed =embed ,
        view =view ,
        )





    @app_commands .command (
    name ="kickz",
    description ="Kick a user from the server with confirmation",
    )
    @app_commands .describe (
    user ="Username, User Mention, or User ID to kick",
    reason ="Reason for the kick (optional)",
    )
    async def kickz_slash (
    self ,
    interaction :discord .Interaction ,
    user :str ,
    reason :str =None ,
    ):
        log_command (
        interaction .user ,
        "/kickz",
        interaction .channel ,
        f"user={user }, reason={reason }",
        )

        if not can_kick (interaction .user ):
            log_mod (
            "Permission Denied for /kickz",
            interaction .user ,
            user ,
            )

            await interaction .response .send_message (
            embed =error (
            "You do not have permission to use this command."
            ),
            ephemeral =True ,
            )
            return 

        target_obj ,target_name ,target_id =await resolve_user (
        interaction .guild ,
        self .bot ,
        user ,
        )

        if not target_obj and not target_id :
            await interaction .response .send_message (
            embed =error (
            f"Could not find or resolve user: `{user }`"
            ),
            ephemeral =True ,
            )
            return 

        description =(
        f"Are you sure you want to kick **{target_name }**?"
        )

        if reason :
            description +=f"\n\n{reason }"

        embed =discord .Embed (
        title ="Kick",
        description =description ,
        color =discord .Color .from_rgb (255 ,255 ,255 ),
        )

        view =KickConfirmView (
        author_id =interaction .user .id ,
        target_user =target_obj or target_id ,
        target_name =target_name ,
        reason =reason ,
        )

        await interaction .response .send_message (
        embed =embed ,
        view =view ,
        )

    @commands .command (
    name ="kickZ",
    aliases =["kickz"],
    )
    async def kickz_prefix (
    self ,
    ctx :commands .Context ,
    user_input :str =None ,
    *,
    reason :str =None ,
    ):
        log_command (
        ctx .author ,
        "!kickZ",
        ctx .channel ,
        f"user={user_input }, reason={reason }",
        )

        if not can_kick (ctx .author ):
            log_mod (
            "Permission Denied for !kickZ",
            ctx .author ,
            user_input ,
            )

            await ctx .send (
            embed =error (
            "You do not have permission to use this command."
            )
            )
            return 

        if not user_input :
            await ctx .send (
            embed =error (
            "Please specify a Username, Mention, or User ID to kick."
            )
            )
            return 

        target_obj ,target_name ,target_id =await resolve_user (
        ctx .guild ,
        self .bot ,
        user_input ,
        )

        if not target_obj and not target_id :
            await ctx .send (
            embed =error (
            f"Could not find or resolve user: `{user_input }`"
            )
            )
            return 

        description =(
        f"Are you sure you want to kick **{target_name }**?"
        )

        if reason :
            description +=f"\n\n{reason }"

        embed =discord .Embed (
        title ="Kick",
        description =description ,
        color =discord .Color .from_rgb (255 ,255 ,255 ),
        )

        view =KickConfirmView (
        author_id =ctx .author .id ,
        target_user =target_obj or target_id ,
        target_name =target_name ,
        reason =reason ,
        )

        await ctx .send (
        embed =embed ,
        view =view ,
        )





    async def _issue_warning (
    self ,
    *,
    guild :discord .Guild ,
    moderator ,
    target_obj ,
    target_name :str ,
    actual_id :int ,
    reason :str ,
    ):
        dm_sent =False 

        if isinstance (target_obj ,(discord .Member ,discord .User )):
            try :
                dm_embed =discord .Embed (
                title ="Warning Notice",
                description =(
                "You have been issued a warning in "
                f"**{guild .name }**."
                ),
                color =discord .Color .from_rgb (241 ,196 ,15 ),
                )
                dm_embed .add_field (
                name ="REASON",
                value =reason ,
                inline =False ,
                )
                dm_embed .add_field (
                name ="ISSUED BY",
                value =moderator .display_name ,
                inline =True ,
                )
                dm_embed .set_footer (
                text =(
                f"{config .BOT_NAME } | "
                "Moderation Operations"
                )
                )

                await target_obj .send (embed =dm_embed )
                dm_sent =True 

                log_dm (
                target_obj ,
                "Warning Notice",
                success =True ,
                )
            except discord.Forbidden as exc:
                dm_sent =False 
                log_dm (
                target_obj ,
                "Warning Notice",
                success =False ,
                error_detail =str (exc ),
                )
            except discord.HTTPException as exc:
                dm_sent =False
                log_dm(target_obj, "Warning Notice", success=False, error_detail=str(exc))
                log_exception(
                    "DM",
                    exc,
                    guild=guild,
                    user=target_obj,
                    context="Failed to deliver warning notice",
                )

        infraction_uuid =await add_infraction (
        user_id =actual_id ,
        moderator_id =moderator .id ,
        action_type ="WARN",
        reason =reason ,
        guild_id =guild .id if guild else None ,
        )

        log_mod (
        "warned",
        moderator ,
        target_obj or target_name ,
        reason =reason ,
        extra =(
        f"DM Delivered: {dm_sent } | "
        f"Infraction UUID: {infraction_uuid }"
        ),
        )

        embed =discord .Embed (
        title ="Warning Issued",
        description =(
        "An official warning has been registered "
        f"for **{target_name }**."
        ),
        color =discord .Color .from_rgb (241 ,196 ,15 ),
        )
        embed .add_field (
        name ="TARGET USER",
        value =f"**{target_name }** (`{actual_id }`)",
        inline =True ,
        )
        embed .add_field (
        name ="MODERATOR",
        value =moderator .mention ,
        inline =True ,
        )
        embed .add_field (
        name ="INFRACTION UUID",
        value =f"`{infraction_uuid }`",
        inline =False ,
        )
        embed .add_field (
        name ="REASON",
        value =reason ,
        inline =False ,
        )
        embed .add_field (
        name ="DIRECT MESSAGE STATUS",
        value =(
        "Delivered to Direct Messages"
        if dm_sent 
        else "Failed to Deliver (Direct Messages Disabled)"
        ),
        inline =False ,
        )
        embed .set_footer (
        text =f"{config .BOT_NAME } | Infraction Logged"
        )

        return embed 

    @app_commands .command (
    name ="warnz",
    description ="Warn a user and send them a DM notification",
    )
    @app_commands .describe (
    user ="Username, User Mention, or User ID to warn",
    reason ="Reason for the warning",
    )
    async def warnz_slash (
    self ,
    interaction :discord .Interaction ,
    user :str ,
    reason :str ,
    ):
        await interaction .response .defer ()

        log_command (
        interaction .user ,
        "/warnz",
        interaction .channel ,
        f"user={user }, reason={reason }",
        )

        if not can_warn_or_view_history (interaction .user ):
            log_mod (
            "Permission Denied for /warnz",
            interaction .user ,
            user ,
            )

            await interaction .followup .send (
            embed =error (
            "You do not have permission to use this command."
            ),
            ephemeral =True ,
            )
            return 

        target_obj ,target_name ,target_id =await resolve_user (
        interaction .guild ,
        self .bot ,
        user ,
        )

        if not target_obj and not target_id :
            await interaction .followup .send (
            embed =error (
            f"Could not find or resolve user: `{user }`"
            ),
            ephemeral =True ,
            )
            return 

        actual_id =target_id or getattr (target_obj ,"id",None )

        if not actual_id :
            await interaction .followup .send (
            embed =error (
            f"Could not determine the user ID for `{user }`"
            ),
            ephemeral =True ,
            )
            return 

        embed =await self ._issue_warning (
        guild =interaction .guild ,
        moderator =interaction .user ,
        target_obj =target_obj ,
        target_name =target_name ,
        actual_id =actual_id ,
        reason =reason ,
        )

        await interaction .followup .send (embed =embed )

    @commands .command (
    name ="warnZ",
    aliases =["warnz"],
    )
    async def warnz_prefix (
    self ,
    ctx :commands .Context ,
    user_input :str =None ,
    *,
    reason :str ="No reason specified",
    ):
        log_command (
        ctx .author ,
        "!warnZ",
        ctx .channel ,
        f"user={user_input }, reason={reason }",
        )

        if not can_warn_or_view_history (ctx .author ):
            log_mod (
            "Permission Denied for !warnZ",
            ctx .author ,
            user_input ,
            )

            await ctx .send (
            embed =error (
            "You do not have permission to use this command."
            )
            )
            return 

        if not user_input :
            await ctx .send (
            embed =error (
            "Please specify a Username, Mention, or User ID to warn."
            )
            )
            return 

        target_obj ,target_name ,target_id =await resolve_user (
        ctx .guild ,
        self .bot ,
        user_input ,
        )

        if not target_obj and not target_id :
            await ctx .send (
            embed =error (
            f"Could not find or resolve user: `{user_input }`"
            )
            )
            return 

        actual_id =target_id or getattr (target_obj ,"id",None )

        if not actual_id :
            await ctx .send (
            embed =error (
            "Could not determine the target user's ID."
            )
            )
            return 

        embed =await self ._issue_warning (
        guild =ctx .guild ,
        moderator =ctx .author ,
        target_obj =target_obj ,
        target_name =target_name ,
        actual_id =actual_id ,
        reason =reason ,
        )

        await ctx .send (embed =embed )





    async def _remove_warning (
    self ,
    *,
    guild :discord .Guild ,
    moderator ,
    target_obj ,
    target_name :str ,
    actual_id :int ,
    warn_id :str ,
    reason :str ,
    ):
        count_removed ,records =await remove_user_warning (
        actual_id ,
        warn_id =warn_id ,
        guild_id =guild .id if guild else None ,
        )


        if count_removed ==0 and guild :
            count_removed ,records =await remove_user_warning (
            actual_id ,
            warn_id =warn_id ,
            guild_id =None ,
            )

        if count_removed ==0 :
            return None ,None ,0 ,records 

        removed_uuid =(
        records [0 ].get ("uuid")
        if records and len (records )==1 
        else None 
        )

        log_mod (
        "Removed Warning",
        moderator ,
        target_obj or target_name ,
        reason =reason ,
        extra =(
        f"Removed count: {count_removed }, "
        f"Warn ID: {warn_id }, "
        f"UUID: {removed_uuid }"
        ),
        )

        dm_sent =False 

        if isinstance (target_obj ,(discord .Member ,discord .User )):
            try :
                dm_embed =discord .Embed (
                title ="Warning Removed",
                description =(
                "A warning issued on your account in "
                f"**{guild .name }** has been removed."
                ),
                color =discord .Color .from_rgb (46 ,204 ,113 ),
                )

                if warn_id :
                    dm_embed .add_field (
                    name ="WARNING ID / UUID",
                    value =f"`{removed_uuid or warn_id }`",
                    inline =True ,
                    )
                else :
                    dm_embed .add_field (
                    name ="WARNINGS CLEARED",
                    value =f"`{count_removed }` warning(s)",
                    inline =True ,
                    )

                dm_embed .add_field (
                name ="REASON FOR REMOVAL",
                value =reason ,
                inline =False ,
                )
                dm_embed .add_field (
                name ="MODERATOR",
                value =moderator .display_name ,
                inline =True ,
                )
                dm_embed .set_footer (
                text =(
                f"{config .BOT_NAME } | "
                "Moderation Operations"
                )
                )

                await target_obj .send (embed =dm_embed )
                dm_sent =True 

                log_dm (
                target_obj ,
                "Warning Removal Notice",
                success =True ,
                )
            except discord.Forbidden as exc:
                log_dm (
                target_obj ,
                "Warning Removal Notice",
                success =False ,
                error_detail =str (exc ),
                )
            except discord.HTTPException as exc:
                log_dm(target_obj, "Warning Removal Notice", success=False, error_detail=str(exc))
                log_exception(
                    "DM",
                    exc,
                    guild=guild,
                    user=target_obj,
                    context="Failed to deliver warning removal notice",
                )

        embed =discord .Embed (
        title ="Warning Removed",
        description =(
        f"Warning record updated for **{target_name }**."
        ),
        color =discord .Color .from_rgb (46 ,204 ,113 ),
        )
        embed .add_field (
        name ="TARGET USER",
        value =f"**{target_name }** (`{actual_id }`)",
        inline =True ,
        )
        embed .add_field (
        name ="MODERATOR",
        value =moderator .mention ,
        inline =True ,
        )

        if warn_id :
            embed .add_field (
            name ="REMOVED WARNING ID / UUID",
            value =f"`{removed_uuid or warn_id }`",
            inline =True ,
            )
        else :
            embed .add_field (
            name ="TOTAL REMOVED",
            value =f"`{count_removed }` warning(s)",
            inline =True ,
            )

        embed .add_field (
        name ="REASON",
        value =reason ,
        inline =False ,
        )
        embed .add_field (
        name ="DIRECT MESSAGE STATUS",
        value =(
        "Delivered to Direct Messages"
        if dm_sent 
        else "Failed to Deliver (Direct Messages Disabled)"
        ),
        inline =False ,
        )
        embed .set_footer (
        text =f"{config .BOT_NAME } | Infraction Removed"
        )

        return embed ,removed_uuid ,count_removed ,records 

    @app_commands .command (
    name ="warnremovez",
    description ="Remove warning(s) from a user and notify them via DM",
    )
    @app_commands .describe (
    user ="Username, User Mention, or User ID",
    warn_id ="Specific Warning ID or UUID",
    reason ="Reason for removing the warning",
    )
    async def warnremovez_slash (
    self ,
    interaction :discord .Interaction ,
    user :str ,
    warn_id :str =None ,
    reason :str ="No reason specified",
    ):
        log_command (
        interaction .user ,
        "/warnremovez",
        interaction .channel ,
        f"user={user }, warn_id={warn_id }, reason={reason }",
        )

        if not can_warn_or_view_history (interaction .user ):
            log_mod (
            "Permission Denied for /warnremovez",
            interaction .user ,
            user ,
            )

            await interaction .response .send_message (
            embed =error (
            "You do not have permission to use this command."
            ),
            ephemeral =True ,
            )
            return 


        if user and (
        "-"in user 
        or (user .isdigit ()and len (user )<15 )
        ):
            found_inf =await get_infraction_by_uuid (user .strip ())

            if found_inf :
                warn_id =user .strip ()
                user =str (found_inf ["user_id"])

        target_obj ,target_name ,target_id =await resolve_user (
        interaction .guild ,
        self .bot ,
        user ,
        )

        actual_id =target_id or getattr (target_obj ,"id",None )

        if not actual_id :
            await interaction .response .send_message (
            embed =error (
            f"Could not find or resolve user: `{user }`"
            ),
            ephemeral =True ,
            )
            return 

        result =await self ._remove_warning (
        guild =interaction .guild ,
        moderator =interaction .user ,
        target_obj =target_obj ,
        target_name =target_name ,
        actual_id =actual_id ,
        warn_id =warn_id ,
        reason =reason ,
        )

        embed ,_ ,count_removed ,_ =result 

        if count_removed ==0 :
            if warn_id :
                message =(
                "No warning found with ID/UUID "
                f"`{warn_id }` for **{target_name }**."
                )
            else :
                message =(
                "No active warning records found for "
                f"**{target_name }** (`{actual_id }`)."
                )

            await interaction .response .send_message (
            embed =error (message ),
            ephemeral =True ,
            )
            return 

        await interaction .response .send_message (embed =embed )

    @commands .command (
    name ="warnremoveZ",
    aliases =["warnremovez"],
    )
    async def warnremovez_prefix (
    self ,
    ctx :commands .Context ,
    user_input :str =None ,
    warn_id_or_reason :str =None ,
    *,
    reason :str ="No reason specified",
    ):
        log_command (
        ctx .author ,
        "!warnremoveZ",
        ctx .channel ,
        (
        f"user={user_input }, "
        f"warn_id_or_reason={warn_id_or_reason }, "
        f"reason={reason }"
        ),
        )

        if not can_warn_or_view_history (ctx .author ):
            log_mod (
            "Permission Denied for !warnremoveZ",
            ctx .author ,
            user_input ,
            )

            await ctx .send (
            embed =error (
            "You do not have permission to use this command."
            )
            )
            return 

        if not user_input :
            await ctx .send (
            embed =error (
            "Please specify a Username, Mention, or User ID "
            "to remove warning from."
            )
            )
            return 

        warn_id =None 

        if warn_id_or_reason :
            if (
            warn_id_or_reason .isdigit ()
            or len (warn_id_or_reason )>=6 
            or "-"in warn_id_or_reason 
            ):
                warn_id =warn_id_or_reason 
            else :
                if reason =="No reason specified":
                    reason =warn_id_or_reason 
                else :
                    reason =f"{warn_id_or_reason } {reason }"


        if user_input and (
        "-"in user_input 
        or (
        user_input .isdigit ()
        and len (user_input )<15 
        )
        ):
            found_inf =await get_infraction_by_uuid (
            user_input .strip ()
            )

            if found_inf :
                warn_id =user_input .strip ()
                user_input =str (found_inf ["user_id"])

        target_obj ,target_name ,target_id =await resolve_user (
        ctx .guild ,
        self .bot ,
        user_input ,
        )

        actual_id =target_id or getattr (target_obj ,"id",None )

        if not actual_id :
            await ctx .send (
            embed =error (
            f"Could not find or resolve user: `{user_input }`"
            )
            )
            return 

        result =await self ._remove_warning (
        guild =ctx .guild ,
        moderator =ctx .author ,
        target_obj =target_obj ,
        target_name =target_name ,
        actual_id =actual_id ,
        warn_id =warn_id ,
        reason =reason ,
        )

        embed ,_ ,count_removed ,_ =result 

        if count_removed ==0 :
            if warn_id :
                message =(
                "No warning found with ID/UUID "
                f"`{warn_id }` for **{target_name }**."
                )
            else :
                message =(
                "No active warning records found for "
                f"**{target_name }** (`{actual_id }`)."
                )

            await ctx .send (embed =error (message ))
            return 

        await ctx .send (embed =embed )





    async def build_history_embed (
    self ,
    guild ,
    target_obj ,
    target_name ,
    target_id ,
    ):
        actual_id =target_id or getattr (target_obj ,"id",None )
        gid =guild .id if guild else None 

        stats =await get_user_stats (
        actual_id ,
        guild_id =gid ,
        )
        infractions =await get_user_infractions (
        actual_id ,
        guild_id =gid ,
        )

        warnings =[
        i for i in infractions 
        if i ["action_type"]=="WARN"
        ]
        bad_word_logs =[
        i for i in infractions 
        if i ["action_type"]=="BAD_WORD"
        ]
        other_mods =[
        i for i in infractions 
        if i ["action_type"]in ("BAN","UNBAN","KICK")
        ]

        warn_count =len (warnings )
        total_bad_words =stats ["bad_word_count"]

        if warn_count >=3 or len (other_mods )>=2 :
            risk_status ="[HIGH RISK - REPEAT OFFENDER]"
            risk_color =discord .Color .from_rgb (231 ,76 ,60 )
        elif warn_count >=1 or total_bad_words >=3 :
            risk_status ="[NOTICE - WARNINGS RECORDED]"
            risk_color =discord .Color .from_rgb (241 ,196 ,15 )
        else :
            risk_status ="[CLEAN MEMBER]"
            risk_color =discord .Color .from_rgb (46 ,204 ,113 )

        server_name =guild .name if guild else "Server"

        embed =discord .Embed (
        title =(
        "User History Audit Profile "
        f"({server_name }) | {target_name }"
        ),
        color =risk_color ,
        )

        if isinstance (target_obj ,(discord .Member ,discord .User )):
            if target_obj .display_avatar :
                embed .set_thumbnail (
                url =target_obj .display_avatar .url 
                )

        embed .add_field (
        name ="TARGET USER ID",
        value =f"`{actual_id }`",
        inline =True ,
        )
        embed .add_field (
        name ="RISK STATUS",
        value =f"`{risk_status }`",
        inline =True ,
        )
        embed .add_field (
        name ="LAST ACTIVE",
        value =f"`{stats ['last_active']}`",
        inline =True ,
        )

        embed .add_field (
        name ="ACTIVITY & MESSAGES",
        value =(
        "• Total Messages Sent : "
        f"`{stats ['message_count']}`\n"
        "• Flagged Bad Words   : "
        f"`{stats ['bad_word_count']}`"
        ),
        inline =False ,
        )

        if warnings :
            warn_lines =[]

            for warning in warnings [:5 ]:
                mod_user =(
                guild .get_member (warning ["moderator_id"])
                if guild 
                else None 
                )
                mod_name =(
                mod_user .display_name 
                if mod_user 
                else f"ID: {warning ['moderator_id']}"
                )
                uuid_display =(
                f" (`{warning ['uuid']}`)"
                if warning .get ("uuid")
                else ""
                )

                warn_lines .append (
                f"• [{warning ['timestamp']}]"
                f"{uuid_display } "
                f"Reason: `{warning ['reason']}` | "
                f"Issued by: {mod_name }"
                )

            warning_string ="\n".join (warn_lines )

            if len (warnings )>5 :
                warning_string +=(
                f"\n*...and {len (warnings )-5 } "
                "older warning(s)*"
                )
        else :
            warning_string =(
            "*No warnings recorded in database.*"
            )

        embed .add_field (
        name =f"WARNING RECORDS ({len (warnings )})",
        value =warning_string ,
        inline =False ,
        )

        if other_mods :
            mod_lines =[]

            for moderation in other_mods [:5 ]:
                uuid_display =(
                f" (`{moderation ['uuid']}`)"
                if moderation .get ("uuid")
                else ""
                )

                mod_lines .append (
                f"• [{moderation ['timestamp']}]"
                f"{uuid_display } "
                f"Action: `{moderation ['action_type']}` | "
                f"Reason: {moderation ['reason']}"
                )

            moderation_string ="\n".join (mod_lines )
        else :
            moderation_string =(
            "*No ban, unban, or kick actions recorded.*"
            )

        embed .add_field (
        name =f"MODERATION HISTORY ({len (other_mods )})",
        value =moderation_string ,
        inline =False ,
        )

        if bad_word_logs :
            bad_word_lines =[]

            for bad_word in bad_word_logs [:3 ]:
                uuid_display =(
                f" (`{bad_word ['uuid']}`)"
                if bad_word .get ("uuid")
                else ""
                )

                bad_word_lines .append (
                f"• [{bad_word ['timestamp']}]"
                f"{uuid_display } "
                f"{bad_word ['reason']}"
                )

            bad_word_string ="\n".join (bad_word_lines )
        else :
            bad_word_string =(
            "*No profanity flags recorded.*"
            )

        embed .add_field (
        name =f"BAD WORD LOGS ({len (bad_word_logs )})",
        value =bad_word_string ,
        inline =False ,
        )

        embed .set_footer (
        text =(
        f"{config .BOT_NAME } | "
        f"Audit Record ID: {actual_id }"
        )
        )

        return embed 

    @app_commands .command (
    name ="historyz",
    description =(
    "View full message count, bad words, "
    "and warning/ban history of a user"
    ),
    )
    @app_commands .describe (
    user ="Username, User Mention, or User ID to check"
    )
    async def historyz_slash (
    self ,
    interaction :discord .Interaction ,
    user :str =None ,
    ):
        log_command (
        interaction .user ,
        "/historyz",
        interaction .channel ,
        f"user={user }",
        )

        if not can_warn_or_view_history (interaction .user ):
            log_mod (
            "Permission Denied for /historyz",
            interaction .user ,
            user ,
            )

            await interaction .response .send_message (
            embed =error (
            "You do not have permission to view user history."
            ),
            ephemeral =True ,
            )
            return 

        search_user =user or str (interaction .user .id )

        target_obj ,target_name ,target_id =await resolve_user (
        interaction .guild ,
        self .bot ,
        search_user ,
        )

        actual_id =target_id or getattr (target_obj ,"id",None )

        if not actual_id :
            await interaction .response .send_message (
            embed =error (
            f"Could not find or resolve user: `{search_user }`"
            ),
            ephemeral =True ,
            )
            return 

        embed =await self .build_history_embed (
        interaction .guild ,
        target_obj ,
        target_name ,
        actual_id ,
        )

        await interaction .response .send_message (embed =embed )

    @commands .command (
    name ="historyZ",
    aliases =["historyz"],
    )
    async def historyz_prefix (
    self ,
    ctx :commands .Context ,
    user_input :str =None ,
    ):
        log_command (
        ctx .author ,
        "!historyZ",
        ctx .channel ,
        f"user={user_input }",
        )

        if not can_warn_or_view_history (ctx .author ):
            log_mod (
            "Permission Denied for !historyZ",
            ctx .author ,
            user_input ,
            )

            await ctx .send (
            embed =error (
            "You do not have permission to view user history."
            )
            )
            return 

        search_user =user_input or str (ctx .author .id )

        target_obj ,target_name ,target_id =await resolve_user (
        ctx .guild ,
        self .bot ,
        search_user ,
        )

        actual_id =target_id or getattr (target_obj ,"id",None )

        if not actual_id :
            await ctx .send (
            embed =error (
            f"Could not find or resolve user: `{search_user }`"
            )
            )
            return 

        embed =await self .build_history_embed (
        ctx .guild ,
        target_obj ,
        target_name ,
        actual_id ,
        )

        await ctx .send (embed =embed )





    @staticmethod 
    def _normalize_uuid (uuid_value :str )->str :
        if uuid_value is None :
            return ""

        return (
        str (uuid_value )
        .strip ()
        .strip ("`")
        .strip ()
        )

    @staticmethod 
    def _build_infraction_embed (infraction ):
        action_type =str (infraction ["action_type"]).upper ()

        if action_type =="BAN":
            embed_color =discord .Color .from_rgb (231 ,76 ,60 )
        elif action_type =="WARN":
            embed_color =discord .Color .from_rgb (241 ,196 ,15 )
        else :
            embed_color =discord .Color .from_rgb (52 ,152 ,219 )

        embed =discord .Embed (
        title =(
        "Infraction Details | "
        f"{infraction ['action_type']}"
        ),
        color =embed_color ,
        )
        embed .add_field (
        name ="INFRACTION UUID",
        value =f"`{infraction ['uuid']}`",
        inline =False ,
        )
        embed .add_field (
        name ="TARGET USER ID",
        value =f"`{infraction ['user_id']}`",
        inline =True ,
        )
        embed .add_field (
        name ="MODERATOR ID",
        value =f"`{infraction ['moderator_id']}`",
        inline =True ,
        )
        embed .add_field (
        name ="TIMESTAMP",
        value =f"`{infraction ['timestamp']}`",
        inline =True ,
        )
        embed .add_field (
        name ="REASON",
        value =(
        infraction ["reason"]
        or "No reason specified"
        ),
        inline =False ,
        )

        return embed 

    @staticmethod 
    def _build_removed_infraction_embed (removed ):
        embed =discord .Embed (
        title ="Infraction Removed via UUID",
        description =(
        "Successfully deleted "
        f"`{removed ['action_type']}` infraction."
        ),
        color =discord .Color .from_rgb (46 ,204 ,113 ),
        )
        embed .add_field (
        name ="INFRACTION UUID",
        value =f"`{removed ['uuid']}`",
        inline =False ,
        )
        embed .add_field (
        name ="TARGET USER ID",
        value =f"`{removed ['user_id']}`",
        inline =True ,
        )
        embed .add_field (
        name ="ACTION TYPE",
        value =f"`{removed ['action_type']}`",
        inline =True ,
        )
        embed .add_field (
        name ="REASON",
        value =(
        removed ["reason"]
        or "No reason specified"
        ),
        inline =False ,
        )

        return embed 

    @staticmethod 
    def _ticket_value (ticket ,key ,default =None ):
        try :
            value =ticket [key ]
        except (KeyError ,IndexError ,TypeError ):
            return default 

        return default if value is None else value 

    async def _resolve_ticket_user (self ,user_id ,guild =None ):
        try :
            user_id_int =int (user_id )
        except (TypeError ,ValueError ):
            return None ,"Unknown User",str (user_id )

        user =None 

        if guild :
            user =guild .get_member (user_id_int )

        if user is None :
            user =self .bot .get_user (user_id_int )

        if user is None :
            try :
                user =await self .bot .fetch_user (user_id_int )
            except discord.HTTPException as error:
                log_exception(
                    "DISCORD",
                    error,
                    guild=guild,
                    user=user_id_int,
                    context="Failed to resolve ticket user for infraction display",
                )
                user =None 

        if user :
            return user ,user .name ,str (user_id_int )

        return None ,"Unknown User",str (user_id_int )

    async def _build_ticket_embed (self ,ticket ,guild =None ):
        ticket_uuid =self ._ticket_value (ticket ,"uuid","Unknown")
        user_id =self ._ticket_value (ticket ,"user_id","Unknown")
        application =self ._ticket_value (ticket ,"application","Unknown")
        status =self ._ticket_value (ticket ,"status","Unknown")
        channel_id =self ._ticket_value (ticket ,"channel_id","Unknown")
        guild_id =self ._ticket_value (ticket ,"guild_id","Unknown")
        created_at =self ._ticket_value (ticket ,"created_at","Unknown")
        priority =self ._ticket_value (ticket ,"priority","Not set")

        try :
            created_dt =datetime .fromisoformat (str (created_at ))
            created_display =created_dt .strftime ("%d.%m.%Y, %H:%M")
        except (ValueError ,TypeError ):
            created_display =str (created_at )
        claimed_by =self ._ticket_value (ticket ,"claimed_by")
        claimed_at =self ._ticket_value (ticket ,"claimed_at")
        closed_by =self ._ticket_value (ticket ,"closed_by")
        closed_at =self ._ticket_value (ticket ,"closed_at")
        close_reason =self ._ticket_value (ticket ,"close_reason")

        if guild is None :
            try :
                guild =self .bot .get_guild (int (guild_id ))
            except (TypeError ,ValueError ):
                guild =None 

        user_obj ,user_name ,resolved_user_id =await self ._resolve_ticket_user (
        user_id ,
        guild ,
        )

        status_text =str (status ).strip ().title ()
        priority_text =str (priority ).strip ().title ()

        if str (status ).lower ()=="open":
            embed_color =discord .Color .from_rgb (46 ,204 ,113 )
        elif str (status ).lower ()=="closed":
            embed_color =discord .Color .from_rgb (99 ,110 ,114 )
        else :
            embed_color =discord .Color .blurple ()

        embed =discord .Embed (
        title ="Support Ticket",
        description =f"Ticket `{ticket_uuid }`",
        color =embed_color ,
        )

        user_value =f"**{user_name }**\n`{resolved_user_id }`"

        if user_obj :
            user_value =(
            f"**{user_name }**\n"
            f"{user_obj .mention }\n"
            f"`{resolved_user_id }`"
            )

            if user_obj .display_avatar :
                embed .set_thumbnail (url =user_obj .display_avatar .url )

        embed .add_field (
        name ="USER",
        value =user_value ,
        inline =False ,
        )
        embed .add_field (
        name ="CATEGORY",
        value =str (application ),
        inline =True ,
        )
        embed .add_field (
        name ="STATUS",
        value =status_text ,
        inline =True ,
        )
        embed .add_field (
        name ="PRIORITY",
        value =priority_text ,
        inline =True ,
        )

        try :
            channel_id_int =int (channel_id )
            channel_value =f"<#{channel_id_int }>\n`{channel_id_int }`"
        except (TypeError ,ValueError ):
            channel_value =f"`{channel_id }`"

        embed .add_field (
        name ="TICKET CHANNEL",
        value =channel_value ,
        inline =True ,
        )

        if guild :
            guild_value =f"**{guild .name }**\n`{guild .id }`"
        else :
            guild_value =f"`{guild_id }`"

        embed .add_field (
        name ="SERVER",
        value =guild_value ,
        inline =True ,
        )
        embed .add_field (
        name ="CREATED",
        value =f"`{created_display }`",
        inline =False ,
        )
        embed .add_field (
        name ="TICKET UUID",
        value =f"`{ticket_uuid }`",
        inline =False ,
        )

        if claimed_by :
            claimed_obj ,claimed_name ,claimed_id =await self ._resolve_ticket_user (
            claimed_by ,
            guild ,
            )
            claimed_value =f"**{claimed_name }**\n`{claimed_id }`"

            if claimed_obj :
                claimed_value =(
                f"**{claimed_name }**\n"
                f"{claimed_obj .mention }\n"
                f"`{claimed_id }`"
                )

            embed .add_field (
            name ="CLAIMED BY",
            value =claimed_value ,
            inline =True ,
            )

        if claimed_at :
            embed .add_field (
            name ="CLAIMED AT",
            value =f"`{claimed_at }`",
            inline =True ,
            )

        if closed_by :
            closed_obj ,closed_name ,closed_id =await self ._resolve_ticket_user (
            closed_by ,
            guild ,
            )
            closed_value =f"**{closed_name }**\n`{closed_id }`"

            if closed_obj :
                closed_value =(
                f"**{closed_name }**\n"
                f"{closed_obj .mention }\n"
                f"`{closed_id }`"
                )

            embed .add_field (
            name ="CLOSED BY",
            value =closed_value ,
            inline =True ,
            )

        if closed_at :
            embed .add_field (
            name ="CLOSED AT",
            value =f"`{closed_at }`",
            inline =True ,
            )

        if close_reason :
            embed .add_field (
            name ="CLOSE REASON",
            value =str (close_reason ),
            inline =False ,
            )

        embed .set_footer (text =config .BOT_NAME )

        return embed 






    @app_commands .command (
    name ="infraction",
    description =(
    "Search or manage an infraction, or look up "
    "a support ticket UUID"
    ),
    )
    @app_commands .describe (
    uuid ="Infraction UUID or Ticket UUID",
    action =(
    "Optional action: 'view' to inspect "
    "or 'remove' to delete an infraction"
    ),
    )
    async def infraction_slash (
    self ,
    interaction :discord .Interaction ,
    uuid :str ,
    action :str ="view",
    ):
        log_command (
        interaction .user ,
        "/infraction",
        interaction .channel ,
        f"uuid={uuid }, action={action }",
        )

        if not can_warn_or_view_history (interaction .user ):
            await interaction .response .send_message (
            embed =error (
            "You do not have permission to use this command."
            ),
            ephemeral =True ,
            )
            return 

        uuid_value =self ._normalize_uuid (uuid )
        action_value =str (action ).strip ().lower ()

        if not uuid_value :
            await interaction .response .send_message (
            embed =error (
            "Please specify an Infraction UUID or Ticket UUID."
            ),
            ephemeral =True ,
            )
            return 

        if action_value not in ("view","remove"):
            await interaction .response .send_message (
            embed =error (
            "Invalid action. Please use `view` or `remove`."
            ),
            ephemeral =True ,
            )
            return 




        infraction =await get_infraction_by_uuid (uuid_value )

        if infraction :
            try :
                infraction_guild_id =int (infraction ["guild_id"])
            except (KeyError ,IndexError ,TypeError ,ValueError ):
                infraction_guild_id =None 

            if (
            not interaction .guild 
            or infraction_guild_id !=interaction .guild .id 
            ):
                infraction =None 

        if infraction :
            if action_value =="remove":
                removed =await remove_infraction_by_uuid (uuid_value )

                if not removed :
                    await interaction .response .send_message (
                    embed =error (
                    f"Could not remove UUID: `{uuid_value }`"
                    ),
                    ephemeral =True ,
                    )
                    return 

                log_mod (
                "Removed Infraction by UUID",
                interaction .user ,
                removed ["user_id"],
                reason =(
                f"Removed via UUID {removed ['uuid']}"
                ),
                extra =(
                f"Infraction UUID: {removed ['uuid']}"
                ),
                )

                await interaction .response .send_message (
                embed =self ._build_removed_infraction_embed (
                removed 
                )
                )
                return 

            await interaction .response .send_message (
            embed =self ._build_infraction_embed (infraction )
            )
            return 




        ticket =await get_ticket_by_uuid (uuid_value )

        if ticket :
            try :
                ticket_guild_id =int (ticket ["guild_id"])
            except (KeyError ,IndexError ,TypeError ,ValueError ):
                ticket_guild_id =None 

            if (
            not interaction .guild 
            or ticket_guild_id !=interaction .guild .id 
            ):
                ticket =None 

        if ticket :
            if action_value =="remove":
                await interaction .response .send_message (
                embed =error (
                "This UUID belongs to a support ticket.\n\n"
                "`/infraction remove` can only remove "
                "moderation infractions. Tickets are not "
                "deleted by this command."
                ),
                ephemeral =True ,
                )
                return 

            await interaction .response .send_message (
            embed =await self ._build_ticket_embed (ticket ,interaction .guild ),
            )
            return 




        await interaction .response .send_message (
        embed =error (
        "No ticket or infraction found matching UUID: "
        f"`{uuid_value }`"
        ),
        ephemeral =True ,
        )

    @commands .command (name ="infraction")
    async def infraction_prefix (
    self ,
    ctx :commands .Context ,
    uuid_input :str =None ,
    action :str ="view",
    ):
        log_command (
        ctx .author ,
        "!infraction",
        ctx .channel ,
        f"uuid={uuid_input }, action={action }",
        )

        if not can_warn_or_view_history (ctx .author ):
            await ctx .send (
            embed =error (
            "You do not have permission to use this command."
            )
            )
            return 

        if not uuid_input :
            await ctx .send (
            embed =error (
            "Please specify an Infraction UUID or Ticket UUID."
            )
            )
            return 

        uuid_value =self ._normalize_uuid (uuid_input )
        action_value =str (action ).strip ().lower ()

        if action_value not in ("view","remove"):
            await ctx .send (
            embed =error (
            "Invalid action. Please use `view` or `remove`."
            )
            )
            return 




        infraction =await get_infraction_by_uuid (uuid_value )

        if infraction :
            if action_value =="remove":
                removed =await remove_infraction_by_uuid (uuid_value )

                if not removed :
                    await ctx .send (
                    embed =error (
                    f"Could not remove UUID: `{uuid_value }`"
                    )
                    )
                    return 

                log_mod (
                "Removed Infraction by UUID",
                ctx .author ,
                removed ["user_id"],
                reason =(
                f"Removed via UUID {removed ['uuid']}"
                ),
                extra =(
                f"Infraction UUID: {removed ['uuid']}"
                ),
                )

                await ctx .send (
                embed =self ._build_removed_infraction_embed (
                removed 
                )
                )
                return 

            await ctx .send (
            embed =self ._build_infraction_embed (infraction )
            )
            return 




        ticket =await get_ticket_by_uuid (uuid_value )

        if ticket :
            if action_value =="remove":
                await ctx .send (
                embed =error (
                "This UUID belongs to a support ticket.\n\n"
                "`!infraction <UUID> remove` can only "
                "remove moderation infractions. Tickets "
                "are not deleted by this command."
                )
                )
                return 

            await ctx .send (
            embed =await self ._build_ticket_embed (ticket ,ctx .guild )
            )
            return 




        await ctx .send (
        embed =error (
        "No ticket or infraction found matching UUID: "
        f"`{uuid_value }`"
        )
        )


async def setup (bot :commands .Bot ):
    await bot .add_cog (BanCog (bot ))
