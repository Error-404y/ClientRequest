


TOKEN = "**************************************************"


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
        "TICKET_OPTIONS": ["Partnership", "Player Reports", "Moderator Application", "Server suggestions"]
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
        "TICKET_OPTIONS": ["Partnership", "Player Reports", "Apply Higher Role", "Question", "Issues"]
    }
}


def get_guild_config(guild_id):
    return GUILDS.get(guild_id, GUILDS[1536279648428884058])

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
TICKET_CATEGORY_ID = 1536487365256814673
TICKET_PANEL_CHANNEL_ID = 1536487522257993728
TICKET_ARCHIVE_CATEGORY_ID = 1536487745327734916
SETUP_USER_ID = 1269233770834165860
ALLOWED_BAN_USERS = [1508934920377204950, 1269233770834165860]
OWNER_ROLES = [1536279648470704149, 1536279648470704153, 1536279648470704152, 1536279648470704150]
MOD_ROLE = 1536279648470704150
TRIAL_MOD_ROLE = 1536279648470704150
WARN_HISTORY_ROLE_ID = 1536279648470704150


BOT_NAME = "ZER & Kaiser Ticket Bot"
BOT_CREATOR = "ZER & Kaiser"
BOT_COLLABORATORS = "Kaiser"

UPLOADER_FORM = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLScEXtR945KYxZovhw4mTNnJ3CLOIBs06kQUiKeYjzJP1A-T9Q/"
    "viewform?usp=dialog"
)

MODERATOR_FORM = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLScmr7q3XpXUNbrXMdpIeev0OoZv-yAWZoOWMbP6EDSp3lGdHA/"
    "viewform?usp=sharing&ouid=117835685606289606539"
)


DATABASE = "database.db"
TRANSCRIPT_FOLDER = "transcripts"
LOG_FOLDER = "logs"


INACTIVITY_WARN_HOURS = 24
INACTIVITY_CLOSE_HOURS = 24


BAD_WORDS = [
    "fuck", "shit", "bitch", "asshole", "bastard", "cunt",
    "dick", "pussy", "nigger", "nigga", "retard", "whore",
    "slut", "motherfucker", "faggot", "stfu", "gtfo"
]


