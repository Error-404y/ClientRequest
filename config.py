import os
from pathlib import Path


def _load_local_env():
    env_path = Path(__file__).with_name(".env")
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SUPPORT_SERVER_URL = os.getenv("SUPPORT_SERVER_URL", "").strip()
PRIVACY_POLICY_URL = os.getenv("PRIVACY_POLICY_URL", "").strip()
TERMS_OF_SERVICE_URL = os.getenv("TERMS_OF_SERVICE_URL", "").strip()
ERROR_REPORT_USER_ID = (
    int(os.getenv("ERROR_REPORT_USER_ID", "0"))
    if os.getenv("ERROR_REPORT_USER_ID", "0").isdigit()
    else 0
)

DEFAULT_TICKET_OPTIONS = [
    "General Support",
    "Partnership",
    "Player Reports",
    "Questions",
    "Issues",
]


def normalize_guild_config(guild_id, settings=None):
    values = dict(settings or {})
    staff_roles = [int(role_id) for role_id in values.get("OWNER_ROLES", []) if role_id]
    primary_staff_role = int(
        values.get("MOD_ROLE") or (staff_roles[0] if staff_roles else 0)
    )
    if primary_staff_role and primary_staff_role not in staff_roles:
        staff_roles.append(primary_staff_role)
    return {
        "NAME": str(values.get("NAME") or f"Server {guild_id}"),
        "TICKET_CATEGORY_ID": int(values.get("TICKET_CATEGORY_ID") or 0),
        "TICKET_PANEL_CHANNEL_ID": int(values.get("TICKET_PANEL_CHANNEL_ID") or 0),
        "TICKET_ARCHIVE_CATEGORY_ID": int(
            values.get("TICKET_ARCHIVE_CATEGORY_ID") or 0
        ),
        "LOG_CHANNEL_ID": int(values.get("LOG_CHANNEL_ID") or 0),
        "OWNER_ROLES": staff_roles,
        "MOD_ROLE": primary_staff_role,
        "TRIAL_MOD_ROLE": int(values.get("TRIAL_MOD_ROLE") or primary_staff_role),
        "WARN_HISTORY_ROLE_ID": int(
            values.get("WARN_HISTORY_ROLE_ID") or primary_staff_role
        ),
        "ALLOWED_BAN_USERS": [
            int(user_id) for user_id in values.get("ALLOWED_BAN_USERS", []) if user_id
        ],
        "SETUP_ADMIN_USERS": [
            int(user_id) for user_id in values.get("SETUP_ADMIN_USERS", []) if user_id
        ],
        "TICKET_OPTIONS": list(values.get("TICKET_OPTIONS") or DEFAULT_TICKET_OPTIONS),
        "TIMEZONE": str(values.get("TIMEZONE") or "Europe/Athens"),
        "SETUP_COMPLETE": bool(values.get("SETUP_COMPLETE", False)),
        "WELCOME_SENT": bool(values.get("WELCOME_SENT", False)),
    }


GUILDS = {}


def register_guild_config(guild_id, settings):
    GUILDS[int(guild_id)] = normalize_guild_config(guild_id, settings)
    return GUILDS[int(guild_id)]


def remove_guild_config(guild_id):
    return GUILDS.pop(int(guild_id), None)


def is_guild_configured(guild_id):
    settings = GUILDS.get(int(guild_id))
    return bool(settings and settings.get("SETUP_COMPLETE"))


def replace_guild_configs(settings_by_guild):
    GUILDS.clear()
    for guild_id, settings in settings_by_guild.items():
        register_guild_config(guild_id, settings)


def get_guild_config(guild_id):
    try:
        return GUILDS[int(guild_id)]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Guild {guild_id} is not configured") from error


def get_ticket_category_id(guild_id):
    return get_guild_config(guild_id)["TICKET_CATEGORY_ID"]


def get_archive_category_id(guild_id):
    return get_guild_config(guild_id)["TICKET_ARCHIVE_CATEGORY_ID"]


def get_owner_roles(guild_id):
    return get_guild_config(guild_id)["OWNER_ROLES"]


def get_mod_role(guild_id):
    return get_guild_config(guild_id)["MOD_ROLE"]


def get_trial_mod_role(guild_id):
    return get_guild_config(guild_id)["TRIAL_MOD_ROLE"]


def get_ticket_options(guild_id):
    return get_guild_config(guild_id)["TICKET_OPTIONS"]


BOT_NAME = "! maja !"
BOT_CREATOR = "! ZER"
BOT_COLLABORATORS = "! Unbekannt"
BOT_CREDITS = "Created by ! ZER and ! Unbekannt"
TIMEZONE = "Europe/Athens"
UPLOADER_FORM = "https://docs.google.com/forms/d/e/1FAIpQLScEXtR945KYxZovhw4mTNnJ3CLOIBs06kQUiKeYjzJP1A-T9Q/viewform?usp=dialog"
MODERATOR_FORM = "https://docs.google.com/forms/d/e/1FAIpQLScmr7q3XpXUNbrXMdpIeev0OoZv-yAWZoOWMbP6EDSp3lGdHA/viewform?usp=sharing&ouid=117835685606289606539"
BASE_DIR = Path(__file__).resolve().parent
DATABASE = str(BASE_DIR / "database.db")
TRANSCRIPT_FOLDER = str(BASE_DIR / "transcripts")
LOG_FOLDER = str(BASE_DIR / "logs")
INACTIVITY_WARN_HOURS = 24
INACTIVITY_CLOSE_HOURS = 24
ESCALATION_SCAN_MINUTES = 5
TICKET_REVIEW_ESCALATION_HOURS = 6
NO_RESPONSE_ESCALATION_HOURS = 24
BAD_WORDS = [
    "nigger",
    "nigga",
    "kill yourself",
    "kys",
    "go die",
    "hang yourself",
    "neck yourself",
    "you should die",
    "hope you die",
    "i hope you die",
    "i will kill you",
    "im going to kill you",
    "i'm going to kill you",
    "your mom should die",
    "your mother should die",
    "your dad should die",
    "your father should die",
    "i hope your mom dies",
    "i hope your mother dies",
    "i hope your dad dies",
    "i hope your father dies",
    "your family should die",
    "kill your mom",
    "kill your mother",
]
