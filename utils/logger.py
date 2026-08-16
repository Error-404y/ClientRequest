import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import traceback
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytz

import config

timezone = pytz.timezone("Europe/Berlin")
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5
SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(discord_token|token|authorization|password|secret)(\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"https://(?:discord|discordapp)\.com/api/webhooks/[^\s]+", re.IGNORECASE),
)
COLORS = {
    "DEBUG": "\033[90m",
    "INFO": "\033[36m",
    "SUCCESS": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
    "RESET": "\033[0m",
}
_initialized = False


def redact(value):
    text = str(value)
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub(r"\1\2[REDACTED]", text) if pattern.groups >= 3 else pattern.sub("[REDACTED]", text)
    token = getattr(config, "TOKEN", "")
    return text.replace(token, "[REDACTED]") if token else text


def _identity(value):
    if value is None:
        return None
    return value if isinstance(value, int) else getattr(value, "id", None)


def _guild(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value
    direct = getattr(value, "id", None) if hasattr(value, "channels") else None
    related = getattr(getattr(value, "guild", None), "id", None)
    return direct or related


class ReadableFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created, timezone).strftime("%Y-%m-%d %H:%M:%S")
        level = getattr(record, "display_level", record.levelname)
        category = getattr(record, "category", "SYSTEM")
        context = []
        reference = getattr(record, "reference", None)
        if reference:
            context.append(reference)
        for label in ("guild_id", "channel_id", "user_id"):
            value = getattr(record, label, None)
            if value:
                context.append(f"{label.removesuffix('_id')}={value}")
        suffix = f" | {' | '.join(context)}" if context else ""
        return f"{timestamp} | {level:<8} | {category:<12} | {record.getMessage()}{suffix}"


class ConsoleFormatter(ReadableFormatter):
    def format(self, record):
        rendered = super().format(record)
        level = getattr(record, "display_level", record.levelname)
        return f"{COLORS.get(level, '')}{rendered}{COLORS['RESET']}" if os.isatty(1) else rendered


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone).isoformat(),
            "level": getattr(record, "display_level", record.levelname),
            "category": getattr(record, "category", "SYSTEM"),
            "message": record.getMessage(),
            "reference": getattr(record, "reference", None),
            "guild_id": getattr(record, "guild_id", None),
            "channel_id": getattr(record, "channel_id", None),
            "user_id": getattr(record, "user_id", None),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _file_handler(filename, formatter):
    Path(config.LOG_FOLDER).mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(Path(config.LOG_FOLDER) / filename, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    handler.setFormatter(formatter)
    return handler


def setup_logs():
    global _initialized
    if _initialized:
        return
    logger = logging.getLogger("maja")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(ConsoleFormatter())
    readable_file = _file_handler("bot.log", ReadableFormatter())
    readable_file.setLevel(logging.INFO)
    structured_file = _file_handler("structured.jsonl", JsonFormatter())
    structured_file.setLevel(logging.DEBUG)
    logger.addHandler(console)
    logger.addHandler(readable_file)
    logger.addHandler(structured_file)
    _initialized = True


def create_error_reference():
    return f"ERR-{uuid.uuid4().hex[:8].upper()}"


def create_error_fingerprint(category, error, context=None):
    frames = traceback.extract_tb(error.__traceback__)
    location = "unknown"
    if frames:
        frame = frames[-1]
        location = f"{Path(frame.filename).name}:{frame.name}"
    raw = f"{category.upper()}|{type(error).__name__}|{context or ''}|{location}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _store_error(reference, fingerprint, category, error, trace, context, guild_id, channel_id, user_id):
    timestamp = datetime.now(timezone).isoformat()
    try:
        with sqlite3.connect(config.DATABASE, timeout=2) as database:
            row = database.execute("SELECT reference FROM error_events WHERE fingerprint=?", (fingerprint,)).fetchone()
            if row:
                database.execute(
                    "UPDATE error_events SET occurrence_count=occurrence_count+1, last_seen=?, message=?, traceback=?, context=?, guild_id=COALESCE(?, guild_id), channel_id=COALESCE(?, channel_id), user_id=COALESCE(?, user_id) WHERE fingerprint=?",
                    (timestamp, redact(error), redact(trace), redact(context or ""), guild_id, channel_id, user_id, fingerprint),
                )
                return row[0]
            database.execute(
                "INSERT INTO error_events(reference, fingerprint, category, error_type, message, traceback, context, guild_id, channel_id, user_id, occurrence_count, first_seen, last_seen) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (reference, fingerprint, category.upper(), type(error).__name__, redact(error), redact(trace), redact(context or ""), guild_id, channel_id, user_id, timestamp, timestamp),
            )
    except (sqlite3.Error, OSError):
        return reference
    return reference


def emit(level, category, message, guild=None, channel=None, user=None, reference=None):
    setup_logs()
    normalized = level.upper()
    python_level = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "SUCCESS": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }.get(normalized, logging.INFO)
    extra = {
        "display_level": normalized,
        "category": category.upper(),
        "reference": reference,
        "guild_id": _guild(guild) or _guild(channel) or _guild(user),
        "channel_id": _identity(channel),
        "user_id": _identity(user),
    }
    logger = logging.getLogger("maja")
    record = logger.makeRecord("maja", python_level, "", 0, redact(message).replace("\n", " "), (), None, extra=extra)
    logger.handle(record)
    if normalized in {"ERROR", "CRITICAL"}:
        handler = _file_handler("errors.log", ReadableFormatter())
        handler.handle(record)
        handler.close()


def log_exception(category, error, guild=None, channel=None, user=None, context=None):
    trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    fingerprint = create_error_fingerprint(category, error, context)
    reference = _store_error(
        create_error_reference(), fingerprint, category, error, trace, context,
        _guild(guild) or _guild(channel) or _guild(user), _identity(channel), _identity(user),
    )
    summary = f"{context + ' | ' if context else ''}{type(error).__name__}: {error}"
    emit("ERROR", category, summary, guild=guild, channel=channel, user=user, reference=reference)
    try:
        handler = _file_handler("tracebacks.log", logging.Formatter("%(message)s"))
        record = logging.LogRecord("maja.traceback", logging.ERROR, "", 0, f"{datetime.now(timezone).isoformat()} | {reference}\n{redact(trace)}", (), None)
        handler.handle(record)
        handler.close()
    except OSError:
        pass
    return reference


def log_performance(operation, started_at, threshold_ms=500, guild=None):
    duration = (time.perf_counter() - started_at) * 1000
    if duration < threshold_ms:
        return duration
    try:
        with sqlite3.connect(config.DATABASE, timeout=2) as database:
            database.execute(
                "INSERT INTO performance_events(operation, duration_ms, threshold_ms, guild_id, created_at) VALUES(?, ?, ?, ?, ?)",
                (operation, duration, threshold_ms, _guild(guild), datetime.now(timezone).isoformat()),
            )
    except (sqlite3.Error, OSError):
        pass
    emit("WARNING", "PERFORMANCE", f"Slow operation | {operation} | {duration:.1f} ms | limit {threshold_ms:.1f} ms", guild=guild)
    return duration


def get_time():
    return datetime.now(timezone).strftime("%d.%m.%Y %H:%M:%S")


def format_user(user):
    if user is None:
        return "Unknown User"
    if isinstance(user, int):
        return f"User {user}"
    name = getattr(user, "display_name", getattr(user, "name", str(user)))
    return f"{name} ({getattr(user, 'id', 'Unknown')})"


def format_channel(channel):
    if channel is None:
        return "Unknown Channel"
    if isinstance(channel, str):
        return channel
    return f"#{getattr(channel, 'name', 'unknown')} ({getattr(channel, 'id', 'Unknown')})"


def log(message, guild=None):
    text = str(message)
    match = re.match(r"\[(?:DEBUG|INFO|WARNING|ERROR)/([^]]+)]\s*(.*)", text, re.DOTALL)
    category, text = match.groups() if match else ("SYSTEM", text)
    upper = text.upper()
    if "FAILED" in upper or "ERROR" in upper:
        level = "ERROR"
    elif "WARNING" in upper or "MISSING" in upper:
        level = "WARNING"
    elif any(value in upper for value in ("SUCCESS", "LOADED", "ONLINE", "COMPLETED", "CONNECTED")):
        level = "SUCCESS"
    else:
        level = "INFO"
    emit(level, category, text, guild=guild)


def log_debug(category, message, guild=None):
    emit("DEBUG", category, message, guild=guild)


def log_dm(recipient, subject, success=True, error_detail=None):
    emit("SUCCESS" if success else "WARNING", "DM", f"{subject} | {format_user(recipient)}{f' | {error_detail}' if error_detail else ''}", user=recipient)


def log_mod(action, moderator, target, reason=None, extra=None):
    details = [action, f"moderator={format_user(moderator)}", f"target={format_user(target)}"]
    if reason:
        details.append(f"reason={reason}")
    if extra:
        details.append(str(extra))
    emit("INFO", "MODERATION", " | ".join(details), user=moderator)


def log_command(user, command_name, channel=None, details=None):
    emit("INFO", "COMMAND", f"{format_user(user)} used {command_name}{f' | {details}' if details else ''}", channel=channel, user=user)


def log_ticket(action, channel, user=None, details=None):
    level = "ERROR" if "fail" in action.lower() else "INFO"
    emit(level, "TICKET", f"{action} | {format_channel(channel)}{f' | {details}' if details else ''}", channel=channel if not isinstance(channel, str) else None, user=user)


def log_interaction(user, custom_id, channel=None, details=None):
    emit("INFO", "INTERACTION", f"{format_user(user)} triggered {custom_id}{f' | {details}' if details else ''}", channel=channel, user=user)


def log_db(operation, table, details=None):
    emit("DEBUG", "DATABASE", f"{operation} | {table}{f' | {details}' if details else ''}")


def log_filter(user, words, channel=None):
    emit("WARNING", "FILTER", f"Matched {', '.join(words)}", channel=channel, user=user)


def log_transcript(action, channel=None, details=None):
    emit("INFO", "TRANSCRIPT", f"{action}{f' | {details}' if details else ''}", channel=channel)


def log_inactivity(action, channel=None, user=None, details=None):
    emit("WARNING" if "fail" in action.lower() else "INFO", "INACTIVITY", f"{action}{f' | {details}' if details else ''}", channel=channel, user=user)


def log_perm(channel, target, permissions_summary):
    emit("INFO", "PERMISSION", permissions_summary, channel=channel, user=target)


async def send_report_to_owner(bot, embed, file=None, is_error=False):
    if not bot or not is_error or not getattr(config, "SETUP_USER_ID", None):
        return
    try:
        owner = bot.get_user(config.SETUP_USER_ID) or await bot.fetch_user(config.SETUP_USER_ID)
        if file:
            await owner.send(embed=embed, file=file)
        else:
            await owner.send(embed=embed)
    except Exception as error:
        log_exception("DM", error, user=config.SETUP_USER_ID, context="Owner report delivery failed")


def ticket_report(user, application, channel, bot=None):
    log_ticket("Created", channel, user, f"application={application}")


def ticket_claim_report(channel, staff, owner_id, bot):
    log_ticket("Claimed", channel, staff, f"applicant={owner_id or 'Unknown'}")


def ticket_close_report(channel, moderator, owner_id, reason, transcript_path, bot):
    log_ticket("Closed", channel, moderator, f"reason={reason} | applicant={owner_id or 'Unknown'} | transcript={transcript_path or 'Unavailable'}")


def ticket_reopen_report(channel, moderator, owner_id, bot):
    log_ticket("Reopened", channel, moderator, f"applicant={owner_id or 'Unknown'}")


def ticket_delete_report(channel_name, moderator, owner_id, bot):
    log_ticket("Deleted", channel_name, moderator, f"applicant={owner_id or 'Unknown'}")


def error_report(error):
    return log_exception("ERROR", error)
