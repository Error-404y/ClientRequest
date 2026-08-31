from datetime import datetime, timezone
from time import monotonic

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import get_all_afk_user_ids, process_afk_message, set_afk_status
from utils.embeds import error as error_embed
from utils.logger import log_interaction


def afk_duration(set_at, now=None):
    now_value = now or datetime.now(timezone.utc)
    try:
        started = datetime.fromisoformat(set_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "an unknown duration"
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone.utc)
    seconds = max(0, int((now_value - started).total_seconds()))
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value}{unit}")
    return " ".join(parts) or "0s"


def english_elapsed(set_at, now=None):
    now_value = now or datetime.now(timezone.utc)
    try:
        started = datetime.fromisoformat(set_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "recently"
    seconds = max(
        0, int((now_value - started.astimezone(timezone.utc)).total_seconds())
    )
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} seconds ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.mention_cooldowns = {}
        self.afk_users = set()
        self.afk_cache_loaded = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.afk_cache_loaded:
            return
        self.afk_users = await get_all_afk_user_ids()
        self.afk_cache_loaded = True

    @app_commands.command(name="setafkz", description="Set your server AFK status")
    @app_commands.describe(reason="Why you are currently away")
    async def setafkz(
        self,
        interaction: discord.Interaction,
        reason: app_commands.Range[str, 2, 300],
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("This command can only be used inside a server."),
                ephemeral=True,
            )
            return
        clean_reason = " ".join(reason.split())
        set_at = discord.utils.utcnow().isoformat()
        await set_afk_status(
            interaction.guild.id, interaction.user.id, clean_reason, set_at
        )
        self.afk_users.add((interaction.guild.id, interaction.user.id))
        log_interaction(
            interaction.user,
            "setafkz",
            interaction.channel,
            details=f"Reason: {clean_reason}",
        )
        embed = discord.Embed(
            title="AFK Status Activated",
            description="Your AFK status is now active for this server.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="User",
            value=f"{interaction.user.mention}\n`{interaction.user.id}`",
            inline=True,
        )
        embed.add_field(name="Status", value="AFK", inline=True)
        embed.add_field(name="Reason", value=clean_reason, inline=False)
        embed.add_field(
            name="Automatic Removal",
            value="Your AFK status will be removed when you send your next message in this server.",
            inline=False,
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"{config.BOT_NAME} | Presence Status")
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        mentioned_ids = {
            member.id
            for member in message.mentions
            if member.id != message.author.id and not member.bot
        }
        author_key = (message.guild.id, message.author.id)
        if self.afk_cache_loaded and author_key not in self.afk_users and not any(
            (message.guild.id, user_id) in self.afk_users
            for user_id in mentioned_ids
        ):
            return
        removed, records = await process_afk_message(
            message.guild.id, message.author.id, mentioned_ids
        )
        if removed:
            self.afk_users.discard(author_key)
            now = discord.utils.utcnow()
            embed = discord.Embed(
                title=f"Welcome back, {message.author.name}",
                description=(
                    f"You were AFK for **{afk_duration(removed['set_at'], now)}**.\n"
                    "Your AFK status has been cleared."
                ),
                color=discord.Color.green(),
                timestamp=now,
            )
            embed.set_footer(text=f"{config.BOT_NAME} | Presence Status")
            await message.channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        if not records:
            return
        now_counter = monotonic()
        active_records = []
        for record in records:
            key = (
                message.guild.id,
                message.channel.id,
                message.author.id,
                record["user_id"],
            )
            if now_counter - self.mention_cooldowns.get(key, 0) < 5:
                continue
            self.mention_cooldowns[key] = now_counter
            active_records.append(record)
        if not active_records:
            return
        if len(self.mention_cooldowns) > 5000:
            self.mention_cooldowns = {
                key: value
                for key, value in self.mention_cooldowns.items()
                if now_counter - value < 300
            }
        embeds = []
        for record in active_records[:10]:
            member = message.guild.get_member(record["user_id"])
            display = member.mention if member else f"User `{record['user_id']}`"
            embed = discord.Embed(
                title="AFK Status",
                description=(
                    f"{message.author.mention}, {display} is currently **AFK**."
                ),
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Reason",
                value=record["reason"],
                inline=False,
            )
            embed.add_field(
                name="AFK Since",
                value=english_elapsed(record["set_at"]),
                inline=False,
            )
            embed.set_footer(text=f"{config.BOT_NAME} | Presence Status")
            embeds.append(embed)
        await message.channel.send(
            embeds=embeds,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot):
    await bot.add_cog(AFK(bot))
