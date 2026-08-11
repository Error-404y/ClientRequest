import config

# ==========================================
# Owner Check
# ==========================================
def is_owner(member):
    if member is None:
        return False

    if getattr(member, "id", None) == config.SETUP_USER_ID:
        return True

    guild_id = member.guild.id if hasattr(member, "guild") and member.guild else config.GUILD_ID
    owner_roles = config.get_owner_roles(guild_id)

    roles = getattr(member, "roles", [])
    return any(
        role.id in owner_roles
        for role in roles
    )

# ==========================================
# Moderator Check
# ==========================================
def is_moderator(member):
    if member is None:
        return False

    guild_id = member.guild.id if hasattr(member, "guild") and member.guild else config.GUILD_ID
    mod_role_id = config.get_mod_role(guild_id)

    roles = getattr(member, "roles", [])
    return any(
        role.id == mod_role_id
        for role in roles
    )

# ==========================================
# Trial Moderator Check
# ==========================================
def is_trial_moderator(member):
    if member is None:
        return False

    guild_id = member.guild.id if hasattr(member, "guild") and member.guild else config.GUILD_ID
    trial_mod_role_id = config.get_trial_mod_role(guild_id)

    roles = getattr(member, "roles", [])
    return any(
        role.id == trial_mod_role_id
        for role in roles
    )


# ==========================================
# Staff Check
# ==========================================
def is_staff(member):
    return is_owner(member) or is_moderator(member) or is_trial_moderator(member)


# ==========================================
# Setup Permission Check
# ==========================================
def can_setup(member):
    if member is None:
        return False

    if member.id == config.SETUP_USER_ID:
        return True

    return is_owner(member)


# ==========================================
# Ban Permission Check
# ==========================================
def can_ban(member):
    if member is None:
        return False

    if is_staff(member):
        return True

    user_id = getattr(member, "id", None)
    if not user_id:
        return False

    allowed_ids = getattr(config, "ALLOWED_BAN_USERS", [1508934920377204950, 1269233770834165860])
    return user_id in allowed_ids



# ==========================================
# Warn & History Permission Check
# ==========================================
def can_warn_or_view_history(member):
    if member is None:
        return False

    if getattr(member, "id", None) == config.SETUP_USER_ID:
        return True

    guild_id = member.guild.id if hasattr(member, "guild") and member.guild else config.GUILD_ID
    guild_cfg = config.get_guild_config(guild_id)
    target_role_id = guild_cfg.get("WARN_HISTORY_ROLE_ID", 1492287624067547326)

    roles = getattr(member, "roles", [])
    return any(role.id == target_role_id for role in roles) or is_owner(member)
