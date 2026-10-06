import json
import uuid as uuid_lib
from datetime import datetime

import aiosqlite
import pytz

import config
from utils.logger import log_db


async def create_ticket_record(
    channel_id,
    guild_id,
    user_id,
    application,
    created_at,
    form_response=None,
):

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
                uuid,
                form_response,
                waiting_on,
                waiting_changed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                channel_id,
                guild_id,
                user_id,
                application,
                "open",
                created_at,
                ticket_uuid,
                json.dumps(form_response, ensure_ascii=False)
                if form_response
                else None,
                "staff",
                created_at,
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

async def get_open_ticket_for_user(guild_id, user_id, application=None):
    async with aiosqlite.connect(config.DATABASE) as db:
        if application is None:
            cursor = await db.execute(
                "SELECT channel_id, uuid, application FROM tickets WHERE guild_id=? AND user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                (int(guild_id), int(user_id)),
            )
        else:
            cursor = await db.execute(
                "SELECT channel_id, uuid, application FROM tickets WHERE guild_id=? AND user_id=? AND application=? AND status='open' ORDER BY id DESC LIMIT 1",
                (int(guild_id), int(user_id), str(application)),
            )
        row = await cursor.fetchone()
    if row is None:
        return None
    return {"channel_id": row[0], "uuid": row[1], "application": row[2]}

async def get_latest_closed_ticket_for_user_type(guild_id, user_id, application):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT channel_id, uuid, closed_at, claimed_by, control_message_id, closed_by, close_reason FROM tickets WHERE guild_id=? AND user_id=? AND application=? AND status='closed' ORDER BY closed_at DESC, id DESC LIMIT 1",
            (int(guild_id), int(user_id), str(application)),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "channel_id": row[0],
        "uuid": row[1],
        "closed_at": row[2],
        "claimed_by": row[3],
        "control_message_id": row[4],
        "closed_by": row[5],
        "close_reason": row[6],
    }

async def close_ticket(
    channel_id, closed_at, closed_by=None, close_reason=None, expected_warned_at=None
):

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
                AND (? IS NULL OR (warned_inactive=1 AND warned_at=?))
        """,
            (
                "closed",
                closed_at,
                closed_by,
                close_reason,
                channel_id,
                expected_warned_at,
                expected_warned_at,
            ),
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

async def reopen_ticket(channel_id, reopened_at=None):
    reopened_at = reopened_at or datetime.now(pytz.utc).isoformat()

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
                warned_at=NULL,
                waiting_on='staff',
                waiting_changed_at=?
            WHERE channel_id=? AND status='closed'
        """,
            ("open", reopened_at, channel_id),
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
            (datetime.now(pytz.utc).isoformat(), channel_id),
        )
        await db.commit()

async def get_ticket_owner(channel_id):

    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            """
            SELECT user_id
            FROM tickets
            WHERE channel_id=?
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

async def transfer_ticket_claim(channel_id, transferred_by, target_id, changed_at):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT status, claimed_by FROM tickets WHERE channel_id=?",
            (int(channel_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.rollback()
            return {"status": "not_found"}
        status, previous_claimed_by = row
        if status != "open":
            await db.rollback()
            return {"status": "not_open", "claimed_by": previous_claimed_by}
        if previous_claimed_by is None:
            await db.rollback()
            return {"status": "unclaimed"}
        if int(previous_claimed_by) == int(target_id):
            await db.rollback()
            return {
                "status": "same",
                "claimed_by": int(target_id),
                "previous_claimed_by": int(previous_claimed_by),
            }
        await db.execute(
            "UPDATE tickets SET claimed_by=?, claimed_at=?, claim_changed_at=? WHERE channel_id=? AND status='open'",
            (int(target_id), changed_at, changed_at, int(channel_id)),
        )
        await db.commit()
    return {
        "status": "transferred",
        "claimed_by": int(target_id),
        "previous_claimed_by": int(previous_claimed_by),
        "transferred_by": int(transferred_by),
    }

async def set_ticket_waiting_on(channel_id, waiting_on, changed_at=None):
    value = str(waiting_on).lower()
    if value not in {"user", "staff"}:
        raise ValueError("waiting_on must be user or staff")
    changed_at = changed_at or datetime.now(pytz.utc).isoformat()
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "UPDATE tickets SET waiting_on=?, waiting_changed_at=?, warned_inactive=0, warned_at=NULL WHERE channel_id=? AND status='open'",
            (value, changed_at, int(channel_id)),
        )
        await db.commit()
    return cursor.rowcount == 1

async def process_ticket_message(channel_id, author_id, staff_message, changed_at):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            "SELECT status, user_id, claimed_by, waiting_on FROM tickets WHERE channel_id=?",
            (int(channel_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.rollback()
            return {"status": "not_found"}
        status, user_id, claimed_by, waiting_on = row
        if status != "open":
            await db.rollback()
            return {"status": "not_open"}
        if int(author_id) == int(user_id):
            await db.execute(
                "UPDATE tickets SET waiting_on='staff', waiting_changed_at=?, warned_inactive=0, warned_at=NULL WHERE channel_id=? AND status='open'",
                (changed_at, int(channel_id)),
            )
            await db.commit()
            return {
                "status": "updated",
                "waiting_on": "staff",
                "owner_id": int(user_id),
                "claimed_by": claimed_by,
                "auto_claimed": False,
            }
        if not staff_message:
            await db.rollback()
            return {
                "status": "ignored",
                "waiting_on": waiting_on,
                "owner_id": int(user_id),
                "claimed_by": claimed_by,
                "auto_claimed": False,
            }
        auto_claimed = claimed_by is None
        if auto_claimed:
            await db.execute(
                "UPDATE tickets SET waiting_on='user', waiting_changed_at=?, warned_inactive=0, warned_at=NULL, claimed_by=?, claimed_at=?, claim_changed_at=? WHERE channel_id=? AND status='open'",
                (changed_at, int(author_id), changed_at, changed_at, int(channel_id)),
            )
            claimed_by = int(author_id)
        else:
            await db.execute(
                "UPDATE tickets SET waiting_on='user', waiting_changed_at=?, warned_inactive=0, warned_at=NULL WHERE channel_id=? AND status='open'",
                (changed_at, int(channel_id)),
            )
        await db.commit()
    return {
        "status": "updated",
        "waiting_on": "user",
        "owner_id": int(user_id),
        "claimed_by": claimed_by,
        "auto_claimed": auto_claimed,
    }

async def set_ticket_control_message(channel_id, message_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "UPDATE tickets SET control_message_id=? WHERE channel_id=?",
            (int(message_id), int(channel_id)),
        )
        await db.commit()

async def get_ticket_controls(after_id=0, limit=None):
    async with aiosqlite.connect(config.DATABASE) as db:
        parameters = [int(after_id)]
        query = "SELECT id, channel_id, guild_id, control_message_id, claimed_by, application, status, label, user_id, waiting_on, waiting_changed_at FROM tickets WHERE status IN ('open', 'closed') AND id>? ORDER BY id"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(max(1, int(limit)))
        cursor = await db.execute(query, tuple(parameters))
        rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "channel_id": row[1],
            "guild_id": row[2],
            "control_message_id": row[3],
            "claimed_by": row[4],
            "application": row[5],
            "status": row[6],
            "label": row[7],
            "user_id": row[8],
            "waiting_on": row[9] or "staff",
            "waiting_changed_at": row[10],
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
                label,
                form_response,
                waiting_on,
                waiting_changed_at
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
        "form_response": json.loads(row[17]) if row[17] else [],
        "waiting_on": row[18] or "staff",
        "waiting_changed_at": row[19],
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

async def set_ticket_form(guild_id, ticket_type, questions, updated_by, updated_at):
    normalized_type = " ".join(str(ticket_type).split())
    async with aiosqlite.connect(config.DATABASE) as db:
        await db.execute(
            "INSERT INTO ticket_forms(guild_id, ticket_type, questions, updated_by, updated_at) VALUES(?, ?, ?, ?, ?) ON CONFLICT(guild_id, ticket_type) DO UPDATE SET questions=excluded.questions, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (
                int(guild_id),
                normalized_type,
                json.dumps(questions, ensure_ascii=False),
                int(updated_by),
                updated_at,
            ),
        )
        await db.commit()

async def get_ticket_form(guild_id, ticket_type):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT questions, updated_by, updated_at FROM ticket_forms WHERE guild_id=? AND LOWER(ticket_type)=LOWER(?)",
            (int(guild_id), " ".join(str(ticket_type).split())),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "questions": json.loads(row[0]),
        "updated_by": row[1],
        "updated_at": row[2],
    }

async def get_ticket_forms(guild_id):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "SELECT ticket_type, questions, updated_by, updated_at FROM ticket_forms WHERE guild_id=? ORDER BY LOWER(ticket_type)",
            (int(guild_id),),
        )
        rows = await cursor.fetchall()
    return [
        {
            "ticket_type": row[0],
            "questions": json.loads(row[1]),
            "updated_by": row[2],
            "updated_at": row[3],
        }
        for row in rows
    ]

async def delete_ticket_form(guild_id, ticket_type):
    async with aiosqlite.connect(config.DATABASE) as db:
        cursor = await db.execute(
            "DELETE FROM ticket_forms WHERE guild_id=? AND LOWER(ticket_type)=LOWER(?)",
            (int(guild_id), " ".join(str(ticket_type).split())),
        )
        await db.commit()
    return cursor.rowcount == 1

