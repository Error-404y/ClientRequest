import asyncio
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import add_infraction
from utils.embeds import error as error_embed
from utils.logger import log_exception, log_interaction
from utils.permissions import can_setup

RULE_PREFIX = f"{config.BOT_NAME} Managed"
RULE_NAMES = {
    "keywords": f"{RULE_PREFIX} Keywords",
    "presets": f"{RULE_PREFIX} Presets",
    "mentions": f"{RULE_PREFIX} Mentions",
}


def build_actions(alert_channel_id=None, timeout_minutes=0):
    actions = [
        discord.AutoModRuleAction(
            type=discord.AutoModRuleActionType.block_message,
            custom_message="This message was blocked by server safety rules.",
        )
    ]
    if alert_channel_id:
        actions.append(
            discord.AutoModRuleAction(
                type=discord.AutoModRuleActionType.send_alert_message,
                channel_id=int(alert_channel_id),
            )
        )
    if timeout_minutes:
        actions.append(
            discord.AutoModRuleAction(
                type=discord.AutoModRuleActionType.timeout,
                duration=timedelta(minutes=int(timeout_minutes)),
            )
        )
    return actions


def status_embed(rules):
    embed = discord.Embed(
        title="Discord AutoMod Status",
        description="Native Discord AutoMod rules managed by the bot are shown below.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    by_name = {rule.name: rule for rule in rules}
    for key, label in (
        ("keywords", "Keyword Protection"),
        ("presets", "Discord Safety Presets"),
        ("mentions", "Mention Spam Protection"),
    ):
        rule = by_name.get(RULE_NAMES[key])
        embed.add_field(
            name=label,
            value=(
                f"{'Enabled' if rule.enabled else 'Disabled'}\nRule ID: `{rule.id}`"
                if rule
                else "Not configured"
            ),
            inline=False,
        )
    embed.set_footer(text=f"{config.BOT_NAME} | Native Discord AutoMod")
    return embed


class AutoModeration(commands.Cog):
    automodz = app_commands.Group(
        name="automodz", description="Configure native Discord AutoMod protection"
    )

    def __init__(self, bot):
        self.bot = bot

    async def authorize(self, interaction):
        if interaction.guild is None or not config.is_guild_configured(
            interaction.guild.id
        ):
            await interaction.response.send_message(
                embed=error_embed(
                    "This command can only be used in a configured server."
                ),
                ephemeral=True,
            )
            return False
        if not can_setup(interaction.user):
            await interaction.response.send_message(
                embed=error_embed(
                    "Only the server owner or an authorized administrator can manage AutoMod."
                ),
                ephemeral=True,
            )
            return False
        member = interaction.guild.me
        if member is None or not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=error_embed(
                    "I require the Manage Server permission to configure Discord AutoMod."
                ),
                ephemeral=True,
            )
            return False
        return True

    async def managed_rules(self, guild):
        return [
            rule
            for rule in await guild.fetch_automod_rules()
            if rule.name.startswith(RULE_PREFIX)
        ]

    async def upsert_rule(
        self,
        guild,
        existing,
        name,
        trigger,
        actions,
        enabled=True,
        adopt_rule=None,
    ):
        rule = discord.utils.get(existing, name=name)
        if rule:
            return (
                await rule.edit(
                    trigger=trigger,
                    actions=actions,
                    enabled=enabled,
                    reason=f"{config.BOT_NAME} AutoMod configuration",
                ),
                False,
            )
        if adopt_rule:
            return (
                await adopt_rule.edit(
                    name=name,
                    actions=actions,
                    enabled=enabled,
                    reason=f"{config.BOT_NAME} AutoMod rule adoption",
                ),
                True,
            )
        return (
            await guild.create_automod_rule(
                name=name,
                event_type=discord.AutoModRuleEventType.message_send,
                trigger=trigger,
                actions=actions,
                enabled=enabled,
                reason=f"{config.BOT_NAME} AutoMod configuration",
            ),
            False,
        )

    async def configure_rule(self, guild, name, trigger, actions):
        trigger_limits = {
            discord.AutoModRuleTriggerType.keyword: 6,
            discord.AutoModRuleTriggerType.keyword_preset: 1,
            discord.AutoModRuleTriggerType.mention_spam: 1,
        }
        for attempt in range(2):
            all_rules = await guild.fetch_automod_rules()
            managed = [
                rule for rule in all_rules if rule.name.startswith(RULE_PREFIX)
            ]
            existing_rule = discord.utils.get(managed, name=name)
            adopt_rule = None
            if existing_rule is None:
                used = sum(rule.trigger.type == trigger.type for rule in all_rules)
                if used >= trigger_limits[trigger.type]:
                    adopt_rule = next(
                        (
                            rule
                            for rule in all_rules
                            if rule.trigger.type == trigger.type
                        ),
                        None,
                    )
            try:
                return await self.upsert_rule(
                    guild,
                    managed,
                    name,
                    trigger,
                    actions,
                    adopt_rule=adopt_rule,
                )
            except discord.NotFound:
                if attempt:
                    raise
                await asyncio.sleep(0.5)
        raise RuntimeError(f"AutoMod rule configuration did not complete: {name}")

    @automodz.command(
        name="setup", description="Create or update recommended AutoMod rules"
    )
    @app_commands.describe(
        alert_channel="Channel for Discord AutoMod alerts",
        timeout_minutes="Optional timeout applied by AutoMod, or zero to disable",
        mention_limit="Maximum mentions allowed in one message",
    )
    async def setup_rules(
        self,
        interaction: discord.Interaction,
        alert_channel: discord.TextChannel | None = None,
        timeout_minutes: app_commands.Range[int, 0, 40320] = 0,
        mention_limit: app_commands.Range[int, 2, 50] = 5,
    ):
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        settings = config.get_guild_config(guild.id)
        selected_alert = alert_channel or guild.get_channel(
            settings.get("LOG_CHANNEL_ID", 0)
        )
        if timeout_minutes and not guild.me.guild_permissions.moderate_members:
            await interaction.followup.send(
                embed=error_embed(
                    "I require the Moderate Members permission when automatic AutoMod timeouts are enabled."
                ),
                ephemeral=True,
            )
            return
        if isinstance(selected_alert, discord.TextChannel):
            alert_permissions = selected_alert.permissions_for(guild.me)
            if (
                not alert_permissions.view_channel
                or not alert_permissions.send_messages
            ):
                await interaction.followup.send(
                    embed=error_embed(
                        "I require View Channel and Send Messages in the selected AutoMod alert channel."
                    ),
                    ephemeral=True,
                )
                return
        keyword_actions = build_actions(
            selected_alert.id
            if isinstance(selected_alert, discord.TextChannel)
            else None,
            timeout_minutes,
        )
        preset_actions = build_actions(
            selected_alert.id
            if isinstance(selected_alert, discord.TextChannel)
            else None
        )
        mention_actions = build_actions(
            selected_alert.id
            if isinstance(selected_alert, discord.TextChannel)
            else None,
            timeout_minutes,
        )
        rules = []
        adopted_count = 0
        specifications = (
            (
                "keywords",
                discord.AutoModTrigger(
                    type=discord.AutoModRuleTriggerType.keyword,
                    keyword_filter=list(dict.fromkeys(config.BAD_WORDS))[:1000],
                ),
                keyword_actions,
            ),
            (
                "presets",
                discord.AutoModTrigger(
                    type=discord.AutoModRuleTriggerType.keyword_preset,
                    presets=discord.AutoModPresets(
                        profanity=True, sexual_content=True, slurs=True
                    ),
                ),
                preset_actions,
            ),
            (
                "mentions",
                discord.AutoModTrigger(
                    type=discord.AutoModRuleTriggerType.mention_spam,
                    mention_limit=mention_limit,
                    mention_raid_protection=True,
                ),
                mention_actions,
            ),
        )
        for key, trigger, actions in specifications:
            try:
                rule, adopted = await self.configure_rule(
                    guild, RULE_NAMES[key], trigger, actions
                )
            except discord.HTTPException as error:
                reference = log_exception(
                    "AUTOMOD",
                    error,
                    guild=guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context=f"AutoMod setup failed while configuring {key}",
                )
                embed = discord.Embed(
                    title="AutoMod Setup Incomplete",
                    description="Discord rejected one stage of the AutoMod configuration. Any completed stages remain valid and setup can be run again safely.",
                    color=discord.Color.orange(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.add_field(
                    name="Failed Stage", value=key.replace("_", " ").title()
                )
                embed.add_field(
                    name="Completed Rules", value=str(len(rules)), inline=True
                )
                embed.add_field(
                    name="Error Reference", value=f"`{reference}`", inline=True
                )
                embed.add_field(
                    name="Recommended Action",
                    value="Wait a few seconds and run `/automodz setup` again. If the same stage fails, inspect `/debugerror` with the reference above and verify that the selected alert channel still exists.",
                    inline=False,
                )
                embed.set_footer(text=f"{config.BOT_NAME} | Native Discord AutoMod")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            rules.append(rule)
            adopted_count += int(adopted)
        log_interaction(
            interaction.user,
            "automodz setup",
            interaction.channel,
            details=f"Timeout: {timeout_minutes}, Mention limit: {mention_limit}",
        )
        embed = status_embed(rules)
        embed.description = "Discord AutoMod protection was configured successfully. Rules are enforced by Discord before blocked content reaches the server."
        embed.add_field(
            name="Alert Channel",
            value=selected_alert.mention if selected_alert else "Discord default",
            inline=True,
        )
        embed.add_field(
            name="Automatic Timeout",
            value=(
                f"{timeout_minutes} minutes for keyword and mention rules"
                if timeout_minutes
                else "Disabled"
            ),
            inline=True,
        )
        embed.add_field(
            name="Existing Rules Adopted",
            value=str(adopted_count),
            inline=True,
        )
        if adopted_count:
            embed.add_field(
                name="Preserved Configuration",
                value="Existing trigger thresholds, keyword selections, allow lists, regular expressions, role exemptions, and channel exemptions were retained for adopted rules.",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @automodz.command(
        name="status", description="Inspect managed Discord AutoMod rules"
    )
    async def rule_status(self, interaction: discord.Interaction):
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            embed=status_embed(await self.managed_rules(interaction.guild)),
            ephemeral=True,
        )

    async def set_enabled(self, interaction, enabled):
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        rules = await self.managed_rules(interaction.guild)
        if not rules:
            await interaction.followup.send(
                embed=error_embed("Run `/automodz setup` before changing rule status."),
                ephemeral=True,
            )
            return
        for rule in rules:
            await rule.edit(
                enabled=enabled,
                reason=f"{config.BOT_NAME} AutoMod {'enabled' if enabled else 'disabled'}",
            )
        embed = discord.Embed(
            title="AutoMod Protection Updated",
            description=f"All managed Discord AutoMod rules are now {'enabled' if enabled else 'disabled'}.",
            color=discord.Color.green() if enabled else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Rules Updated", value=str(len(rules)), inline=True)
        embed.set_footer(text=f"{config.BOT_NAME} | Native Discord AutoMod")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @automodz.command(name="enable", description="Enable all managed AutoMod rules")
    async def enable_rules(self, interaction: discord.Interaction):
        await self.set_enabled(interaction, True)

    @automodz.command(name="disable", description="Disable all managed AutoMod rules")
    async def disable_rules(self, interaction: discord.Interaction):
        await self.set_enabled(interaction, False)

    async def update_keyword(self, interaction, keyword, remove=False):
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        clean = " ".join(keyword.lower().split())
        if not 2 <= len(clean) <= 60:
            await interaction.followup.send(
                embed=error_embed(
                    "The normalized keyword must contain between 2 and 60 characters."
                ),
                ephemeral=True,
            )
            return
        rules = await self.managed_rules(interaction.guild)
        rule = discord.utils.get(rules, name=RULE_NAMES["keywords"])
        if rule is None:
            await interaction.followup.send(
                embed=error_embed("Run `/automodz setup` before editing keywords."),
                ephemeral=True,
            )
            return
        words = list(rule.trigger.keyword_filter or [])
        existing = {word.casefold(): word for word in words}
        if remove:
            if clean.casefold() not in existing:
                await interaction.followup.send(
                    embed=error_embed(
                        "That keyword is not present in the managed rule."
                    ),
                    ephemeral=True,
                )
                return
            words.remove(existing[clean.casefold()])
        elif clean.casefold() not in existing:
            if len(words) >= 1000:
                await interaction.followup.send(
                    embed=error_embed(
                        "Discord allows a maximum of 1,000 keyword entries in this rule."
                    ),
                    ephemeral=True,
                )
                return
            words.append(clean)
        await rule.edit(
            trigger=discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.keyword,
                keyword_filter=words,
                allow_list=list(rule.trigger.allow_list or []),
                regex_patterns=list(rule.trigger.regex_patterns or []),
            ),
            reason=f"{config.BOT_NAME} AutoMod keyword update",
        )
        embed = discord.Embed(
            title="AutoMod Keyword Updated",
            description=f"`{clean}` was {'removed from' if remove else 'added to'} the managed keyword protection rule.",
            color=discord.Color.orange() if remove else discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Protected Keywords", value=str(len(words)), inline=True)
        embed.set_footer(text=f"{config.BOT_NAME} | Native Discord AutoMod")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @automodz.command(name="addword", description="Add a protected AutoMod keyword")
    async def add_word(
        self, interaction: discord.Interaction, keyword: app_commands.Range[str, 2, 60]
    ):
        await self.update_keyword(interaction, keyword)

    @automodz.command(
        name="removeword", description="Remove a protected AutoMod keyword"
    )
    async def remove_word(
        self, interaction: discord.Interaction, keyword: app_commands.Range[str, 2, 60]
    ):
        await self.update_keyword(interaction, keyword, remove=True)

    async def update_exemption(self, interaction, target, enabled):
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        rules = await self.managed_rules(interaction.guild)
        if not rules:
            await interaction.followup.send(
                embed=error_embed("Run `/automodz setup` before managing exemptions."),
                ephemeral=True,
            )
            return
        maximum = 20 if isinstance(target, discord.Role) else 50
        for rule in rules:
            collection = (
                list(rule.exempt_roles)
                if isinstance(target, discord.Role)
                else list(rule.exempt_channels)
            )
            current_ids = {item.id for item in collection}
            if enabled and target.id not in current_ids and len(collection) >= maximum:
                await interaction.followup.send(
                    embed=error_embed(
                        f"Discord allows a maximum of {maximum} exemptions of this type per AutoMod rule."
                    ),
                    ephemeral=True,
                )
                return
        for rule in rules:
            roles = list(rule.exempt_roles)
            channels = list(rule.exempt_channels)
            collection = roles if isinstance(target, discord.Role) else channels
            current_ids = {item.id for item in collection}
            if enabled and target.id not in current_ids:
                collection.append(target)
            if not enabled:
                collection[:] = [item for item in collection if item.id != target.id]
            await rule.edit(
                exempt_roles=roles,
                exempt_channels=channels,
                reason=f"{config.BOT_NAME} AutoMod exemption update",
            )
        embed = discord.Embed(
            title="AutoMod Exemption Updated",
            description=f"{target.mention} is now {'exempt from' if enabled else 'subject to'} managed AutoMod rules.",
            color=discord.Color.green() if enabled else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Rules Updated", value=str(len(rules)), inline=True)
        embed.set_footer(text=f"{config.BOT_NAME} | Native Discord AutoMod")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @automodz.command(
        name="exemptrole", description="Add or remove an AutoMod role exemption"
    )
    async def exempt_role(
        self, interaction: discord.Interaction, role: discord.Role, exempt: bool = True
    ):
        if interaction.guild and role == interaction.guild.default_role:
            await interaction.response.send_message(
                embed=error_embed("The everyone role cannot be exempted from AutoMod."),
                ephemeral=True,
            )
            return
        await self.update_exemption(interaction, role, exempt)

    @automodz.command(
        name="exemptchannel", description="Add or remove an AutoMod channel exemption"
    )
    async def exempt_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        exempt: bool = True,
    ):
        await self.update_exemption(interaction, channel, exempt)

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        if execution.action.type != discord.AutoModRuleActionType.block_message:
            return
        guild = execution.guild
        if guild is None:
            return
        try:
            rule = await guild.fetch_automod_rule(execution.rule_id)
        except discord.HTTPException as error:
            log_exception(
                "AUTOMOD",
                error,
                guild=guild,
                context="Failed to resolve AutoMod rule execution",
            )
            return
        if not rule.name.startswith(RULE_PREFIX):
            return
        reason = f"Blocked by Discord AutoMod rule: {rule.name}"
        if execution.matched_keyword:
            reason += f" | Matched keyword: {execution.matched_keyword}"
        try:
            await add_infraction(
                user_id=execution.user_id,
                moderator_id=self.bot.user.id,
                action_type="AUTOMOD",
                reason=reason,
                guild_id=guild.id,
            )
        except Exception as error:
            log_exception(
                "AUTOMOD",
                error,
                guild=guild,
                user=execution.user_id,
                context=f"Failed to record AutoMod action for rule {execution.rule_id}",
            )


async def setup(bot):
    await bot.add_cog(AutoModeration(bot))
