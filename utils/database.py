import os
import uuid as uuid_lib
from datetime import datetime

import aiosqlite
import pytz

import config
from utils.logger import log_db




async def setup_database():
    async with aiosqlite.connect(config.DATABASE) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                guild_id INTEGER,
                user_id INTEGER,
                application TEXT,
                status TEXT,
                created_at TEXT,
                closed_at TEXT,
                uuid TEXT
            )
        """)

        cursor = await db.execute("PRAGMA table_info(tickets)")
        columns = [row[1] for row in await cursor.fetchall()]

        if "claimed_by" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN claimed_by INTEGER DEFAULT NULL"
            )

        if "close_reason" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN close_reason TEXT DEFAULT NULL"
            )

        if "warned_inactive" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN warned_inactive INTEGER DEFAULT 0"
            )

        if "priority" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN priority TEXT DEFAULT 'Medium'"
            )

        if "closed_by" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN closed_by INTEGER DEFAULT NULL"
            )

        if "claimed_at" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN claimed_at TEXT DEFAULT NULL"
            )

        if "uuid" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN uuid TEXT DEFAULT NULL"
            )

            # Give old tickets UUIDs.
            cursor = await db.execute("""
                SELECT id
                FROM tickets
                WHERE uuid IS NULL OR uuid = ''
            """)

            old_rows = await cursor.fetchall()

            for (row_id,) in old_rows:
                new_uuid = str(uuid_lib.uuid4())
                await db.execute(
                    "UPDATE tickets SET uuid=? WHERE id=?",
                    (new_uuid, row_id)
                )

        # ----------------------------------------------------
        # INFRACTIONS
        # ----------------------------------------------------
        await db.execute("""
            CREATE TABLE IF NOT EXISTS infractions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT UNIQUE,
                guild_id INTEGER,
                user_id INTEGER,
                moderator_id INTEGER,
                action_type TEXT,
                reason TEXT,
                timestamp TEXT
            )
        """)

        cursor = await db.execute("PRAGMA table_info(infractions)")
        inf_columns = [row[1] for row in await cursor.fetchall()]

        if "guild_id" not in inf_columns:
            await db.execute(
                "ALTER TABLE infractions ADD COLUMN guild_id INTEGER DEFAULT NULL"
            )

        if "uuid" not in inf_columns:
            await db.execute(
                "ALTER TABLE infractions ADD COLUMN uuid TEXT DEFAULT NULL"
            )

        # Give old infractions UUIDs.
        cursor = await db.execute("""
            SELECT id
            FROM infractions
            WHERE uuid IS NULL OR uuid = ''
        """)

        old_rows = await cursor.fetchall()

        for (row_id,) in old_rows:
            new_uuid = str(uuid_lib.uuid4())
            await db.execute(
                "UPDATE infractions SET uuid=? WHERE id=?",
                (new_uuid, row_id)
            )

        cursor = await db.execute("PRAGMA table_info(user_stats)")
        stats_columns = [row[1] for row in await cursor.fetchall()]

        if not stats_columns:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    guild_id INTEGER,
                    user_id INTEGER,
                    message_count INTEGER DEFAULT 0,
                    bad_word_count INTEGER DEFAULT 0,
                    last_active TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
        elif "guild_id" not in stats_columns:
            # Legacy table migration.
            await db.execute(
                "ALTER TABLE user_stats RENAME TO old_user_stats"
            )

            await db.execute("""
                CREATE TABLE user_stats (
                    guild_id INTEGER,
                    user_id INTEGER,
                    message_count INTEGER DEFAULT 0,
                    bad_word_count INTEGER DEFAULT 0,
                    last_active TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)

            await db.execute("""
                INSERT INTO user_stats (
                    guild_id,
                    user_id,
                    message_count,
                    bad_word_count,
                    last_active
                )
                SELECT ?, user_id, message_count, bad_word_count, last_active
                FROM old_user_stats
            """, (config.GUILD_ID,))

            await db.execute("DROP TABLE old_user_stats")

        await db.commit()

    log_db(
        "INITIALIZE",
        "database",
        "Verified tables and schema columns"
    )



def generate_infraction_uuid(guild_id=None):
    """
    Generates a UUID which is easy to identify by guild.

    Example:
    G123456789-550e8400-e29b-41d4-a716-446655440000
    """

    prefix = f"G{guild_id}-" if guild_id else "GLOBAL-"

    return f"{prefix}{uuid_lib.uuid4()}"


 
async def add_infraction(
    user_id: int,
    moderator_id: int,
    action_type: str,
    reason: str,
    guild_id: int = None,
    custom_uuid: str = None
) -> str:

    tz = pytz.timezone("Europe/Berlin")
    now_str = datetime.now(tz).strftime("%d/%m/%Y - %H:%M")

    # ALWAYS generate a UUID.
    infraction_uuid = (
        str(custom_uuid).strip()
        if custom_uuid
        else generate_infraction_uuid(guild_id)
    )

    if not infraction_uuid:
        raise RuntimeError(
            "Failed to generate an infraction UUID."
        )

    async with aiosqlite.connect(config.DATABASE) as db:

        # Protect against UUID collision.
        if not custom_uuid:
            while True:
                cursor = await db.execute(
                    "SELECT 1 FROM infractions WHERE uuid=?",
                    (infraction_uuid,)
                )

                exists = await cursor.fetchone()

                if not exists:
                    break

                infraction_uuid = generate_infraction_uuid(guild_id)

        await db.execute("""
            INSERT INTO infractions (
                uuid,
                guild_id,
                user_id,
                moderator_id,
                action_type,
                reason,
                timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            infraction_uuid,
            guild_id,
            user_id,
            moderator_id,
            action_type,
            reason,
            now_str
        ))

        await db.commit()

    log_db(
        "INSERT",
        "infractions",
        (
            f"UUID: {infraction_uuid}, "
            f"Guild: {guild_id}, "
            f"User: {user_id}, "
            f"Mod: {moderator_id}, "
            f"Type: {action_type}, "
            f"Reason: {reason}"
        )
    )

   
    monitor_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "MonitorUUID"
    )

    os.makedirs(monitor_dir, exist_ok=True)

    log_content = (
        f"Event UUID: {infraction_uuid}\n"
        f"Action: {action_type}\n"
        f"Timestamp: {now_str}\n"
        f"User ID: {user_id}\n"
        f"Moderator ID: {moderator_id}\n"
        f"Reason: {reason}\n"
        f"Guild ID: {guild_id}\n"
    )

    file_path = os.path.join(
        monitor_dir,
        f"{infraction_uuid}.txt"
    )

    with open(file_path, "w", encoding="utf-8") as file:
        file.write(log_content)

    # IMPORTANT:
    # This MUST return the UUID.
    return infraction_uuid



async def get_user_infractions(
    user_id: int,
    guild_id: int = None
):
    async with aiosqlite.connect(config.DATABASE) as db:

        if guild_id is not None:
            cursor = await db.execute("""
                SELECT
                    id,
                    user_id,
                    moderator_id,
                    action_type,
                    reason,
                    timestamp,
                    guild_id,
                    uuid
                FROM infractions
                WHERE user_id=? AND guild_id=?
                ORDER BY id DESC
            """, (user_id, guild_id))
        else:
            cursor = await db.execute("""
                SELECT
                    id,
                    user_id,
                    moderator_id,
                    action_type,
                    reason,
                    timestamp,
                    guild_id,
                    uuid
                FROM infractions
                WHERE user_id=?
                ORDER BY id DESC
            """, (user_id,))

        rows = await cursor.fetchall()

    return [
        {
            "id": row[0],
            "user_id": row[1],
            "moderator_id": row[2],
            "action_type": row[3],
            "reason": row[4],
            "timestamp": row[5],
            "guild_id": row[6],
            "uuid": row[7]
        }
        for row in rows
    ]



async def get_infraction_by_uuid(uuid_str: str):
    if not uuid_str:
        return None

    uuid_str = str(uuid_str).strip()

    async with aiosqlite.connect(config.DATABASE) as db:

        cursor = await db.execute("""
            SELECT
                id,
                user_id,
                moderator_id,
                action_type,
                reason,
                timestamp,
                guild_id,
                uuid
            FROM infractions
            WHERE uuid=? OR uuid LIKE ?
            LIMIT 1
        """, (
            uuid_str,
            f"{uuid_str}%"
        ))

        row = await cursor.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "user_id": row[1],
        "moderator_id": row[2],
        "action_type": row[3],
        "reason": row[4],
        "timestamp": row[5],
        "guild_id": row[6],
        "uuid": row[7]
    }


async def remove_user_warning(
    user_id: int,
    warn_id=None,
    guild_id: int = None
):
    async with aiosqlite.connect(config.DATABASE) as db:

        if warn_id is not None:

            warn_str = str(warn_id).strip()

            numeric_id = (
                int(warn_str)
                if warn_str.isdigit()
                else -1
            )

            if guild_id is not None:
                cursor = await db.execute("""
                    SELECT
                        id,
                        reason,
                        timestamp,
                        uuid,
                        user_id
                    FROM infractions
                    WHERE
                        (uuid=?
                         OR uuid LIKE ?
                         OR id=?)
                        AND guild_id=?
                        AND action_type='WARN'
                    LIMIT 1
                """, (
                    warn_str,
                    f"{warn_str}%",
                    numeric_id,
                    guild_id
                ))
            else:
                cursor = await db.execute("""
                    SELECT
                        id,
                        reason,
                        timestamp,
                        uuid,
                        user_id
                    FROM infractions
                    WHERE
                        (uuid=?
                         OR uuid LIKE ?
                         OR id=?)
                        AND action_type='WARN'
                    LIMIT 1
                """, (
                    warn_str,
                    f"{warn_str}%",
                    numeric_id
                ))

            row = await cursor.fetchone()

            if not row:
                return 0, []

            infraction_id = row[0]

            await db.execute(
                "DELETE FROM infractions WHERE id=?",
                (infraction_id,)
            )

            await db.commit()

            log_db(
                "DELETE",
                "infractions",
                (
                    f"Removed warning #{infraction_id} "
                    f"(UUID: {row[3]}) "
                    f"for User ID {row[4]}"
                )
            )

            return 1, [{
                "id": row[0],
                "reason": row[1],
                "timestamp": row[2],
                "uuid": row[3],
                "user_id": row[4]
            }]

    
        if guild_id is not None:
            cursor = await db.execute("""
                SELECT
                    id,
                    reason,
                    timestamp,
                    uuid,
                    user_id
                FROM infractions
                WHERE
                    user_id=?
                    AND guild_id=?
                    AND action_type='WARN'
            """, (user_id, guild_id))
        else:
            cursor = await db.execute("""
                SELECT
                    id,
                    reason,
                    timestamp,
                    uuid,
                    user_id
                FROM infractions
                WHERE
                    user_id=?
                    AND action_type='WARN'
            """, (user_id,))

        rows = await cursor.fetchall()

        if not rows:
            return 0, []

        ids = [row[0] for row in rows]

        placeholders = ",".join("?" for _ in ids)

        await db.execute(
            f"DELETE FROM infractions WHERE id IN ({placeholders})",
            ids
        )

        await db.commit()

        log_db(
            "DELETE",
            "infractions",
            f"Cleared {len(rows)} warnings for User ID {user_id}"
        )

        return len(rows), [
            {
                "id": row[0],
                "reason": row[1],
                "timestamp": row[2],
                "uuid": row[3],
                "user_id": row[4]
            }
            for row in rows
        ]



async def remove_infraction_by_uuid(uuid_str: str):
    if not uuid_str:
        return None

    uuid_str = str(uuid_str).strip()

    async with aiosqlite.connect(config.DATABASE) as db:

        cursor = await db.execute("""
            SELECT
                id,
                user_id,
                action_type,
                reason,
                timestamp,
                uuid
            FROM infractions
            WHERE uuid=? OR uuid LIKE ?
            LIMIT 1
        """, (
            uuid_str,
            f"{uuid_str}%"
        ))

        row = await cursor.fetchone()

        if not row:
            return None

        await db.execute(
            "DELETE FROM infractions WHERE id=?",
            (row[0],)
        )

        await db.commit()

    log_db(
        "DELETE",
        "infractions",
        (
            f"Removed infraction UUID: {row[5]} "
            f"({row[2]}) for User ID {row[1]}"
        )
    )

    return {
        "id": row[0],
        "user_id": row[1],
        "action_type": row[2],
        "reason": row[3],
        "timestamp": row[4],
        "uuid": row[5]
    }


async def increment_user_activity(
    user_id: int,
    guild_id: int = None,
    has_bad_word: bool = False
):
    tz = pytz.timezone("Europe/Berlin")
    now_str = datetime.now(tz).strftime("%d/%m/%Y - %H:%M")

    gid = guild_id or config.GUILD_ID

    async with aiosqlite.connect(config.DATABASE) as db:

        bad_inc = 1 if has_bad_word else 0

        await db.execute("""
            INSERT INTO user_stats (
                guild_id,
                user_id,
                message_count,
                bad_word_count,
                last_active
            )
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(guild_id, user_id)
            DO UPDATE SET
                message_count = message_count + 1,
                bad_word_count = bad_word_count + excluded.bad_word_count,
                last_active = excluded.last_active
        """, (
            gid,
            user_id,
            bad_inc,
            now_str
        ))

        await db.commit()

        if has_bad_word:
            log_db(
                "UPSERT",
                "user_stats",
                (
                    f"Guild: {gid}, "
                    f"User: {user_id}, "
                    f"Bad Word: True"
                )
            )



async def get_user_stats(
    user_id: int,
    guild_id: int = None
):
    gid = guild_id or config.GUILD_ID

    async with aiosqlite.connect(config.DATABASE) as db:

        cursor = await db.execute("""
            SELECT
                user_id,
                message_count,
                bad_word_count,
                last_active
            FROM user_stats
            WHERE user_id=? AND guild_id=?
        """, (
            user_id,
            gid
        ))

        row = await cursor.fetchone()

    if row:
        return {
            "user_id": row[0],
            "message_count": row[1],
            "bad_word_count": row[2],
            "last_active": row[3]
        }

    return {
        "user_id": user_id,
        "message_count": 0,
        "bad_word_count": 0,
        "last_active": "Never"
    }


async def create_ticket_record(
    channel_id,
    guild_id,
    user_id,
    application,
    created_at
):
    ticket_uuid = str(uuid_lib.uuid4())
    async with aiosqlite.connect(config.DATABASE) as db:

        await db.execute("""
            INSERT INTO tickets (
                channel_id,
                guild_id,
                user_id,
                application,
                status,
                created_at,
                uuid
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            channel_id,
            guild_id,
            user_id,
            application,
            "open",
            created_at,
            ticket_uuid
        ))

        await db.commit()

    log_db(
        "INSERT",
        "tickets",
        (
            f"Created record for Channel: {channel_id}, "
            f"User: {user_id}, App: {application}, UUID: {ticket_uuid}"
        )
    )
    return ticket_uuid


async def close_ticket(
    channel_id,
    closed_at,
    closed_by=None,
    close_reason=None
):
    async with aiosqlite.connect(config.DATABASE) as db:

        await db.execute("""
            UPDATE tickets
            SET
                status=?,
                closed_at=?,
                closed_by=?,
                close_reason=?
            WHERE channel_id=?
        """, (
            "closed",
            closed_at,
            closed_by,
            close_reason,
            channel_id
        ))

        await db.commit()

    log_db(
        "UPDATE",
        "tickets",
        (
            f"Closed Channel: {channel_id}, "
            f"Closed By: {closed_by}, "
            f"Reason: {close_reason}"
        )
    )


async def reopen_ticket(channel_id):
    async with aiosqlite.connect(config.DATABASE) as db:

        await db.execute("""
            UPDATE tickets
            SET
                status=?,
                closed_at=NULL
            WHERE channel_id=?
        """, (
            "open",
            channel_id
        ))

        await db.commit()

    log_db(
        "UPDATE",
        "tickets",
        f"Reopened Channel: {channel_id}"
    )


async def get_ticket_owner(channel_id):
    async with aiosqlite.connect(config.DATABASE) as db:

        cursor = await db.execute("""
            SELECT user_id
            FROM tickets
            WHERE channel_id=?
        """, (channel_id,))

        result = await cursor.fetchone()

    return result[0] if result else None


async def get_next_ticket_number():
    async with aiosqlite.connect(config.DATABASE) as db:

        cursor = await db.execute(
            "SELECT COUNT(*) FROM tickets"
        )

        result = await cursor.fetchone()

    return result[0] + 1


async def claim_ticket(
    channel_id,
    user_id,
    claimed_at
):
    async with aiosqlite.connect(config.DATABASE) as db:

        await db.execute("""
            UPDATE tickets
            SET
                claimed_by=?,
                claimed_at=?
            WHERE channel_id=?
        """, (
            user_id,
            claimed_at,
            channel_id
        ))

        await db.commit()

    log_db(
        "UPDATE",
        "tickets",
        f"Claimed Channel: {channel_id} by User: {user_id}"
    )


async def get_ticket_record(channel_id):
    async with aiosqlite.connect(config.DATABASE) as db:

        cursor = await db.execute("""
            SELECT
                id,
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
                closed_by
            FROM tickets
            WHERE channel_id=?
        """, (channel_id,))

        row = await cursor.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "channel_id": row[1],
        "guild_id": row[2],
        "user_id": row[3],
        "application": row[4],
        "status": row[5],
        "created_at": row[6],
        "closed_at": row[7],
        "claimed_by": row[8],
        "close_reason": row[9],
        "priority": row[10],
        "claimed_at": row[11],
        "closed_by": row[12]
    }


async def set_ticket_priority(
    channel_id,
    priority
):
    async with aiosqlite.connect(config.DATABASE) as db:

        await db.execute("""
            UPDATE tickets
            SET priority=?
            WHERE channel_id=?
        """, (
            priority,
            channel_id
        ))

        await db.commit()

    log_db(
        "UPDATE",
        "tickets",
        (
            f"Priority set to '{priority}' "
            f"for Channel: {channel_id}"
        )
    )
