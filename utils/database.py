import asyncio
import json
import os
import shutil
import uuid as uuid_lib
from datetime import datetime
from pathlib import Path

import aiosqlite
import pytz

import config
from utils.logger import log_db, log_exception


async def setup_database():

    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")
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
            await db.execute("""
                ALTER TABLE tickets
                ADD COLUMN claimed_by INTEGER DEFAULT NULL
            """)

        if "close_reason" not in columns:
            await db.execute("""
                ALTER TABLE tickets
                ADD COLUMN close_reason TEXT DEFAULT NULL
            """)

        if "warned_inactive" not in columns:
            await db.execute("""
                ALTER TABLE tickets
                ADD COLUMN warned_inactive INTEGER DEFAULT 0
            """)

        if "warned_at" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN warned_at TEXT DEFAULT NULL"
            )

        if "priority" not in columns:
            await db.execute("""
                ALTER TABLE tickets
                ADD COLUMN priority TEXT DEFAULT 'Medium'
            """)

        if "label" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN label TEXT DEFAULT NULL"
            )

        if "closed_by" not in columns:
            await db.execute("""
                ALTER TABLE tickets
                ADD COLUMN closed_by INTEGER DEFAULT NULL
            """)

        if "claimed_at" not in columns:
            await db.execute("""
                ALTER TABLE tickets
                ADD COLUMN claimed_at TEXT DEFAULT NULL
            """)

        if "claim_changed_at" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN claim_changed_at TEXT DEFAULT NULL"
            )

        if "control_message_id" not in columns:
            await db.execute(
                "ALTER TABLE tickets ADD COLUMN control_message_id INTEGER DEFAULT NULL"
            )

        if "uuid" not in columns:
            await db.execute("""
                ALTER TABLE tickets
                ADD COLUMN uuid TEXT DEFAULT NULL
            """)

        cursor = await db.execute("SELECT id, uuid FROM tickets ORDER BY id")
        repaired_ticket_count = 0
        seen_ticket_uuids = set()
        for row_id, current_uuid in await cursor.fetchall():
            normalized_uuid = str(current_uuid or "").strip()
            if not normalized_uuid or normalized_uuid in seen_ticket_uuids:
                normalized_uuid = str(uuid_lib.uuid4())
                while normalized_uuid in seen_ticket_uuids:
                    normalized_uuid = str(uuid_lib.uuid4())
                await db.execute(
                    "UPDATE tickets SET uuid=? WHERE id=?",
                    (normalized_uuid, row_id),
                )
                repaired_ticket_count += 1
            seen_ticket_uuids.add(normalized_uuid)

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
            await db.execute("""
                ALTER TABLE infractions
                ADD COLUMN guild_id INTEGER DEFAULT NULL
            """)

        if "uuid" not in inf_columns:
            await db.execute("""
                ALTER TABLE infractions
                ADD COLUMN uuid TEXT DEFAULT NULL
            """)

        cursor = await db.execute(
            "SELECT id, guild_id, uuid FROM infractions ORDER BY id"
        )
        repaired_infraction_count = 0
        seen_infraction_uuids = set()
        for row_id, guild_id, current_uuid in await cursor.fetchall():
            normalized_uuid = str(current_uuid or "").strip()
            if not normalized_uuid or normalized_uuid in seen_infraction_uuids:
                normalized_uuid = generate_infraction_uuid(guild_id or 0)
                while normalized_uuid in seen_infraction_uuids:
                    normalized_uuid = generate_infraction_uuid(guild_id or 0)
                await db.execute(
                    "UPDATE infractions SET uuid=? WHERE id=?",
                    (normalized_uuid, row_id),
                )
                repaired_infraction_count += 1
            seen_infraction_uuids.add(normalized_uuid)

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
            await db.execute("""
                ALTER TABLE user_stats
                RENAME TO old_user_stats
            """)

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

            await db.execute(
                """
                INSERT INTO user_stats (
                    guild_id,
                    user_id,
                    message_count,
                    bad_word_count,
                    last_active
                )
                SELECT
                    ?,
                    user_id,
                    message_count,
                    bad_word_count,
                    last_active
                FROM old_user_stats
            """,
                (0,),
            )

            await db.execute("DROP TABLE old_user_stats")

        cursor = await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_channel_id ON tickets(channel_id)"
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_uuid ON tickets(uuid)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets(guild_id, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_tickets_guild_user_status ON tickets(guild_id, user_id, status)"
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_infractions_uuid ON infractions(uuid)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_infractions_guild_user ON infractions(guild_id, user_id)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS ticket_counters (guild_id INTEGER PRIMARY KEY, next_number INTEGER NOT NULL)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS staff_availability (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS ticket_panels (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (guild_id, message_id))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS escalation_events (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, event_key TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (guild_id, channel_id, event_key))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS guild_settings (guild_id INTEGER PRIMARY KEY, name TEXT NOT NULL, ticket_category_id INTEGER NOT NULL DEFAULT 0, ticket_panel_channel_id INTEGER NOT NULL DEFAULT 0, ticket_archive_category_id INTEGER NOT NULL DEFAULT 0, log_channel_id INTEGER NOT NULL DEFAULT 0, owner_role_ids TEXT NOT NULL DEFAULT '[]', mod_role_id INTEGER NOT NULL DEFAULT 0, trial_mod_role_id INTEGER NOT NULL DEFAULT 0, warn_history_role_id INTEGER NOT NULL DEFAULT 0, allowed_ban_user_ids TEXT NOT NULL DEFAULT '[]', ticket_options TEXT NOT NULL DEFAULT '[]', timezone TEXT NOT NULL DEFAULT 'Europe/Athens', setup_complete INTEGER NOT NULL DEFAULT 0, welcome_sent INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS guild_setup_admins (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS afk_status (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, reason TEXT NOT NULL, set_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))"
        )
        guild_settings_cursor = await db.execute("PRAGMA table_info(guild_settings)")
        guild_settings_columns = [
            row[1] for row in await guild_settings_cursor.fetchall()
        ]
        if "auto_assign_tickets" not in guild_settings_columns:
            await db.execute(
                "ALTER TABLE guild_settings ADD COLUMN auto_assign_tickets INTEGER NOT NULL DEFAULT 0"
            )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS error_events (reference TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, category TEXT NOT NULL, error_type TEXT NOT NULL, message TEXT NOT NULL, traceback TEXT NOT NULL, context TEXT, guild_id INTEGER, channel_id INTEGER, user_id INTEGER, occurrence_count INTEGER NOT NULL DEFAULT 1, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS performance_events (id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL, duration_ms REAL NOT NULL, threshold_ms REAL NOT NULL, guild_id INTEGER, created_at TEXT NOT NULL)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_availability_status ON staff_availability(guild_id, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_error_events_last_seen ON error_events(last_seen DESC)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_performance_events_created_at ON performance_events(created_at DESC)"
        )
        cursor = await db.execute(
            "SELECT guild_id, name, ticket_category_id, ticket_panel_channel_id, ticket_archive_category_id, log_channel_id, owner_role_ids, mod_role_id, trial_mod_role_id, warn_history_role_id, allowed_ban_user_ids, ticket_options, timezone, setup_complete, welcome_sent, auto_assign_tickets FROM guild_settings"
        )
        admin_cursor = await db.execute(
            "SELECT guild_id, user_id FROM guild_setup_admins ORDER BY user_id"
        )
        setup_admins = {}
        for guild_id, user_id in await admin_cursor.fetchall():
            setup_admins.setdefault(guild_id, []).append(user_id)
        persistent_settings = {}
        for row in await cursor.fetchall():
            persistent_settings[row[0]] = {
                "NAME": row[1],
                "TICKET_CATEGORY_ID": row[2],
                "TICKET_PANEL_CHANNEL_ID": row[3],
                "TICKET_ARCHIVE_CATEGORY_ID": row[4],
                "LOG_CHANNEL_ID": row[5],
                "OWNER_ROLES": json.loads(row[6]),
                "MOD_ROLE": row[7],
                "TRIAL_MOD_ROLE": row[8],
                "WARN_HISTORY_ROLE_ID": row[9],
                "ALLOWED_BAN_USERS": json.loads(row[10]),
                "SETUP_ADMIN_USERS": setup_admins.get(row[0], []),
                "TICKET_OPTIONS": json.loads(row[11]),
                "TIMEZONE": row[12],
                "SETUP_COMPLETE": bool(row[13]),
                "WELCOME_SENT": bool(row[14]),
                "AUTO_ASSIGN_TICKETS": bool(row[15]),
            }
        config.replace_guild_configs(persistent_settings)
        for guild_id in config.GUILDS:
            cursor = await db.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM tickets WHERE guild_id=?",
                (guild_id,),
            )
            suggested = (await cursor.fetchone())[0]
            await db.execute(
                "INSERT INTO ticket_counters(guild_id, next_number) VALUES(?, ?) ON CONFLICT(guild_id) DO UPDATE SET next_number=MAX(ticket_counters.next_number, excluded.next_number)",
                (guild_id, suggested),
            )
        await db.commit()

    log_db(
        "INITIALIZE",
        "database",
        (
            "Verified tables, schema columns, "
            f"repaired {repaired_ticket_count} ticket UUID(s), "
            f"repaired {repaired_infraction_count} infraction UUID(s)"
        ),
    )


async def save_guild_settings(guild_id, settings):
    normalized = config.normalize_guild_config(guild_id, settings)
    now_value = datetime.now(pytz.timezone(config.TIMEZONE)).isoformat()
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "INSERT INTO guild_settings(guild_id, name, ticket_category_id, ticket_panel_channel_id, ticket_archive_category_id, log_channel_id, owner_role_ids, mod_role_id, trial_mod_role_id, warn_history_role_id, allowed_ban_user_ids, ticket_options, timezone, setup_complete, welcome_sent, auto_assign_tickets, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET name=excluded.name, ticket_category_id=excluded.ticket_category_id, ticket_panel_channel_id=excluded.ticket_panel_channel_id, ticket_archive_category_id=excluded.ticket_archive_category_id, log_channel_id=excluded.log_channel_id, owner_role_ids=excluded.owner_role_ids, mod_role_id=excluded.mod_role_id, trial_mod_role_id=excluded.trial_mod_role_id, warn_history_role_id=excluded.warn_history_role_id, allowed_ban_user_ids=excluded.allowed_ban_user_ids, ticket_options=excluded.ticket_options, timezone=excluded.timezone, setup_complete=excluded.setup_complete, welcome_sent=excluded.welcome_sent, auto_assign_tickets=excluded.auto_assign_tickets, updated_at=excluded.updated_at",
            (
                int(guild_id),
                normalized["NAME"],
                normalized["TICKET_CATEGORY_ID"],
                normalized["TICKET_PANEL_CHANNEL_ID"],
                normalized["TICKET_ARCHIVE_CATEGORY_ID"],
                normalized["LOG_CHANNEL_ID"],
                json.dumps(normalized["OWNER_ROLES"]),
                normalized["MOD_ROLE"],
                normalized["TRIAL_MOD_ROLE"],
                normalized["WARN_HISTORY_ROLE_ID"],
                json.dumps(normalized["ALLOWED_BAN_USERS"]),
                json.dumps(normalized["TICKET_OPTIONS"]),
                normalized["TIMEZONE"],
                int(normalized["SETUP_COMPLETE"]),
                int(normalized["WELCOME_SENT"]),
                int(normalized["AUTO_ASSIGN_TICKETS"]),
                now_value,
                now_value,
            ),
        )
        await db.execute(
            "INSERT OR IGNORE INTO ticket_counters(guild_id, next_number) VALUES(?, 1)",
            (int(guild_id),),
        )
        await db.execute(
            "DELETE FROM guild_setup_admins WHERE guild_id=?", (int(guild_id),)
        )
        for user_id in normalized["SETUP_ADMIN_USERS"]:
            await db.execute(
                "INSERT INTO guild_setup_admins(guild_id, user_id, added_at) VALUES(?, ?, ?)",
                (int(guild_id), int(user_id), now_value),
            )
        await db.commit()
    return config.register_guild_config(guild_id, normalized)


async def get_guild_settings(guild_id):
    return config.GUILDS.get(int(guild_id))


async def reset_guild_settings(guild_id):
    current = config.GUILDS.get(int(guild_id), {})
    reset_settings = await save_guild_settings(
        guild_id,
        {
            "NAME": current.get("NAME", f"Server {guild_id}"),
            "SETUP_ADMIN_USERS": current.get("SETUP_ADMIN_USERS", []),
            "SETUP_COMPLETE": False,
            "WELCOME_SENT": True,
        },
    )
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "DELETE FROM staff_availability WHERE guild_id=?", (int(guild_id),)
        )
        await db.execute("DELETE FROM ticket_panels WHERE guild_id=?", (int(guild_id),))
        await db.execute(
            "DELETE FROM escalation_events WHERE guild_id=?", (int(guild_id),)
        )
        await db.commit()
    return reset_settings


async def purge_guild_data(guild_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT uuid FROM infractions WHERE guild_id=? AND uuid IS NOT NULL",
            (int(guild_id),),
        )
        infraction_uuids = [row[0] for row in await cursor.fetchall()]
        for statement in (
            "DELETE FROM guild_settings WHERE guild_id=?",
            "DELETE FROM guild_setup_admins WHERE guild_id=?",
            "DELETE FROM staff_availability WHERE guild_id=?",
            "DELETE FROM ticket_panels WHERE guild_id=?",
            "DELETE FROM escalation_events WHERE guild_id=?",
            "DELETE FROM ticket_counters WHERE guild_id=?",
            "DELETE FROM tickets WHERE guild_id=?",
            "DELETE FROM infractions WHERE guild_id=?",
            "DELETE FROM user_stats WHERE guild_id=?",
            "DELETE FROM error_events WHERE guild_id=?",
            "DELETE FROM performance_events WHERE guild_id=?",
            "DELETE FROM afk_status WHERE guild_id=?",
        ):
            await db.execute(statement, (int(guild_id),))
        await db.commit()
    transcript_directory = os.path.join(config.TRANSCRIPT_FOLDER, str(int(guild_id)))
    if os.path.isdir(transcript_directory):
        await asyncio.to_thread(shutil.rmtree, transcript_directory)
    monitor_directory = os.path.join(config.BASE_DIR, "MonitorUUID")
    for infraction_uuid in infraction_uuids:
        record_path = os.path.join(monitor_directory, f"{infraction_uuid}.txt")
        if os.path.isfile(record_path):
            await asyncio.to_thread(os.remove, record_path)
    config.remove_guild_config(guild_id)


async def add_setup_admin(guild_id, user_id):
    current = dict(
        config.GUILDS.get(int(guild_id)) or config.normalize_guild_config(guild_id)
    )
    admin_users = list(current.get("SETUP_ADMIN_USERS", []))
    if int(user_id) in admin_users:
        return False, current
    admin_users.append(int(user_id))
    current["SETUP_ADMIN_USERS"] = admin_users
    return True, await save_guild_settings(guild_id, current)


async def remove_setup_admin(guild_id, user_id):
    current = dict(
        config.GUILDS.get(int(guild_id)) or config.normalize_guild_config(guild_id)
    )
    admin_users = list(current.get("SETUP_ADMIN_USERS", []))
    if int(user_id) not in admin_users:
        return False, current
    current["SETUP_ADMIN_USERS"] = [
        admin_id for admin_id in admin_users if admin_id != int(user_id)
    ]
    return True, await save_guild_settings(guild_id, current)


def generate_infraction_uuid(guild_id):

    prefix = f"G{guild_id}-"

    return f"{prefix}{uuid_lib.uuid4()}"


async def add_infraction(
    user_id: int,
    moderator_id: int,
    action_type: str,
    reason: str,
    guild_id: int,
    custom_uuid: str | None = None,
) -> str:

    if not guild_id:
        raise ValueError("guild_id is required")

    tz = pytz.timezone(config.TIMEZONE)

    now_str = datetime.now(tz).strftime("%d/%m/%Y - %H:%M")

    infraction_uuid = (
        str(custom_uuid).strip() if custom_uuid else generate_infraction_uuid(guild_id)
    )

    if not infraction_uuid:
        raise RuntimeError("Failed to generate an infraction UUID.")

    async with aiosqlite.connect(config.DATABASE) as db:
        if not custom_uuid:
            while True:
                cursor = await db.execute(
                    """
                    SELECT 1
                    FROM infractions
                    WHERE uuid=?
                    """,
                    (infraction_uuid,),
                )

                exists = await cursor.fetchone()

                if not exists:
                    break

                infraction_uuid = generate_infraction_uuid(guild_id)

        await db.execute(
            """
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
        """,
            (
                infraction_uuid,
                guild_id,
                user_id,
                moderator_id,
                action_type,
                reason,
                now_str,
            ),
        )

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
        ),
    )

    monitor_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "MonitorUUID"
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

    file_path = os.path.join(monitor_dir, f"{infraction_uuid}.txt")

    try:
        await asyncio.to_thread(
            Path(file_path).write_text, log_content, encoding="utf-8"
        )
    except OSError as error:
        log_exception(
            "DATABASE",
            error,
            guild=guild_id,
            user=user_id,
            context=f"Infraction mirror file could not be written for {infraction_uuid}",
        )

    return infraction_uuid


async def get_user_infractions(user_id: int, guild_id: int):

    if not guild_id:
        raise ValueError("guild_id is required")

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
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
        """,
            (user_id, guild_id),
        )

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
            "uuid": row[7],
        }
        for row in rows
    ]


async def get_infraction_by_uuid(
    uuid_str: str,
    guild_id: int,
):

    if not uuid_str:
        return None

    uuid_str = str(uuid_str).strip().strip("`").strip()

    if not uuid_str:
        return None

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
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
            WHERE
                guild_id=?
                AND (
                    uuid=?
                    OR substr(uuid, 1, length(?))=?
                    OR substr(uuid, -length(?))=?
                )
            ORDER BY CASE WHEN uuid=? THEN 0 ELSE 1 END, id DESC
            LIMIT 2
        """,
            (
                guild_id,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
            ),
        )

        rows = await cursor.fetchall()

    row = next((item for item in rows if item[7] == uuid_str), None)
    if row is None and len(rows) == 1:
        row = rows[0]

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
        "uuid": row[7],
    }


async def get_ticket_by_uuid(
    uuid_str: str,
    guild_id: int,
):

    if not uuid_str:
        return None

    uuid_str = str(uuid_str).strip().strip("`").strip()

    if not uuid_str:
        return None

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
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
                closed_by,
                warned_inactive,
                uuid,
                control_message_id,
                label
            FROM tickets
            WHERE
                guild_id=?
                AND (
                    uuid=?
                    OR substr(uuid, 1, length(?))=?
                    OR substr(uuid, -length(?))=?
                )
            ORDER BY CASE WHEN uuid=? THEN 0 ELSE 1 END, id DESC
            LIMIT 2
        """,
            (
                guild_id,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
            ),
        )

        rows = await cursor.fetchall()

    row = next((item for item in rows if item[14] == uuid_str), None)
    if row is None and len(rows) == 1:
        row = rows[0]

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
        "closed_by": row[12],
        "warned_inactive": row[13],
        "uuid": row[14],
        "control_message_id": row[15],
        "label": row[16],
    }


async def remove_user_warning(user_id: int, guild_id: int, warn_id=None):

    if not guild_id:
        raise ValueError("guild_id is required")

    async with aiosqlite.connect(config.DATABASE) as db:
        if warn_id is not None:
            warn_str = str(warn_id).strip().strip("`").strip()

            numeric_id = int(warn_str) if warn_str.isdigit() else -1

            cursor = await db.execute(
                """
                    SELECT
                        id,
                        reason,
                        timestamp,
                        uuid,
                        user_id
                    FROM infractions
                    WHERE
                        (
                            uuid=?
                            OR substr(uuid, 1, length(?))=?
                            OR substr(uuid, -length(?))=?
                            OR id=?
                        )
                        AND guild_id=?
                        AND user_id=?
                        AND action_type='WARN'
                    ORDER BY CASE WHEN uuid=? OR id=? THEN 0 ELSE 1 END, id DESC
                    LIMIT 2
            """,
                (
                    warn_str,
                    warn_str,
                    warn_str,
                    warn_str,
                    warn_str,
                    numeric_id,
                    guild_id,
                    user_id,
                    warn_str,
                    numeric_id,
                ),
            )

            rows = await cursor.fetchall()
            row = next(
                (
                    item
                    for item in rows
                    if item[3] == warn_str or item[0] == numeric_id
                ),
                None,
            )
            if row is None and len(rows) == 1:
                row = rows[0]

            if not row:
                return 0, []

            infraction_id = row[0]

            await db.execute(
                """
                DELETE FROM infractions
                WHERE id=?
                """,
                (infraction_id,),
            )

            await db.commit()

            log_db(
                "DELETE",
                "infractions",
                (
                    f"Removed warning #{infraction_id} "
                    f"(UUID: {row[3]}) "
                    f"for User ID {row[4]}"
                ),
            )

            return 1, [
                {
                    "id": row[0],
                    "reason": row[1],
                    "timestamp": row[2],
                    "uuid": row[3],
                    "user_id": row[4],
                }
            ]

        cursor = await db.execute(
            """
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
        """,
            (user_id, guild_id),
        )

        rows = await cursor.fetchall()

        if not rows:
            return 0, []

        await db.execute(
            "DELETE FROM infractions WHERE user_id=? AND guild_id=? AND action_type='WARN'",
            (user_id, guild_id),
        )

        await db.commit()

        log_db(
            "DELETE",
            "infractions",
            (f"Cleared {len(rows)} warnings for User ID {user_id}"),
        )

        return len(rows), [
            {
                "id": row[0],
                "reason": row[1],
                "timestamp": row[2],
                "uuid": row[3],
                "user_id": row[4],
            }
            for row in rows
        ]


async def remove_infraction_by_uuid(
    uuid_str: str,
    guild_id: int,
):

    if not uuid_str:
        return None

    uuid_str = str(uuid_str).strip().strip("`").strip()

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
            SELECT
                id,
                user_id,
                action_type,
                reason,
                timestamp,
                uuid
            FROM infractions
            WHERE
                guild_id=?
                AND (
                    uuid=?
                    OR substr(uuid, 1, length(?))=?
                    OR substr(uuid, -length(?))=?
                )
            ORDER BY CASE WHEN uuid=? THEN 0 ELSE 1 END, id DESC
            LIMIT 2
        """,
            (
                guild_id,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
                uuid_str,
            ),
        )

        rows = await cursor.fetchall()
        row = next((item for item in rows if item[5] == uuid_str), None)
        if row is None and len(rows) == 1:
            row = rows[0]

        if not row:
            return None

        await db.execute(
            """
            DELETE FROM infractions
            WHERE id=?
            """,
            (row[0],),
        )

        await db.commit()

    log_db(
        "DELETE",
        "infractions",
        (f"Removed infraction UUID: {row[5]} ({row[2]}) for User ID {row[1]}"),
    )

    return {
        "id": row[0],
        "user_id": row[1],
        "action_type": row[2],
        "reason": row[3],
        "timestamp": row[4],
        "uuid": row[5],
    }


async def increment_user_activity(
    user_id: int, guild_id: int, has_bad_word: bool = False
):

    if not guild_id:
        raise ValueError("guild_id is required")

    tz = pytz.timezone(config.TIMEZONE)

    now_str = datetime.now(tz).strftime("%d/%m/%Y - %H:%M")

    gid = guild_id

    async with aiosqlite.connect(config.DATABASE) as db:
        bad_inc = 1 if has_bad_word else 0

        await db.execute(
            """
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
        """,
            (gid, user_id, bad_inc, now_str),
        )

        await db.commit()

        if has_bad_word:
            log_db(
                "UPSERT",
                "user_stats",
                (f"Guild: {gid}, User: {user_id}, Bad Word: True"),
            )


async def get_user_stats(user_id: int, guild_id: int):

    if not guild_id:
        raise ValueError("guild_id is required")

    gid = guild_id

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
            SELECT
                user_id,
                message_count,
                bad_word_count,
                last_active
            FROM user_stats
            WHERE user_id=? AND guild_id=?
        """,
            (user_id, gid),
        )

        row = await cursor.fetchone()

    if row:
        return {
            "user_id": row[0],
            "message_count": row[1],
            "bad_word_count": row[2],
            "last_active": row[3],
        }

    return {
        "user_id": user_id,
        "message_count": 0,
        "bad_word_count": 0,
        "last_active": "Never",
    }


async def create_ticket_record(channel_id, guild_id, user_id, application, created_at):

    ticket_uuid = str(uuid_lib.uuid4()).strip()

    if not ticket_uuid:
        raise RuntimeError("Failed to generate ticket UUID.")

    async with aiosqlite.connect(config.DATABASE) as db:
        while True:
            cursor = await db.execute(
                """
                SELECT 1
                FROM tickets
                WHERE uuid=?
                LIMIT 1
                """,
                (ticket_uuid,),
            )

            exists = await cursor.fetchone()

            if not exists:
                break

            ticket_uuid = str(uuid_lib.uuid4()).strip()

        await db.execute(
            """
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
        """,
            (
                channel_id,
                guild_id,
                user_id,
                application,
                "open",
                created_at,
                ticket_uuid,
            ),
        )

        await db.commit()

    log_db(
        "INSERT",
        "tickets",
        (
            f"Created record for Channel: {channel_id}, "
            f"User: {user_id}, "
            f"App: {application}, "
            f"UUID: {ticket_uuid}"
        ),
    )

    return ticket_uuid


async def get_open_ticket_for_user(guild_id, user_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT channel_id, uuid FROM tickets WHERE guild_id=? AND user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (int(guild_id), int(user_id)),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return {"channel_id": row[0], "uuid": row[1]}


async def close_ticket(channel_id, closed_at, closed_by=None, close_reason=None):

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET
                status=?,
                closed_at=?,
                closed_by=?,
                close_reason=?
            WHERE channel_id=? AND status='open'
        """,
            ("closed", closed_at, closed_by, close_reason, channel_id),
        )

        await db.commit()

        closed = cursor.rowcount == 1

    log_db(
        "UPDATE",
        "tickets",
        (
            f"{'Closed' if closed else 'Close skipped for'} Channel: {channel_id}, "
            f"Closed By: {closed_by}, "
            f"Reason: {close_reason}"
        ),
    )

    return closed


async def reopen_ticket(channel_id):

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET
                status=?,
                closed_at=NULL,
                closed_by=NULL,
                close_reason=NULL,
                warned_inactive=0,
                warned_at=NULL
            WHERE channel_id=? AND status='closed'
        """,
            ("open", channel_id),
        )

        await db.commit()

    reopened = cursor.rowcount == 1
    log_db(
        "UPDATE",
        "tickets",
        f"{'Reopened' if reopened else 'Reopen skipped for'} Channel: {channel_id}",
    )
    return reopened


async def mark_ticket_deleted(channel_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "UPDATE tickets SET status='deleted', closed_at=COALESCE(closed_at, ?) WHERE channel_id=?",
            (datetime.now(pytz.timezone(config.TIMEZONE)).isoformat(), channel_id),
        )
        await db.commit()


async def get_ticket_owner(channel_id):

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
            SELECT user_id
            FROM tickets
            WHERE channel_id=? AND status='open'
        """,
            (channel_id,),
        )

        result = await cursor.fetchone()

    return result[0] if result else None


async def get_next_ticket_number(guild_id):
    config.get_guild_config(guild_id)
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT next_number FROM ticket_counters WHERE guild_id=?", (guild_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            cursor = await db.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM tickets WHERE guild_id=?",
                (guild_id,),
            )
            number = (await cursor.fetchone())[0]
            await db.execute(
                "INSERT INTO ticket_counters(guild_id, next_number) VALUES(?, ?)",
                (guild_id, number + 1),
            )
        else:
            number = row[0]
            await db.execute(
                "UPDATE ticket_counters SET next_number=? WHERE guild_id=?",
                (number + 1, guild_id),
            )
        await db.commit()
    return number


async def claim_ticket(channel_id, user_id, claimed_at):

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET
                claimed_by=?,
                claimed_at=?
            WHERE channel_id=? AND status='open' AND claimed_by IS NULL
        """,
            (user_id, claimed_at, channel_id),
        )

        await db.commit()

        claimed = cursor.rowcount == 1

    log_db(
        "UPDATE",
        "tickets",
        (
            f"{'Claimed' if claimed else 'Claim skipped for'} Channel: {channel_id} "
            f"by User: {user_id}"
        ),
    )

    return claimed


async def toggle_ticket_claim(channel_id, user_id, changed_at, cooldown_seconds=3):
    changed_time = datetime.fromisoformat(changed_at)
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT status, claimed_by, claim_changed_at FROM tickets WHERE channel_id=?",
            (int(channel_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.rollback()
            return {"status": "not_found"}
        status, previous_claimed_by, previous_changed_at = row
        if status != "open":
            await db.rollback()
            return {"status": "not_open", "claimed_by": previous_claimed_by}
        if previous_changed_at:
            try:
                elapsed = (
                    changed_time - datetime.fromisoformat(previous_changed_at)
                ).total_seconds()
            except (TypeError, ValueError):
                elapsed = cooldown_seconds
            if elapsed < cooldown_seconds:
                await db.rollback()
                return {
                    "status": "cooldown",
                    "claimed_by": previous_claimed_by,
                    "remaining": max(1, int(cooldown_seconds - elapsed + 0.999)),
                }
        if previous_claimed_by is None:
            await db.execute(
                "UPDATE tickets SET claimed_by=?, claimed_at=?, claim_changed_at=? WHERE channel_id=? AND status='open'",
                (int(user_id), changed_at, changed_at, int(channel_id)),
            )
            result = {
                "status": "claimed",
                "claimed_by": int(user_id),
                "previous_claimed_by": None,
            }
        else:
            await db.execute(
                "UPDATE tickets SET claimed_by=NULL, claimed_at=NULL, claim_changed_at=? WHERE channel_id=? AND status='open'",
                (changed_at, int(channel_id)),
            )
            result = {
                "status": "unclaimed",
                "claimed_by": None,
                "previous_claimed_by": previous_claimed_by,
            }
        await db.commit()
    return result


async def set_ticket_control_message(channel_id, message_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "UPDATE tickets SET control_message_id=? WHERE channel_id=?",
            (int(message_id), int(channel_id)),
        )
        await db.commit()


async def get_ticket_controls():
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT channel_id, control_message_id, claimed_by, application, status, label FROM tickets WHERE status IN ('open', 'closed')"
        )
        rows = await cursor.fetchall()
    return [
        {
            "channel_id": row[0],
            "control_message_id": row[1],
            "claimed_by": row[2],
            "application": row[3],
            "status": row[4],
            "label": row[5],
        }
        for row in rows
    ]


async def auto_assign_ticket(channel_id, guild_id, candidate_ids, claimed_at):
    candidates = sorted({int(user_id) for user_id in candidate_ids})
    if not candidates:
        return None
    placeholders = ",".join("?" for _ in candidates)
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            f"SELECT claimed_by, COUNT(*) FROM tickets WHERE guild_id=? AND status='open' AND claimed_by IN ({placeholders}) GROUP BY claimed_by",
            (int(guild_id), *candidates),
        )
        workloads = {row[0]: row[1] for row in await cursor.fetchall()}
        selected = min(
            candidates, key=lambda user_id: (workloads.get(user_id, 0), user_id)
        )
        cursor = await db.execute(
            "UPDATE tickets SET claimed_by=?, claimed_at=?, claim_changed_at=? WHERE channel_id=? AND status='open' AND claimed_by IS NULL",
            (selected, claimed_at, claimed_at, int(channel_id)),
        )
        await db.commit()
    return selected if cursor.rowcount == 1 else None


async def set_afk_status(guild_id, user_id, reason, set_at):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "INSERT INTO afk_status(guild_id, user_id, reason, set_at) VALUES(?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET reason=excluded.reason, set_at=excluded.set_at",
            (int(guild_id), int(user_id), reason, set_at),
        )
        await db.commit()


async def clear_afk_status(guild_id, user_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "DELETE FROM afk_status WHERE guild_id=? AND user_id=?",
            (int(guild_id), int(user_id)),
        )
        await db.commit()
    return cursor.rowcount == 1


async def get_afk_statuses(guild_id, user_ids):
    ids = sorted({int(user_id) for user_id in user_ids})
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            f"SELECT user_id, reason, set_at FROM afk_status WHERE guild_id=? AND user_id IN ({placeholders})",
            (int(guild_id), *ids),
        )
        rows = await cursor.fetchall()
    return [{"user_id": row[0], "reason": row[1], "set_at": row[2]} for row in rows]


async def get_all_afk_user_ids():
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute("SELECT guild_id, user_id FROM afk_status")
        rows = await cursor.fetchall()
    return {(row[0], row[1]) for row in rows}


async def process_afk_message(guild_id, author_id, mentioned_user_ids):
    ids = sorted(
        {
            int(user_id)
            for user_id in mentioned_user_ids
            if int(user_id) != int(author_id)
        }
    )
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT 1 FROM afk_status WHERE guild_id=? AND user_id=?",
            (int(guild_id), int(author_id)),
        )
        removed = await cursor.fetchone() is not None
        if removed:
            await db.execute(
                "DELETE FROM afk_status WHERE guild_id=? AND user_id=?",
                (int(guild_id), int(author_id)),
            )
        records = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            cursor = await db.execute(
                f"SELECT user_id, reason, set_at FROM afk_status WHERE guild_id=? AND user_id IN ({placeholders})",
                (int(guild_id), *ids),
            )
            records = [
                {"user_id": row[0], "reason": row[1], "set_at": row[2]}
                for row in await cursor.fetchall()
            ]
        if removed:
            await db.commit()
    return removed, records


async def get_ticket_record(channel_id):

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
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
                closed_by,
                warned_inactive,
                uuid,
                control_message_id,
                label
            FROM tickets
            WHERE channel_id=?
        """,
            (channel_id,),
        )

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
        "closed_by": row[12],
        "warned_inactive": row[13],
        "uuid": row[14],
        "control_message_id": row[15],
        "label": row[16],
    }


async def set_ticket_priority(channel_id, priority):

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
            UPDATE tickets
            SET priority=?
            WHERE channel_id=? AND status='open'
        """,
            (priority, channel_id),
        )

        await db.commit()

    updated = cursor.rowcount == 1
    log_db(
        "UPDATE",
        "tickets",
        f"{'Priority updated' if updated else 'Priority update skipped'} for Channel: {channel_id} | Value: {priority}",
    )
    return updated


async def set_ticket_label(channel_id, label):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "UPDATE tickets SET label=? WHERE channel_id=? AND status='open'",
            (label, int(channel_id)),
        )
        await db.commit()
    updated = cursor.rowcount == 1
    log_db(
        "UPDATE",
        "tickets",
        f"{'Label updated' if updated else 'Label update skipped'} for Channel: {channel_id} | Value: {label or 'None'}",
    )
    return updated


async def set_staff_availability(guild_id, user_id, status, updated_at):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "INSERT INTO staff_availability(guild_id, user_id, status, updated_at) VALUES(?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
            (guild_id, user_id, status, updated_at),
        )
        await db.commit()
    log_db(
        "UPSERT",
        "staff_availability",
        f"Guild: {guild_id}, User: {user_id}, Status: {status}",
    )


async def get_staff_availability(guild_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT user_id, status, updated_at FROM staff_availability WHERE guild_id=? ORDER BY CASE status WHEN 'Available' THEN 0 WHEN 'Busy' THEN 1 WHEN 'On Break' THEN 2 WHEN 'Away' THEN 3 ELSE 4 END, updated_at DESC",
            (guild_id,),
        )
        rows = await cursor.fetchall()
    return [{"user_id": row[0], "status": row[1], "updated_at": row[2]} for row in rows]


async def get_available_staff_count(guild_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM staff_availability WHERE guild_id=? AND status='Available'",
            (guild_id,),
        )
        row = await cursor.fetchone()
    return row[0]


async def register_ticket_panel(guild_id, channel_id, message_id, created_at):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO ticket_panels(guild_id, channel_id, message_id, created_at) VALUES(?, ?, ?, ?)",
            (guild_id, channel_id, message_id, created_at),
        )
        await db.commit()


async def get_ticket_panels(guild_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT channel_id, message_id FROM ticket_panels WHERE guild_id=? ORDER BY created_at DESC",
            (guild_id,),
        )
        rows = await cursor.fetchall()
    return [{"channel_id": row[0], "message_id": row[1]} for row in rows]


async def remove_ticket_panel(guild_id, message_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "DELETE FROM ticket_panels WHERE guild_id=? AND message_id=?",
            (guild_id, message_id),
        )
        await db.commit()


async def register_escalation_event(guild_id, channel_id, event_key, created_at):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO escalation_events(guild_id, channel_id, event_key, created_at) VALUES(?, ?, ?, ?)",
            (guild_id, channel_id, event_key, created_at),
        )
        await db.commit()
        return cursor.rowcount == 1


async def escalation_event_exists(guild_id, channel_id, event_key):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT 1 FROM escalation_events WHERE guild_id=? AND channel_id=? AND event_key=? LIMIT 1",
            (guild_id, channel_id, event_key),
        )
        return await cursor.fetchone() is not None


async def clear_escalation_event(guild_id, channel_id, event_key):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "DELETE FROM escalation_events WHERE guild_id=? AND channel_id=? AND event_key=?",
            (guild_id, channel_id, event_key),
        )
        await db.commit()
