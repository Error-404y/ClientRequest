import re

import aiosqlite
from discord import app_commands
from discord.ext import commands

import config
from utils.embeds import error as error_embed

BOT_ACCESS_OWNER_ID = 1536561752659984514


class BotAccessDenied(commands.CheckFailure):
    pass


def parse_user_id(value):
    if not re.fullmatch(r"[1-9][0-9]{16,19}", value):
        raise ValueError(
            "Enter only a numeric Discord user ID, without a name or mention."
        )
    user_id = int(value)
    if user_id >= 2**63:
        raise ValueError("This Discord user ID is outside the valid range.")
    return user_id


def ban_embed():
    embed = error_embed("Sorry, you have been banned from using this bot.")
    embed.title = "Bot Access Restricted"
    embed.add_field(
        name="Scope", value="This restriction applies across all servers.", inline=False
    )
    return embed


async def is_bot_banned(user_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        async with db.execute(
            "SELECT 1 FROM bot_bans WHERE user_id=?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None


async def ban_bot_user(user_id, banned_by):
    if banned_by != BOT_ACCESS_OWNER_ID:
        raise PermissionError("Only the bot access owner can manage bot bans.")
    if user_id == BOT_ACCESS_OWNER_ID:
        raise ValueError("The bot access owner cannot be banned.")
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO bot_bans(user_id, banned_by, created_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
            (user_id, banned_by),
        )
        await db.commit()
        return cursor.rowcount == 1


async def check_bot_access(interaction):
    if not await is_bot_banned(interaction.user.id):
        return True
    if interaction.type.name == "autocomplete":
        await interaction.response.autocomplete([])
    elif interaction.response.is_done():
        await interaction.followup.send(embed=ban_embed(), ephemeral=True)
    else:
        await interaction.response.send_message(embed=ban_embed(), ephemeral=True)
    return False


async def unban_bot_user(user_id, unbanned_by):
    if unbanned_by != BOT_ACCESS_OWNER_ID:
        raise PermissionError("Only the bot access owner can manage bot bans.")
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute("DELETE FROM bot_bans WHERE user_id=?", (user_id,))
        await db.commit()
        return cursor.rowcount == 1


class AccessCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        return await check_bot_access(interaction)
