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

GUILDS = {
    1536279648428884058: {
        "NAME": "Server 1",
        "TICKET_CATEGORY_ID": 1536487365256814673,
        "TICKET_PANEL_CHANNEL_ID": 1536487522257993728,
        "TICKET_ARCHIVE_CATEGORY_ID": 1536487745327734916,
        "OWNER_ROLES": [1536279648470704149, 1536279648470704153, 1536279648470704152, 1536279648470704150, 1536591098112114758],
        "MOD_ROLE": 1536279648470704150,
        "TRIAL_MOD_ROLE": 1536279648470704150,
        "WARN_HISTORY_ROLE_ID": 1536279648470704150,
        "ALLOWED_BAN_USERS": [1508934920377204950, 1269233770834165860],
        "TICKET_OPTIONS": ["Partnership", "Player Reports", "Moderator Application", "Server suggestions"],
    },
    1535446539659386950: {
        "NAME": "Server 2",
        "TICKET_CATEGORY_ID": 1536450516157206821,
        "TICKET_PANEL_CHANNEL_ID": 1536449043897524234,
        "TICKET_ARCHIVE_CATEGORY_ID": 1536450412603904170,
        "OWNER_ROLES": [1536449964899696801],
        "MOD_ROLE": 1536449964899696801,
        "TRIAL_MOD_ROLE": 1536449964899696801,
        "WARN_HISTORY_ROLE_ID": 1536449964899696801,
        "ALLOWED_BAN_USERS": [1508934920377204950, 1269233770834165860],
        "TICKET_OPTIONS": ["Partnership", "Player Reports", "Apply Higher Role", "Question", "Issues"],
    },
}


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


GUILD_ID = 1536279648428884058
TICKET_CATEGORY_ID = GUILDS[GUILD_ID]["TICKET_CATEGORY_ID"]
TICKET_PANEL_CHANNEL_ID = GUILDS[GUILD_ID]["TICKET_PANEL_CHANNEL_ID"]
TICKET_ARCHIVE_CATEGORY_ID = GUILDS[GUILD_ID]["TICKET_ARCHIVE_CATEGORY_ID"]
SETUP_USER_ID = 1269233770834165860
ALLOWED_BAN_USERS = [1508934920377204950, 1269233770834165860]
OWNER_ROLES = GUILDS[GUILD_ID]["OWNER_ROLES"]
MOD_ROLE = GUILDS[GUILD_ID]["MOD_ROLE"]
TRIAL_MOD_ROLE = GUILDS[GUILD_ID]["TRIAL_MOD_ROLE"]
WARN_HISTORY_ROLE_ID = GUILDS[GUILD_ID]["WARN_HISTORY_ROLE_ID"]
BOT_NAME = "! maja !"
BOT_CREATOR = "! ZER"
BOT_COLLABORATORS = "! Unbekannt"
BOT_CREDITS = "Created by ! ZER and ! Unbekannt"
UPLOADER_FORM = "https://docs.google.com/forms/d/e/1FAIpQLScEXtR945KYxZovhw4mTNnJ3CLOIBs06kQUiKeYjzJP1A-T9Q/viewform?usp=dialog"
MODERATOR_FORM = "https://docs.google.com/forms/d/e/1FAIpQLScmr7q3XpXUNbrXMdpIeev0OoZv-yAWZoOWMbP6EDSp3lGdHA/viewform?usp=sharing&ouid=117835685606289606539"
BASE_DIR = Path(__file__).resolve().parent
DATABASE = str(BASE_DIR / "database.db")
TRANSCRIPT_FOLDER = str(BASE_DIR / "transcripts")
LOG_FOLDER = str(BASE_DIR / "logs")
INACTIVITY_WARN_HOURS = 24
INACTIVITY_CLOSE_HOURS = 24
BAD_WORDS = [
    "fuck", "shit", "bitch", "asshole", "bastard", "cunt", "dick", "pussy",
    "nigger", "nigga", "retard", "whore", "slut", "motherfucker", "faggot",
    "stfu", "gtfo",
]
