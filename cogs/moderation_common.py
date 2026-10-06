import re

import discord
from discord.ext import commands

from utils.logger import log_exception


async def resolve_user(
    guild: discord.Guild,
    bot: commands.Bot,
    user_input: str,
):

    if not user_input:
        return None, "", None

    clean_input = user_input.strip()

    mention_match = re.match(r"^<@!?(\d+)>$", clean_input)

    if mention_match:
        user_id = int(mention_match.group(1))

        member = guild.get_member(user_id) if guild else None
        if member:
            return member, member.name, user_id

        try:
            fetched = await bot.fetch_user(user_id)
            return fetched, fetched.name, user_id
        except discord.HTTPException as error:
            log_exception(
                "DISCORD",
                error,
                guild=guild,
                user=user_id,
                context="Failed to resolve mentioned moderation target",
            )
            return user_id, str(user_id), user_id

    if clean_input.isdigit():
        user_id = int(clean_input)

        member = guild.get_member(user_id) if guild else None
        if member:
            return member, member.name, user_id

        try:
            fetched = await bot.fetch_user(user_id)
            return fetched, fetched.name, user_id
        except discord.HTTPException as error:
            log_exception(
                "DISCORD",
                error,
                guild=guild,
                user=user_id,
                context="Failed to resolve numeric moderation target",
            )
            return user_id, clean_input, user_id

    if guild:
        search_term = clean_input.lstrip("@").lower()

        for member in guild.members:
            if member.name.lower() == search_term or str(member).lower() == search_term:
                return member, member.name, member.id

        display_matches = [
            member
            for member in guild.members
            if member.display_name.lower() == search_term
            or (member.global_name and member.global_name.lower() == search_term)
        ]
        if len(display_matches) == 1:
            member = display_matches[0]
            return member, member.name, member.id

    return None, clean_input.lstrip("@"), None


async def resolve_banned_user(
    guild: discord.Guild,
    bot: commands.Bot,
    user_input: str,
):

    if not user_input:
        return None, "", None

    clean_input = user_input.strip()
    user_id = None

    mention_match = re.match(r"^<@!?(\d+)>$", clean_input)

    if mention_match:
        user_id = int(mention_match.group(1))
    elif clean_input.isdigit():
        user_id = int(clean_input)

    ban_entries = []

    if guild:
        try:
            ban_entries = [entry async for entry in guild.bans()]
        except discord.HTTPException as error:
            log_exception(
                "MODERATION",
                error,
                guild=guild,
                context="Failed to retrieve server ban list",
            )
            ban_entries = []

    if user_id:
        for entry in ban_entries:
            if entry.user.id == user_id:
                return entry.user, entry.user.name, entry.user.id

        try:
            fetched = await bot.fetch_user(user_id)
            return fetched, fetched.name, user_id
        except discord.HTTPException as error:
            log_exception(
                "DISCORD",
                error,
                guild=guild,
                user=user_id,
                context="Failed to resolve banned user",
            )
            return user_id, str(user_id), user_id

    search_term = clean_input.lstrip("@").lower()

    for entry in ban_entries:
        user = entry.user

        if (
            user.name.lower() == search_term
            or (user.global_name and user.global_name.lower() == search_term)
            or str(user).lower() == search_term
        ):
            return user, user.name, user.id

    return None, clean_input.lstrip("@"), None


