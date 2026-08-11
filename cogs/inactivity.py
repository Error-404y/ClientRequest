import discord
from discord.ext import commands, tasks
import config
import os
from datetime import datetime
import pytz
import aiosqlite
from utils.ticket_actions import close_ticket_channel
from utils.logger import log, log_inactivity

timezone = pytz.timezone("Europe/Berlin")

class Inactivity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_inactivity.start()

    def cog_unload(self):
        self.check_inactivity.cancel()

    @tasks.loop(minutes=30)
    async def check_inactivity(self):
        guild = self.bot.get_guild(config.GUILD_ID)
        if not guild:
            return

        log_inactivity("Running 30-minute inactivity audit on open tickets")

        # Query all open tickets
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                "SELECT channel_id, user_id, warned_inactive, created_at FROM tickets WHERE status = 'open'"
            )
            rows = await cursor.fetchall()

        for row in rows:
            channel_id, user_id, warned_inactive, created_at_str = row
            channel = guild.get_channel(channel_id)
            if not channel:
                # Channel was deleted manually, update status in DB
                async with aiosqlite.connect(config.DATABASE) as db:
                    await db.execute(
                        "UPDATE tickets SET status = 'closed', closed_at = ? WHERE channel_id = ?",
                        (datetime.now(timezone).isoformat(), channel_id)
                    )
                    await db.commit()
                log_inactivity("Channel deleted externally - marked closed in DB", details=f"Channel ID: {channel_id}")
                continue

            # Find last message timestamp
            last_msg_time = None
            try:
                async for msg in channel.history(limit=1):
                    last_msg_time = msg.created_at.astimezone(timezone)
            except Exception as e:
                print(f"Failed to fetch history for channel {channel.name}: {str(e)}")

            if not last_msg_time:
                # Fallback to ticket creation time
                try:
                    last_msg_time = datetime.fromisoformat(created_at_str).astimezone(timezone)
                except Exception:
                    last_msg_time = datetime.now(timezone)

            elapsed = datetime.now(timezone) - last_msg_time
            elapsed_hours = elapsed.total_seconds() / 3600.0

            if warned_inactive == 0:
                if elapsed_hours >= config.INACTIVITY_WARN_HOURS:
                    # Send warning message
                    applicant = guild.get_member(user_id)
                    if applicant is None:
                        try:
                            applicant = await guild.fetch_member(user_id)
                        except Exception:
                            pass
                    
                    mention_str = applicant.mention if applicant else "Applicant"
                    
                    embed = discord.Embed(
                        title="Inactivity Warning",
                        description=(
                            f"Hello {mention_str}.\n\n"
                            f"This ticket has been inactive for {config.INACTIVITY_WARN_HOURS} hours.\n"
                            f"If there is no activity, this ticket will **automatically close** in {config.INACTIVITY_CLOSE_HOURS} hours."
                        ),
                        color=discord.Color.orange()
                    )
                    try:
                        await channel.send(content=mention_str, embed=embed)
                        log_inactivity("Issued Inactivity Warning", channel, applicant, details=f"Inactive hours: {elapsed_hours:.1f}h")
                    except Exception as e:
                        print(f"Failed to send inactivity warning to {channel.name}: {str(e)}")

                    # Update warned_inactive in database
                    async with aiosqlite.connect(config.DATABASE) as db:
                        await db.execute(
                            "UPDATE tickets SET warned_inactive = 1 WHERE channel_id = ?",
                            (channel_id,)
                        )
                        await db.commit()
            
            elif warned_inactive == 1:
                # If warned, close after elapsed time from last message
                if elapsed_hours >= config.INACTIVITY_CLOSE_HOURS:
                    log_inactivity("Auto-closing Inactive Ticket", channel, details=f"Elapsed hours: {elapsed_hours:.1f}h")
                    try:
                        await close_ticket_channel(
                            channel=channel,
                            moderator=self.bot.user,
                            reason="Closed automatically due to inactivity.",
                            bot=self.bot
                        )
                    except Exception as e:
                        print(f"Failed to auto-close channel {channel.name}: {str(e)}")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # Check if message is in an open ticket channel
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                "SELECT warned_inactive FROM tickets WHERE channel_id = ? AND status = 'open'",
                (message.channel.id,)
            )
            row = await cursor.fetchone()
            
            if row:
                warned_inactive = row[0]
                if warned_inactive == 1:
                    # Reset warned_inactive to 0 since there's new activity
                    await db.execute(
                        "UPDATE tickets SET warned_inactive = 0 WHERE channel_id = ?",
                        (message.channel.id,)
                    )
                    await db.commit()
                    log_inactivity("Reset Inactivity State (New Message)", message.channel, message.author)

async def setup(bot):
    await bot.add_cog(Inactivity(bot))

