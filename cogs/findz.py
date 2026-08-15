import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import config

class UUIDLookup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_infraction(self, uuid_value):
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                """
                SELECT id, uuid, guild_id, user_id, moderator_id, action_type, reason, timestamp
                FROM infractions
                WHERE uuid = ? OR uuid LIKE ?
                LIMIT 1
                """,
                (uuid_value, f"{uuid_value}%")
            )
            row = await cursor.fetchone()

        if not row:
            return None

        return {
            "type": "infraction",
            "id": row[0],
            "uuid": row[1],
            "guild_id": row[2],
            "user_id": row[3],
            "moderator_id": row[4],
            "action_type": row[5],
            "reason": row[6],
            "timestamp": row[7]
        }

    async def get_ticket(self, uuid_value):
        async with aiosqlite.connect(config.DATABASE) as db:
            cursor = await db.execute(
                """
                SELECT id, uuid, channel_id, guild_id, user_id, application,
                       status, created_at, closed_at, claimed_by, close_reason,
                       priority, claimed_at, closed_by
                FROM tickets
                WHERE uuid = ? OR uuid LIKE ?
                LIMIT 1
                """,
                (uuid_value, f"{uuid_value}%")
            )
            row = await cursor.fetchone()

        if not row:
            return None

        return {
            "type": "ticket",
            "id": row[0],
            "uuid": row[1],
            "channel_id": row[2],
            "guild_id": row[3],
            "user_id": row[4],
            "application": row[5],
            "status": row[6],
            "created_at": row[7],
            "closed_at": row[8],
            "claimed_by": row[9],
            "close_reason": row[10],
            "priority": row[11],
            "claimed_at": row[12],
            "closed_by": row[13]
        }

    async def fetch_user_text(self, user_id):
        if not user_id:
            return "Unknown User -> Unknown"

        user = self.bot.get_user(user_id)

        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException):
                return f"Unknown User -> {user_id}"

        return f"{user} -> {user.id}"

    @app_commands.command(
        name="findz",
        description="Find information connected to a generated UUID."
    )
    @app_commands.describe(uuid="The UUID you want to look up.")
    async def findz(self, interaction: discord.Interaction, uuid: str):
        uuid = uuid.strip()

        if not uuid:
            await interaction.response.send_message(
                "Please provide a UUID.",
                ephemeral=True
            )
            return

        infraction = await self.get_infraction(uuid)

        if infraction:
            user_text = await self.fetch_user_text(infraction["user_id"])
            moderator_text = await self.fetch_user_text(infraction["moderator_id"])

            if infraction["action_type"].upper() == "WARN":
                description = (
                    f"**identified information**\n"
                    f"Warn Issued by: {moderator_text}\n"
                    f"Date: {infraction['timestamp']}\n"
                    f"Received Warn: {user_text}\n"
                    f"UUID: `{infraction['uuid']}`\n"
                    f"Reason: {infraction['reason']}"
                )
            else:
                description = (
                    f"**identified information**\n"
                    f"Action: {infraction['action_type']}\n"
                    f"Issued by: {moderator_text}\n"
                    f"Date: {infraction['timestamp']}\n"
                    f"User: {user_text}\n"
                    f"UUID: `{infraction['uuid']}`\n"
                    f"Reason: {infraction['reason']}"
                )

            embed = discord.Embed(
                title="UUID Information",
                description=description,
                color=discord.Color.red()
            )

            if infraction["guild_id"]:
                embed.add_field(
                    name="Guild ID",
                    value=str(infraction["guild_id"]),
                    inline=True
                )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )
            return

        ticket = await self.get_ticket(uuid)

        if ticket:
            user_text = await self.fetch_user_text(ticket["user_id"])
            claimed_text = await self.fetch_user_text(ticket["claimed_by"])
            closed_text = await self.fetch_user_text(ticket["closed_by"])

            description = (
                f"**identified information**\n"
                f"Ticket User: {user_text}\n"
                f"Created: {ticket['created_at']}\n"
                f"Status: {ticket['status']}\n"
                f"Application: {ticket['application']}\n"
                f"Claimed by: {claimed_text}\n"
                f"Closed by: {closed_text}\n"
                f"UUID: `{ticket['uuid']}`"
            )

            if ticket["closed_at"]:
                description += f"\nClosed: {ticket['closed_at']}"

            if ticket["close_reason"]:
                description += f"\nClose Reason: {ticket['close_reason']}"

            embed = discord.Embed(
                title="UUID Information",
                description=description,
                color=discord.Color.blurple()
            )

            if ticket["guild_id"]:
                embed.add_field(
                    name="Guild ID",
                    value=str(ticket["guild_id"]),
                    inline=True
                )

            if ticket["channel_id"]:
                embed.add_field(
                    name="Channel ID",
                    value=str(ticket["channel_id"]),
                    inline=True
                )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"No information was found for `{uuid}`.",
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(UUIDLookup(bot))
