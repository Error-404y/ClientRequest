from datetime import datetime, timezone
from time import monotonic

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import process_afk_message, set_afk_status
from utils.embeds import error as error_embed
from utils.logger import log_interaction


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

    @app_commands.command(name="setafkz", description="Set your server AFK status")
    @app_commands.describe(reason="Why you are currently away")
    async def setafkz(
        self,
        interaction: discord.Interaction,
        reason: app_commands.Range[str, 2, 300],
    ):
        if interaction.guild is None or not config.is_guild_configured(
            interaction.guild.id
        ):
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used in a configured server."
                ),
                ephemeral=True,
            )
            return
        clean_reason = " ".join(reason.split())
        set_at = discord.utils.utcnow().isoformat()
        await set_afk_status(
            interaction.guild.id, interaction.user.id, clean_reason, set_at
        )
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
        removed, records = await process_afk_message(
            message.guild.id, message.author.id, mentioned_ids
        )
        if removed:
            embed = discord.Embed(
                title="Welcome Back",
                description=f"{message.author.mention}, your AFK status has been removed automatically.",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
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
        embed = discord.Embed(
            title="AFK Status Notice",
            description=f"{message.author.mention}, one or more mentioned members are currently away.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        for record in active_records[:10]:
            member = message.guild.get_member(record["user_id"])
            display = member.mention if member else f"User `{record['user_id']}`"
            embed.add_field(
                name=member.display_name if member else str(record["user_id"]),
                value=(
                    f"{display} is currently **AFK**\n"
                    f"Reason: {record['reason']}\n"
                    f"Set: {english_elapsed(record['set_at'])}"
                ),
                inline=False,
            )
        embed.set_footer(text=f"{config.BOT_NAME} | Presence Status")
        await message.channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot):
    await bot.add_cog(AFK(bot))
