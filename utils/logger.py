import logging
import os
import re
import traceback
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import discord
import pytz

import config

timezone = pytz.timezone("Europe/Berlin")
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5
ANSI = {
    "DEBUG": "\033[90m",
    "INFO": "\033[36m",
    "SUCCESS": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
    "RESET": "\033[0m",
}
SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(discord_token|token|authorization|password|secret)(\s*[=:]\s*)([^\s,;]+)"),
    re.compile(r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}"),
]
_initialized = False
_file_handlers = {}


class StructuredFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created, timezone).strftime("%d.%m.%Y %H:%M:%S")
        level = getattr(record, "display_level", record.levelname)
        category = getattr(record, "category", "SYSTEM")
        reference = getattr(record, "reference", None)
        guild_id = getattr(record, "guild_id", None)
        channel_id = getattr(record, "channel_id", None)
        user_id = getattr(record, "user_id", None)
        context = []
        if guild_id:
            context.append(f"guild={guild_id}")
        if channel_id:
            context.append(f"channel={channel_id}")
        if user_id:
            context.append(f"user={user_id}")
        if reference:
            context.append(f"ref={reference}")
        suffix = f" | {' '.join(context)}" if context else ""
        return f"{timestamp} | {level:<8} | {category:<12} | {record.getMessage()}{suffix}"


class ColorFormatter(StructuredFormatter):
    def format(self, record):
        plain = super().format(record)
        level = getattr(record, "display_level", record.levelname)
        if not os.isatty(1):
            return plain
        return f"{ANSI.get(level, '')}{plain}{ANSI['RESET']}"


def get_time():
    return datetime.now(timezone).strftime("%d.%m.%Y %H:%M:%S")


def redact(value):
    text = str(value)
    for pattern in SENSITIVE_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1\2[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    token = getattr(config, "TOKEN", "")
    if token:
        text = text.replace(token, "[REDACTED]")
    return text


def _safe_filename(value):
    safe = "".join(character if character.isalnum() or character in ("_", "-") else "_" for character in str(value))
    return safe.strip("_") or "unknown"


def _handler(filename):
    path = str(Path(config.LOG_FOLDER) / filename)
    if path not in _file_handlers:
        handler = RotatingFileHandler(path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
        handler.setFormatter(StructuredFormatter())
        _file_handlers[path] = handler
    return _file_handlers[path]


def setup_logs():
    global _initialized
    Path(config.LOG_FOLDER).mkdir(parents=True, exist_ok=True)
    if _initialized:
        return
    logger = logging.getLogger("maja")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    console = logging.StreamHandler()
    console.setFormatter(ColorFormatter())
    logger.addHandler(console)
    logger.addHandler(_handler("bot.log"))
    _initialized = True


def create_error_reference():
    return f"ERR-{uuid.uuid4().hex[:8].upper()}"


def _guild_details(guild):
    if isinstance(guild, int):
        return guild, config.GUILDS.get(guild, {}).get("NAME", str(guild))
    if guild is not None:
        return getattr(guild, "id", None), getattr(guild, "name", None)
    return None, None


def emit(level, category, message, guild=None, channel=None, user=None, reference=None):
    setup_logs()
    logger = logging.getLogger("maja")
    normalized_level = level.upper()
    python_level = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "SUCCESS": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }.get(normalized_level, logging.INFO)
    guild_id, guild_name = _guild_details(guild or _extract_guild(channel) or _extract_guild(user))
    extra = {
        "display_level": normalized_level,
        "category": category.upper(),
        "reference": reference,
        "guild_id": guild_id,
        "channel_id": getattr(channel, "id", None),
        "user_id": getattr(user, "id", user if isinstance(user, int) else None),
    }
    safe_message = redact(message)
    record = logger.makeRecord("maja", python_level, "", 0, safe_message, (), None, extra=extra)
    logger.handle(record)
    category_handler = _handler(f"{category.lower()}.log")
    category_handler.handle(record)
    if normalized_level in {"ERROR", "CRITICAL"}:
        _handler("errors.log").handle(record)
    if guild_id:
        server_name = _safe_filename(guild_name or guild_id)
        _handler(f"server_{server_name}.log").handle(record)


def log(message, guild=None):
    text = str(message)
    match = re.match(r"\[(?:DEBUG|INFO|WARNING|ERROR)/([^]]+)]\s*(.*)", text, re.DOTALL)
    if match:
        category, text = match.groups()
    else:
        category = "SYSTEM"
    upper = text.upper()
    if category.upper() == "ERROR" or "FAILED" in upper or "ERROR" in upper:
        level = "ERROR"
    elif "WARNING" in upper or "MISSING" in upper:
        level = "WARNING"
    elif any(word in upper for word in ("SUCCESS", "LOADED", "ONLINE", "COMPLETED", "CONNECTED")):
        level = "SUCCESS"
    else:
        level = "INFO"
    emit(level, category, text, guild=guild)


def log_exception(category, error, guild=None, channel=None, user=None, context=None):
    reference = create_error_reference()
    details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    message = f"{context + ' | ' if context else ''}{type(error).__name__}: {error}\n{details}"
    emit("ERROR", category, message, guild=guild, channel=channel, user=user, reference=reference)
    return reference


def format_user(user):
    if user is None:
        return "Unknown User"
    if isinstance(user, int):
        return f"User ID {user}"
    name = getattr(user, "name", getattr(user, "display_name", str(user)))
    user_id = getattr(user, "id", None)
    return f"{name} ({user_id})" if user_id else str(name)


def format_channel(channel):
    if channel is None:
        return "Unknown Channel"
    name = getattr(channel, "name", str(channel))
    channel_id = getattr(channel, "id", None)
    return f"#{name} ({channel_id})" if channel_id else str(name)


def _extract_guild(obj):
    return getattr(obj, "guild", None) if obj is not None else None


def log_debug(category, message, guild=None):
    emit("DEBUG", category, message, guild=guild)


def log_dm(recipient, subject, success=True, error_detail=None):
    level = "SUCCESS" if success else "ERROR"
    message = f"DM {'sent to' if success else 'failed for'} {format_user(recipient)} | subject={subject}"
    if error_detail:
        message += f" | error={error_detail}"
    emit(level, "DM", message, guild=_extract_guild(recipient), user=recipient)


def log_mod(action, moderator, target, reason=None, extra=None):
    message = f"{action} | moderator={format_user(moderator)} | target={format_user(target)}"
    if reason:
        message += f" | reason={reason}"
    if extra:
        message += f" | {extra}"
    emit("INFO", "MODERATION", message, guild=_extract_guild(moderator) or _extract_guild(target), user=moderator)


def log_command(user, command_name, channel=None, details=None):
    message = f"{format_user(user)} executed {command_name}"
    if details:
        message += f" | {details}"
    emit("INFO", "COMMAND", message, channel=channel, user=user)


def log_ticket(action, channel, user=None, details=None):
    message = f"{action} | ticket={format_channel(channel)}"
    if details:
        message += f" | {details}"
    level = "ERROR" if "fail" in action.lower() else "SUCCESS" if action.lower() in {"created", "claimed", "closed", "reopened", "deleted"} else "INFO"
    emit(level, "TICKET", message, channel=channel if hasattr(channel, "guild") else None, user=user)


def log_interaction(user, custom_id, channel=None, details=None):
    message = f"{format_user(user)} triggered {custom_id}"
    if details:
        message += f" | {details}"
    emit("INFO", "INTERACTION", message, channel=channel, user=user)


def log_db(operation, table, details=None):
    message = f"{operation} | table={table}"
    if details:
        message += f" | {details}"
    emit("INFO", "DATABASE", message)


def log_filter(user, words, channel=None):
    emit("WARNING", "FILTER", f"Content filter matched {words}", channel=channel, user=user)


def log_transcript(action, channel=None, details=None):
    message = action if not details else f"{action} | {details}"
    emit("INFO", "TRANSCRIPT", message, channel=channel)


def log_inactivity(action, channel=None, user=None, details=None):
    message = action if not details else f"{action} | {details}"
    level = "ERROR" if "fail" in action.lower() else "INFO"
    emit(level, "INACTIVITY", message, channel=channel, user=user)


def log_perm(channel, target, permissions_summary):
    emit("INFO", "PERMISSION", permissions_summary, channel=channel, user=target)


async def send_report_to_owner(bot, embed, file=None, is_error=False):
    if not bot or not is_error:
        return
    owner_id = getattr(config, "SETUP_USER_ID", None)
    if not owner_id:
        emit("ERROR", "ERROR", "SETUP_USER_ID is not configured")
        return
    try:
        owner = bot.get_user(owner_id) or await bot.fetch_user(owner_id)
        if file:
            await owner.send(embed=embed, file=file)
        else:
            await owner.send(embed=embed)
        log_dm(owner, f"{config.BOT_NAME} error report", success=True)
    except Exception as error:
        log_exception("DM", error, user=owner_id, context="Owner error report delivery failed")


def ticket_report(user, application, channel, bot=None):
    log_ticket("Created", channel, user, details=f"application={application}")


def ticket_claim_report(channel, staff, owner_id, bot):
    log_ticket("Claimed", channel, staff, details=f"applicant_id={owner_id or 'Unknown'}")


def ticket_close_report(channel, moderator, owner_id, reason, transcript_path, bot):
    log_ticket("Closed", channel, moderator, details=f"reason={reason} | applicant_id={owner_id or 'Unknown'} | transcript={transcript_path or 'Unavailable'}")


def ticket_reopen_report(channel, moderator, owner_id, bot):
    log_ticket("Reopened", channel, moderator, details=f"applicant_id={owner_id or 'Unknown'}")


def ticket_delete_report(channel_name, moderator, owner_id, bot):
    log_ticket("Deleted", channel_name, moderator, details=f"applicant_id={owner_id or 'Unknown'}")


def error_report(error):
    return log_exception("ERROR", error)
