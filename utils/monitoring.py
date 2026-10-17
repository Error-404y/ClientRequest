from datetime import datetime, timedelta

import aiosqlite
import pytz

import config


async def collect_database_metrics(guild_id=None):
    now = datetime.now(pytz.utc)
    cutoff_1h = (now - timedelta(hours=1)).isoformat()
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    scope = "" if guild_id is None else " AND guild_id=?"
    scope_params = () if guild_id is None else (int(guild_id),)
    async with aiosqlite.connect(config.DATABASE) as database:
        started = datetime.now(pytz.utc)
        await database.execute("SELECT 1")
        db_latency_ms = (datetime.now(pytz.utc) - started).total_seconds() * 1000

        open_cursor = await database.execute(
            f"SELECT COUNT(*) FROM tickets WHERE status='open'{scope}",
            scope_params,
        )
        open_tickets = (await open_cursor.fetchone())[0]

        created_cursor = await database.execute(
            f"SELECT COUNT(*) FROM tickets WHERE datetime(created_at)>=datetime(?){scope}",
            (cutoff_24h, *scope_params),
        )
        tickets_created_24h = (await created_cursor.fetchone())[0]

        closed_cursor = await database.execute(
            f"SELECT COUNT(*) FROM tickets WHERE closed_at IS NOT NULL AND datetime(closed_at)>=datetime(?){scope}",
            (cutoff_24h, *scope_params),
        )
        tickets_closed_24h = (await closed_cursor.fetchone())[0]

        errors_1h_cursor = await database.execute(
            f"SELECT COALESCE(SUM(occurrence_count), 0), COUNT(*) FROM error_events WHERE last_seen>=?{scope}",
            (cutoff_1h, *scope_params),
        )
        errors_1h, unique_errors_1h = await errors_1h_cursor.fetchone()

        errors_24h_cursor = await database.execute(
            f"SELECT COALESCE(SUM(occurrence_count), 0), COUNT(*) FROM error_events WHERE last_seen>=?{scope}",
            (cutoff_24h, *scope_params),
        )
        errors_24h, unique_errors_24h = await errors_24h_cursor.fetchone()

        latest_error_cursor = await database.execute(
            f"SELECT reference, category, error_type, occurrence_count, last_seen FROM error_events WHERE 1=1{scope} ORDER BY last_seen DESC LIMIT 1",
            scope_params,
        )
        latest_error = await latest_error_cursor.fetchone()

        slow_cursor = await database.execute(
            f"SELECT COUNT(*), COALESCE(MAX(duration_ms), 0) FROM performance_events WHERE created_at>=?{scope}",
            (cutoff_24h, *scope_params),
        )
        slow_operations_24h, slowest_ms_24h = await slow_cursor.fetchone()

    return {
        "db_latency_ms": db_latency_ms,
        "open_tickets": open_tickets,
        "tickets_created_24h": tickets_created_24h,
        "tickets_closed_24h": tickets_closed_24h,
        "errors_1h": errors_1h,
        "errors_24h": errors_24h,
        "unique_errors_1h": unique_errors_1h,
        "unique_errors_24h": unique_errors_24h,
        "latest_error": {
            "reference": latest_error[0],
            "category": latest_error[1],
            "error_type": latest_error[2],
            "occurrence_count": latest_error[3],
            "last_seen": latest_error[4],
        }
        if latest_error
        else None,
        "slow_operations_24h": slow_operations_24h,
        "slowest_ms_24h": slowest_ms_24h,
    }


async def persist_monitoring_sample(snapshot):
    now = datetime.now(pytz.utc).isoformat()
    async with aiosqlite.connect(config.DATABASE) as database:
        await database.execute(
            """
            INSERT INTO monitoring_samples(
                created_at,
                gateway_latency_ms,
                db_latency_ms,
                event_loop_lag_ms,
                process_cpu_percent,
                process_memory_mb,
                guild_count,
                member_count,
                open_tickets,
                errors_1h,
                errors_24h,
                slow_operations_24h,
                commands_1h,
                interactions_1h,
                command_failures_1h,
                dm_failures_24h
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                snapshot["gateway_latency_ms"],
                snapshot["db_latency_ms"],
                snapshot["event_loop_lag_ms"],
                snapshot["process_cpu_percent"],
                snapshot["process_memory_mb"],
                snapshot["guild_count"],
                snapshot["member_count"],
                snapshot["open_tickets"],
                snapshot["errors_1h"],
                snapshot["errors_24h"],
                snapshot["slow_operations_24h"],
                snapshot["commands_1h"],
                snapshot["interactions_1h"],
                snapshot["command_failures_1h"],
                snapshot["dm_failures_24h"],
            ),
        )
        cutoff = (
            datetime.now(pytz.utc) - timedelta(days=config.MONITOR_RETENTION_DAYS)
        ).isoformat()
        await database.execute(
            "DELETE FROM monitoring_samples WHERE created_at<?",
            (cutoff,),
        )
        await database.commit()


async def monitoring_history(hours=24):
    cutoff = (
        datetime.now(pytz.utc) - timedelta(hours=max(1, int(hours)))
    ).isoformat()
    async with aiosqlite.connect(config.DATABASE) as database:
        cursor = await database.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(AVG(gateway_latency_ms), 0),
                COALESCE(MAX(gateway_latency_ms), 0),
                COALESCE(AVG(db_latency_ms), 0),
                COALESCE(MAX(db_latency_ms), 0),
                COALESCE(AVG(event_loop_lag_ms), 0),
                COALESCE(MAX(event_loop_lag_ms), 0),
                COALESCE(AVG(process_cpu_percent), 0),
                COALESCE(MAX(process_cpu_percent), 0),
                COALESCE(AVG(process_memory_mb), 0),
                COALESCE(MAX(process_memory_mb), 0)
            FROM monitoring_samples
            WHERE created_at>=?
            """,
            (cutoff,),
        )
        row = await cursor.fetchone()
    return {
        "samples": row[0],
        "gateway_avg_ms": row[1],
        "gateway_max_ms": row[2],
        "db_avg_ms": row[3],
        "db_max_ms": row[4],
        "event_loop_avg_ms": row[5],
        "event_loop_max_ms": row[6],
        "cpu_avg_percent": row[7],
        "cpu_max_percent": row[8],
        "memory_avg_mb": row[9],
        "memory_max_mb": row[10],
    }
