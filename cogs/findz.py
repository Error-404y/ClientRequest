import discord

from discord import app_commands

from discord.ext import commands

import aiosqlite

import config


class UUIDLookup(commands.Cog):

    def __init__(
        self,
        bot
    ):

        self.bot = bot

    # ========================================================
    # UUID NORMALIZATION
    # ========================================================

    @staticmethod
    def normalize_uuid(
        uuid_value
    ):

        if uuid_value is None:
            return ""

        value = str(
            uuid_value
        ).strip()

        value = value.strip(
            "`"
        )

        value = value.strip(
            " \t\r\n"
        )

        return value

    # ========================================================
    # INFRACTION LOOKUP
    # ========================================================

    async def get_infraction(
        self,
        uuid_value
    ):

        uuid_value = self.normalize_uuid(
            uuid_value
        )

        if not uuid_value:
            return None

        async with aiosqlite.connect(
            config.DATABASE
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    id,
                    uuid,
                    guild_id,
                    user_id,
                    moderator_id,
                    action_type,
                    reason,
                    timestamp
                FROM infractions
                WHERE
                    uuid = ?
                    OR uuid LIKE ?
                    OR uuid LIKE ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    uuid_value,
                    f"{uuid_value}%",
                    f"%{uuid_value}"
                )
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

    # ========================================================
    # TICKET LOOKUP
    # ========================================================

    async def get_ticket(
        self,
        uuid_value
    ):

        uuid_value = self.normalize_uuid(
            uuid_value
        )

        if not uuid_value:
            return None

        async with aiosqlite.connect(
            config.DATABASE
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    id,
                    uuid,
                    channel_id,
                    guild_id,
                    user_id,
                    application,
                    status,
                    created_at,
                    closed_at,
                    claimed_by,
                    close_reason,
                    priority,
                    claimed_at,
                    closed_by,
                    warned_inactive
                FROM tickets
                WHERE
                    uuid = ?
                    OR uuid LIKE ?
                    OR uuid LIKE ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    uuid_value,
                    f"{uuid_value}%",
                    f"%{uuid_value}"
                )
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
            "closed_by": row[13],
            "warned_inactive": row[14]
        }

    # ========================================================
    # USER DISPLAY
    # ========================================================

    async def fetch_user_text(
        self,
        user_id
    ):

        if not user_id:

            return (
                "Unknown User -> Unknown"
            )

        user = self.bot.get_user(
            user_id
        )

        if user is None:

            try:

                user = await self.bot.fetch_user(
                    user_id
                )

            except (
                discord.NotFound,
                discord.HTTPException
            ):

                return (
                    f"Unknown User -> {user_id}"
                )

        return (
            f"{user} -> {user.id}"
        )

    # ========================================================
    # /findz
    # ========================================================

    @app_commands.command(
        name="findz",
        description=(
            "Find information connected "
            "to a generated UUID."
        )
    )
    @app_commands.describe(
        uuid=(
            "The ticket or infraction UUID "
            "you want to look up."
        )
    )
    async def findz(
        self,
        interaction: discord.Interaction,
        uuid: str
    ):

        uuid = self.normalize_uuid(
            uuid
        )

        if not uuid:

            await interaction.response.send_message(
                "Please provide a UUID.",
                ephemeral=True
            )

            return

        # ====================================================
        # FIRST: SEARCH INFRACTIONS
        # ====================================================

        infraction = await self.get_infraction(
            uuid
        )

        if infraction:

            user_text = (
                await self.fetch_user_text(
                    infraction["user_id"]
                )
            )

            moderator_text = (
                await self.fetch_user_text(
                    infraction["moderator_id"]
                )
            )

            if (
                str(
                    infraction["action_type"]
                ).upper()
                == "WARN"
            ):

                description = (
                    "**identified information**\n"
                    f"Type: Infraction / Warn\n"
                    f"Warn Issued by: {moderator_text}\n"
                    f"Date: {infraction['timestamp']}\n"
                    f"Received Warn: {user_text}\n"
                    f"UUID: `{infraction['uuid']}`\n"
                    f"Reason: {infraction['reason']}"
                )

            else:

                description = (
                    "**identified information**\n"
                    f"Type: Infraction\n"
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
                    value=str(
                        infraction["guild_id"]
                    ),
                    inline=True
                )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        # ====================================================
        # SECOND: SEARCH TICKETS
        #
        # IMPORTANT:
        # A ticket UUID is stored in tickets.uuid.
        #
        # We only reach this section if the UUID was NOT
        # found inside infractions.
        # ====================================================

        ticket = await self.get_ticket(
            uuid
        )

        if ticket:

            user_text = (
                await self.fetch_user_text(
                    ticket["user_id"]
                )
            )

            claimed_text = (
                await self.fetch_user_text(
                    ticket["claimed_by"]
                )
            )

            closed_text = (
                await self.fetch_user_text(
                    ticket["closed_by"]
                )
            )

            description = (
                "**identified information**\n"
                f"Type: Support Ticket\n"
                f"Ticket User: {user_text}\n"
                f"Created: {ticket['created_at']}\n"
                f"Status: {ticket['status']}\n"
                f"Application: {ticket['application']}\n"
                f"Claimed by: {claimed_text}\n"
                f"Closed by: {closed_text}\n"
                f"UUID: `{ticket['uuid']}`"
            )

            if ticket["closed_at"]:

                description += (
                    f"\nClosed: "
                    f"{ticket['closed_at']}"
                )

            if ticket["close_reason"]:

                description += (
                    f"\nClose Reason: "
                    f"{ticket['close_reason']}"
                )

            embed = discord.Embed(
                title="UUID Information",
                description=description,
                color=discord.Color.blurple()
            )

            if ticket["guild_id"]:

                embed.add_field(
                    name="Guild ID",
                    value=str(
                        ticket["guild_id"]
                    ),
                    inline=True
                )

            if ticket["channel_id"]:

                embed.add_field(
                    name="Channel ID",
                    value=str(
                        ticket["channel_id"]
                    ),
                    inline=True
                )

            if ticket["priority"]:

                embed.add_field(
                    name="Priority",
                    value=str(
                        ticket["priority"]
                    ),
                    inline=True
                )

            embed.add_field(
                name="Ticket Database ID",
                value=str(
                    ticket["id"]
                ),
                inline=True
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

            return

        # ====================================================
        # NOTHING FOUND
        # ====================================================

        await interaction.response.send_message(
            (
                f"No ticket or infraction was found "
                f"for UUID `{uuid}`."
            ),
            ephemeral=True
        )


async def setup(
    bot
):

    await bot.add_cog(
        UUIDLookup(bot)
    )
