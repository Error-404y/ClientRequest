import config


def _guild_id(member):
    guild = getattr(member, "guild", None)
    return getattr(guild, "id", None)


def _role_ids(member):
    return {role.id for role in getattr(member, "roles", [])}


def is_owner(member):
    if member is None:
        return False
    guild_id = _guild_id(member)
    if guild_id not in config.GUILDS:
        return False
    guild = getattr(member, "guild", None)
    return bool(
        getattr(guild, "owner_id", None) == getattr(member, "id", None)
        or _role_ids(member).intersection(config.get_owner_roles(guild_id))
    )


def is_moderator(member):
    guild_id = _guild_id(member)
    if guild_id not in config.GUILDS:
        return False
    return config.get_mod_role(guild_id) in _role_ids(member)


def is_trial_moderator(member):
    guild_id = _guild_id(member)
    if guild_id not in config.GUILDS:
        return False
    return config.get_trial_mod_role(guild_id) in _role_ids(member)


def is_staff(member):
    return (
        can_setup(member)
        or is_owner(member)
        or is_moderator(member)
        or is_trial_moderator(member)
    )


def can_setup(member):
    if member is None:
        return False
    guild = getattr(member, "guild", None)
    permissions = getattr(member, "guild_permissions", None)
    guild_id = getattr(guild, "id", None)
    settings = config.GUILDS.get(guild_id, {})
    return bool(
        getattr(guild, "owner_id", None) == getattr(member, "id", None)
        or getattr(permissions, "administrator", False)
        or getattr(member, "id", None) in settings.get("SETUP_ADMIN_USERS", [])
    )


def can_manage_setup_admins(member):
    if member is None:
        return False
    guild = getattr(member, "guild", None)
    return getattr(guild, "owner_id", None) == getattr(member, "id", None)


def _allowed_moderation_user(member):
    if member is None:
        return False
    if is_staff(member):
        return True
    guild_id = _guild_id(member)
    if guild_id not in config.GUILDS:
        return False
    return getattr(member, "id", None) in config.get_guild_config(guild_id).get(
        "ALLOWED_BAN_USERS", []
    )


def can_ban(member):
    return _allowed_moderation_user(member)


def can_kick(member):
    return _allowed_moderation_user(member)


def can_warn_or_view_history(member):
    if member is None:
        return False
    guild_id = _guild_id(member)
    if guild_id not in config.GUILDS:
        return False
    target_role_id = config.get_guild_config(guild_id)["WARN_HISTORY_ROLE_ID"]
    return target_role_id in _role_ids(member) or is_staff(member)


def can_moderate_target(member, target):
    if member is None or target is None:
        return False
    guild = getattr(member, "guild", None)
    if guild is None:
        return False
    target_id = getattr(target, "id", target if isinstance(target, int) else None)
    if not target_id or target_id == member.id or target_id == guild.owner_id:
        return False
    if member.id == guild.owner_id:
        return True
    target_member = (
        target
        if getattr(target, "guild", None) == guild
        else guild.get_member(target_id)
    )
    if target_member is None:
        return True
    return member.top_role > target_member.top_role
