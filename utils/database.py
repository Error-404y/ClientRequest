import os 
import uuid as uuid_lib 
from datetime import datetime 

import aiosqlite 
import pytz 

import config 
from utils .logger import log_db 


async def setup_database ():

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

    
    
    

        await db .execute ("""
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

        cursor =await db .execute (
        "PRAGMA table_info(tickets)"
        )

        columns =[
        row [1 ]
        for row in await cursor .fetchall ()
        ]

        if "claimed_by"not in columns :

            await db .execute ("""
                ALTER TABLE tickets
                ADD COLUMN claimed_by INTEGER DEFAULT NULL
            """)

        if "close_reason"not in columns :

            await db .execute ("""
                ALTER TABLE tickets
                ADD COLUMN close_reason TEXT DEFAULT NULL
            """)

        if "warned_inactive"not in columns :

            await db .execute ("""
                ALTER TABLE tickets
                ADD COLUMN warned_inactive INTEGER DEFAULT 0
            """)

        if "warned_at" not in columns:
            await db.execute("ALTER TABLE tickets ADD COLUMN warned_at TEXT DEFAULT NULL")

        if "priority"not in columns :

            await db .execute ("""
                ALTER TABLE tickets
                ADD COLUMN priority TEXT DEFAULT 'Medium'
            """)

        if "closed_by"not in columns :

            await db .execute ("""
                ALTER TABLE tickets
                ADD COLUMN closed_by INTEGER DEFAULT NULL
            """)

        if "claimed_at"not in columns :

            await db .execute ("""
                ALTER TABLE tickets
                ADD COLUMN claimed_at TEXT DEFAULT NULL
            """)

        if "uuid"not in columns :

            await db .execute ("""
                ALTER TABLE tickets
                ADD COLUMN uuid TEXT DEFAULT NULL
            """)

            
            
            

        cursor =await db .execute ("""
            SELECT id
            FROM tickets
            WHERE uuid IS NULL
               OR TRIM(uuid) = ''
        """)

        old_ticket_rows =await cursor .fetchall ()

        repaired_ticket_count =0 

        for (row_id ,)in old_ticket_rows :

            new_uuid =str (
            uuid_lib .uuid4 ()
            )

            await db .execute (
            """
                UPDATE tickets
                SET uuid=?
                WHERE id=?
                """,
            (
            new_uuid ,
            row_id 
            )
            )

            repaired_ticket_count +=1 

            
            
            

        await db .execute ("""
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

        cursor =await db .execute (
        "PRAGMA table_info(infractions)"
        )

        inf_columns =[
        row [1 ]
        for row in await cursor .fetchall ()
        ]

        if "guild_id"not in inf_columns :

            await db .execute ("""
                ALTER TABLE infractions
                ADD COLUMN guild_id INTEGER DEFAULT NULL
            """)

        if "uuid"not in inf_columns :

            await db .execute ("""
                ALTER TABLE infractions
                ADD COLUMN uuid TEXT DEFAULT NULL
            """)

            
            
            

        cursor =await db .execute ("""
            SELECT id
            FROM infractions
            WHERE uuid IS NULL
               OR TRIM(uuid) = ''
        """)

        old_infraction_rows =await cursor .fetchall ()

        repaired_infraction_count =0 

        for (row_id ,)in old_infraction_rows :

            new_uuid =generate_infraction_uuid ()

            await db .execute (
            """
                UPDATE infractions
                SET uuid=?
                WHERE id=?
                """,
            (
            new_uuid ,
            row_id 
            )
            )

            repaired_infraction_count +=1 

            
            
            

        cursor =await db .execute (
        "PRAGMA table_info(user_stats)"
        )

        stats_columns =[
        row [1 ]
        for row in await cursor .fetchall ()
        ]

        if not stats_columns :

            await db .execute ("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    guild_id INTEGER,
                    user_id INTEGER,
                    message_count INTEGER DEFAULT 0,
                    bad_word_count INTEGER DEFAULT 0,
                    last_active TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)

        elif "guild_id"not in stats_columns :

            await db .execute ("""
                ALTER TABLE user_stats
                RENAME TO old_user_stats
            """)

            await db .execute ("""
                CREATE TABLE user_stats (
                    guild_id INTEGER,
                    user_id INTEGER,
                    message_count INTEGER DEFAULT 0,
                    bad_word_count INTEGER DEFAULT 0,
                    last_active TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)

            await db .execute ("""
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
            """,(
            config .GUILD_ID ,
            ))

            await db .execute (
            "DROP TABLE old_user_stats"
            )

        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_channel_id ON tickets(channel_id)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_uuid ON tickets(uuid)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets(guild_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_guild_user_status ON tickets(guild_id, user_id, status)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_infractions_uuid ON infractions(uuid)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_infractions_guild_user ON infractions(guild_id, user_id)")
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_staff_availability_status ON staff_availability(guild_id, status)")
        for guild_id in config.GUILDS:
            cursor = await db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM tickets WHERE guild_id=?", (guild_id,))
            suggested = (await cursor.fetchone())[0]
            await db.execute(
                "INSERT INTO ticket_counters(guild_id, next_number) VALUES(?, ?) ON CONFLICT(guild_id) DO UPDATE SET next_number=MAX(ticket_counters.next_number, excluded.next_number)",
                (guild_id, suggested),
            )
        await db .commit ()

    log_db (
    "INITIALIZE",
    "database",
    (
    "Verified tables, schema columns, "
    f"repaired {repaired_ticket_count } ticket UUID(s), "
    f"repaired {repaired_infraction_count } infraction UUID(s)"
    )
    )


    
    
    

def generate_infraction_uuid (
guild_id =None 
):

    prefix =(
    f"G{guild_id }-"
    if guild_id 
    else 
    "GLOBAL-"
    )

    return (
    f"{prefix }"
    f"{uuid_lib .uuid4 ()}"
    )


    
    
    

async def add_infraction (
user_id :int ,
moderator_id :int ,
action_type :str ,
reason :str ,
guild_id :int =None ,
custom_uuid :str =None 
)->str :

    tz =pytz .timezone (
    "Europe/Berlin"
    )

    now_str =datetime .now (
    tz 
    ).strftime (
    "%d/%m/%Y - %H:%M"
    )

    infraction_uuid =(
    str (custom_uuid ).strip ()
    if custom_uuid 
    else 
    generate_infraction_uuid (
    guild_id 
    )
    )

    if not infraction_uuid :

        raise RuntimeError (
        "Failed to generate an infraction UUID."
        )

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        if not custom_uuid :

            while True :

                cursor =await db .execute (
                """
                    SELECT 1
                    FROM infractions
                    WHERE uuid=?
                    """,
                (
                infraction_uuid ,
                )
                )

                exists =await cursor .fetchone ()

                if not exists :
                    break 

                infraction_uuid =(
                generate_infraction_uuid (
                guild_id 
                )
                )

        await db .execute ("""
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
        """,(
        infraction_uuid ,
        guild_id ,
        user_id ,
        moderator_id ,
        action_type ,
        reason ,
        now_str 
        ))

        await db .commit ()

    log_db (
    "INSERT",
    "infractions",
    (
    f"UUID: {infraction_uuid }, "
    f"Guild: {guild_id }, "
    f"User: {user_id }, "
    f"Mod: {moderator_id }, "
    f"Type: {action_type }, "
    f"Reason: {reason }"
    )
    )

    monitor_dir =os .path .join (
    os .path .dirname (
    os .path .dirname (__file__ )
    ),
    "MonitorUUID"
    )

    os .makedirs (
    monitor_dir ,
    exist_ok =True 
    )

    log_content =(
    f"Event UUID: {infraction_uuid }\n"
    f"Action: {action_type }\n"
    f"Timestamp: {now_str }\n"
    f"User ID: {user_id }\n"
    f"Moderator ID: {moderator_id }\n"
    f"Reason: {reason }\n"
    f"Guild ID: {guild_id }\n"
    )

    file_path =os .path .join (
    monitor_dir ,
    f"{infraction_uuid }.txt"
    )

    with open (
    file_path ,
    "w",
    encoding ="utf-8"
    )as file :

        file .write (
        log_content 
        )

    return infraction_uuid 


async def get_user_infractions (
user_id :int ,
guild_id :int =None 
):

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        if guild_id is not None :

            cursor =await db .execute ("""
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
            """,(
            user_id ,
            guild_id 
            ))

        else :

            cursor =await db .execute ("""
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
            """,(
            user_id ,
            ))

        rows =await cursor .fetchall ()

    return [
    {
    "id":row [0 ],
    "user_id":row [1 ],
    "moderator_id":row [2 ],
    "action_type":row [3 ],
    "reason":row [4 ],
    "timestamp":row [5 ],
    "guild_id":row [6 ],
    "uuid":row [7 ]
    }
    for row in rows 
    ]


async def get_infraction_by_uuid (
uuid_str :str 
):

    if not uuid_str :
        return None 

    uuid_str =(
    str (uuid_str )
    .strip ()
    .strip ("`")
    .strip ()
    )

    if not uuid_str :
        return None 

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        cursor =await db .execute ("""
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
                uuid=?
                OR uuid LIKE ?
                OR uuid LIKE ?
            ORDER BY id DESC
            LIMIT 1
        """,(
        uuid_str ,
        f"{uuid_str }%",
        f"%{uuid_str }"
        ))

        row =await cursor .fetchone ()

    if not row :
        return None 

    return {
    "id":row [0 ],
    "user_id":row [1 ],
    "moderator_id":row [2 ],
    "action_type":row [3 ],
    "reason":row [4 ],
    "timestamp":row [5 ],
    "guild_id":row [6 ],
    "uuid":row [7 ]
    }


    
    
    

async def get_ticket_by_uuid (
uuid_str :str 
):

    if not uuid_str :
        return None 

    uuid_str =(
    str (uuid_str )
    .strip ()
    .strip ("`")
    .strip ()
    )

    if not uuid_str :
        return None 

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        cursor =await db .execute ("""
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
                uuid
            FROM tickets
            WHERE
                uuid=?
                OR uuid LIKE ?
                OR uuid LIKE ?
            ORDER BY id DESC
            LIMIT 1
        """,(
        uuid_str ,
        f"{uuid_str }%",
        f"%{uuid_str }"
        ))

        row =await cursor .fetchone ()

    if not row :
        return None 

    return {
    "id":row [0 ],
    "channel_id":row [1 ],
    "guild_id":row [2 ],
    "user_id":row [3 ],
    "application":row [4 ],
    "status":row [5 ],
    "created_at":row [6 ],
    "closed_at":row [7 ],
    "claimed_by":row [8 ],
    "close_reason":row [9 ],
    "priority":row [10 ],
    "claimed_at":row [11 ],
    "closed_by":row [12 ],
    "warned_inactive":row [13 ],
    "uuid":row [14 ]
    }


    
    
    

async def remove_user_warning (
user_id :int ,
warn_id =None ,
guild_id :int =None 
):

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        if warn_id is not None :

            warn_str =(
            str (warn_id )
            .strip ()
            .strip ("`")
            .strip ()
            )

            numeric_id =(
            int (warn_str )
            if warn_str .isdigit ()
            else -1 
            )

            if guild_id is not None :

                cursor =await db .execute ("""
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
                            OR uuid LIKE ?
                            OR uuid LIKE ?
                            OR id=?
                        )
                        AND guild_id=?
                        AND action_type='WARN'
                    LIMIT 1
                """,(
                warn_str ,
                f"{warn_str }%",
                f"%{warn_str }",
                numeric_id ,
                guild_id 
                ))

            else :

                cursor =await db .execute ("""
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
                            OR uuid LIKE ?
                            OR uuid LIKE ?
                            OR id=?
                        )
                        AND action_type='WARN'
                    LIMIT 1
                """,(
                warn_str ,
                f"{warn_str }%",
                f"%{warn_str }",
                numeric_id 
                ))

            row =await cursor .fetchone ()

            if not row :
                return 0 ,[]

            infraction_id =row [0 ]

            await db .execute (
            """
                DELETE FROM infractions
                WHERE id=?
                """,
            (
            infraction_id ,
            )
            )

            await db .commit ()

            log_db (
            "DELETE",
            "infractions",
            (
            f"Removed warning #{infraction_id } "
            f"(UUID: {row [3 ]}) "
            f"for User ID {row [4 ]}"
            )
            )

            return 1 ,[{
            "id":row [0 ],
            "reason":row [1 ],
            "timestamp":row [2 ],
            "uuid":row [3 ],
            "user_id":row [4 ]
            }]

        if guild_id is not None :

            cursor =await db .execute ("""
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
            """,(
            user_id ,
            guild_id 
            ))

        else :

            cursor =await db .execute ("""
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
            """,(
            user_id ,
            ))

        rows =await cursor .fetchall ()

        if not rows :
            return 0 ,[]

        ids =[
        row [0 ]
        for row in rows 
        ]

        placeholders =",".join (
        "?"
        for _ in ids 
        )

        await db .execute (
        f"""
            DELETE FROM infractions
            WHERE id IN ({placeholders })
            """,
        ids 
        )

        await db .commit ()

        log_db (
        "DELETE",
        "infractions",
        (
        f"Cleared {len (rows )} warnings "
        f"for User ID {user_id }"
        )
        )

        return len (rows ),[
        {
        "id":row [0 ],
        "reason":row [1 ],
        "timestamp":row [2 ],
        "uuid":row [3 ],
        "user_id":row [4 ]
        }
        for row in rows 
        ]


        
        
        

async def remove_infraction_by_uuid (
uuid_str :str 
):

    if not uuid_str :
        return None 

    uuid_str =(
    str (uuid_str )
    .strip ()
    .strip ("`")
    .strip ()
    )

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        cursor =await db .execute ("""
            SELECT
                id,
                user_id,
                action_type,
                reason,
                timestamp,
                uuid
            FROM infractions
            WHERE
                uuid=?
                OR uuid LIKE ?
                OR uuid LIKE ?
            ORDER BY id DESC
            LIMIT 1
        """,(
        uuid_str ,
        f"{uuid_str }%",
        f"%{uuid_str }"
        ))

        row =await cursor .fetchone ()

        if not row :
            return None 

        await db .execute (
        """
            DELETE FROM infractions
            WHERE id=?
            """,
        (
        row [0 ],
        )
        )

        await db .commit ()

    log_db (
    "DELETE",
    "infractions",
    (
    f"Removed infraction UUID: {row [5 ]} "
    f"({row [2 ]}) for User ID {row [1 ]}"
    )
    )

    return {
    "id":row [0 ],
    "user_id":row [1 ],
    "action_type":row [2 ],
    "reason":row [3 ],
    "timestamp":row [4 ],
    "uuid":row [5 ]
    }


    
    
    

async def increment_user_activity (
user_id :int ,
guild_id :int =None ,
has_bad_word :bool =False 
):

    tz =pytz .timezone (
    "Europe/Berlin"
    )

    now_str =datetime .now (
    tz 
    ).strftime (
    "%d/%m/%Y - %H:%M"
    )

    gid =(
    guild_id 
    or config .GUILD_ID 
    )

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        bad_inc =(
        1 
        if has_bad_word 
        else 
        0 
        )

        await db .execute ("""
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
        """,(
        gid ,
        user_id ,
        bad_inc ,
        now_str 
        ))

        await db .commit ()

        if has_bad_word :

            log_db (
            "UPSERT",
            "user_stats",
            (
            f"Guild: {gid }, "
            f"User: {user_id }, "
            f"Bad Word: True"
            )
            )


async def get_user_stats (
user_id :int ,
guild_id :int =None 
):

    gid =(
    guild_id 
    or config .GUILD_ID 
    )

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        cursor =await db .execute ("""
            SELECT
                user_id,
                message_count,
                bad_word_count,
                last_active
            FROM user_stats
            WHERE user_id=? AND guild_id=?
        """,(
        user_id ,
        gid 
        ))

        row =await cursor .fetchone ()

    if row :

        return {
        "user_id":row [0 ],
        "message_count":row [1 ],
        "bad_word_count":row [2 ],
        "last_active":row [3 ]
        }

    return {
    "user_id":user_id ,
    "message_count":0 ,
    "bad_word_count":0 ,
    "last_active":"Never"
    }


    
    
    

async def create_ticket_record (
channel_id ,
guild_id ,
user_id ,
application ,
created_at 
):







    ticket_uuid =str (
    uuid_lib .uuid4 ()
    ).strip ()

    if not ticket_uuid :

        raise RuntimeError (
        "Failed to generate ticket UUID."
        )

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

    
    
    

        while True :

            cursor =await db .execute (
            """
                SELECT 1
                FROM tickets
                WHERE uuid=?
                LIMIT 1
                """,
            (
            ticket_uuid ,
            )
            )

            exists =await cursor .fetchone ()

            if not exists :
                break 

            ticket_uuid =str (
            uuid_lib .uuid4 ()
            ).strip ()

            
            
            

        await db .execute ("""
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
        """,(
        channel_id ,
        guild_id ,
        user_id ,
        application ,
        "open",
        created_at ,
        ticket_uuid 
        ))

        await db .commit ()

    log_db (
    "INSERT",
    "tickets",
    (
    f"Created record for Channel: {channel_id }, "
    f"User: {user_id }, "
    f"App: {application }, "
    f"UUID: {ticket_uuid }"
    )
    )

    return ticket_uuid 


    
    
    

async def close_ticket (
channel_id ,
closed_at ,
closed_by =None ,
close_reason =None 
):

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        cursor = await db .execute ("""
            UPDATE tickets
            SET
                status=?,
                closed_at=?,
                closed_by=?,
                close_reason=?
            WHERE channel_id=? AND status='open'
        """,(
        "closed",
        closed_at ,
        closed_by ,
        close_reason ,
        channel_id 
        ))

        await db .commit ()

        closed = cursor.rowcount == 1

    log_db (
    "UPDATE",
    "tickets",
    (
    f"{'Closed' if closed else 'Close skipped for'} Channel: {channel_id }, "
    f"Closed By: {closed_by }, "
    f"Reason: {close_reason }"
    )
    )

    return closed


async def reopen_ticket (
channel_id 
):

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        await db .execute ("""
            UPDATE tickets
            SET
                status=?,
                closed_at=NULL,
                closed_by=NULL,
                close_reason=NULL,
                warned_inactive=0,
                warned_at=NULL
            WHERE channel_id=?
        """,(
        "open",
        channel_id 
        ))

        await db .commit ()

    log_db (
    "UPDATE",
    "tickets",
    f"Reopened Channel: {channel_id }"
    )


async def mark_ticket_deleted(channel_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "UPDATE tickets SET status='deleted', closed_at=COALESCE(closed_at, ?) WHERE channel_id=?",
            (datetime.now(pytz.timezone("Europe/Berlin")).isoformat(), channel_id),
        )
        await db.commit()


async def get_ticket_owner (
channel_id 
):

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        cursor =await db .execute ("""
            SELECT user_id
            FROM tickets
            WHERE channel_id=?
        """,(
        channel_id ,
        ))

        result =await cursor .fetchone ()

    return (
    result [0 ]
    if result 
    else 
    None 
    )


async def get_next_ticket_number(guild_id):
    config.get_guild_config(guild_id)
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT next_number FROM ticket_counters WHERE guild_id=?", (guild_id,))
        row = await cursor.fetchone()
        if row is None:
            cursor = await db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM tickets WHERE guild_id=?", (guild_id,))
            number = (await cursor.fetchone())[0]
            await db.execute("INSERT INTO ticket_counters(guild_id, next_number) VALUES(?, ?)", (guild_id, number + 1))
        else:
            number = row[0]
            await db.execute("UPDATE ticket_counters SET next_number=? WHERE guild_id=?", (number + 1, guild_id))
        await db.commit()
    return number


async def claim_ticket (
channel_id ,
user_id ,
claimed_at 
):

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        cursor = await db .execute ("""
            UPDATE tickets
            SET
                claimed_by=?,
                claimed_at=?
            WHERE channel_id=? AND status='open' AND claimed_by IS NULL
        """,(
        user_id ,
        claimed_at ,
        channel_id 
        ))

        await db .commit ()

        claimed = cursor.rowcount == 1

    log_db (
    "UPDATE",
    "tickets",
    (
    f"{'Claimed' if claimed else 'Claim skipped for'} Channel: {channel_id } "
    f"by User: {user_id }"
    )
    )

    return claimed


async def get_ticket_record (
channel_id 
):

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        cursor =await db .execute ("""
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
                uuid
            FROM tickets
            WHERE channel_id=?
        """,(
        channel_id ,
        ))

        row =await cursor .fetchone ()

    if not row :
        return None 

    return {
    "id":row [0 ],
    "channel_id":row [1 ],
    "guild_id":row [2 ],
    "user_id":row [3 ],
    "application":row [4 ],
    "status":row [5 ],
    "created_at":row [6 ],
    "closed_at":row [7 ],
    "claimed_by":row [8 ],
    "close_reason":row [9 ],
    "priority":row [10 ],
    "claimed_at":row [11 ],
    "closed_by":row [12 ],
    "warned_inactive":row [13 ],
    "uuid":row [14 ]
    }


async def set_ticket_priority (
channel_id ,
priority 
):

    async with aiosqlite .connect (
    config .DATABASE 
    )as db :

        await db .execute ("""
            UPDATE tickets
            SET priority=?
            WHERE channel_id=?
        """,(
        priority ,
        channel_id 
        ))

        await db .commit ()

    log_db (
    "UPDATE",
    "tickets",
    (
    f"Priority set to '{priority }' "
    f"for Channel: {channel_id }"
    )
    )


async def set_staff_availability(guild_id, user_id, status, updated_at):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "INSERT INTO staff_availability(guild_id, user_id, status, updated_at) VALUES(?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
            (guild_id, user_id, status, updated_at),
        )
        await db.commit()
    log_db("UPSERT", "staff_availability", f"Guild: {guild_id}, User: {user_id}, Status: {status}")


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
        await db.execute("DELETE FROM ticket_panels WHERE guild_id=? AND message_id=?", (guild_id, message_id))
        await db.commit()


async def register_escalation_event(guild_id, channel_id, event_key, created_at):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO escalation_events(guild_id, channel_id, event_key, created_at) VALUES(?, ?, ?, ?)",
            (guild_id, channel_id, event_key, created_at),
        )
        await db.commit()
        return cursor.rowcount == 1


async def clear_escalation_event(guild_id, channel_id, event_key):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "DELETE FROM escalation_events WHERE guild_id=? AND channel_id=? AND event_key=?",
            (guild_id, channel_id, event_key),
        )
        await db.commit()
