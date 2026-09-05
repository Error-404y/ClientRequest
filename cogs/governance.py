from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from cogs.onboarding import resource_report, setup_permission_report
from utils.database import (
    add_appeal_details,
    add_approval_details,
    add_infraction,
    cancel_pending_approval_requests,
    claim_moderation_appeal,
    complete_moderation_appeal,
    create_moderation_appeal,
    delete_ticket_form,
    expire_approval_requests,
    fail_stale_appeal_reviews,
    fail_stale_approval_executions,
    get_approval_request,
    get_approval_requests,
    get_approval_rule,
    get_approval_rules,
    get_infraction_by_uuid,
    get_moderation_appeal,
    get_moderation_appeals,
    get_risk_records,
    get_ticket_forms,
    remove_infraction_by_uuid,
    set_approval_rule,
    set_ticket_form,
    vote_approval_request,
)
from utils.embeds import error as error_embed
from utils.governance import (
    ACTION_LABELS,
    APPROVAL_ACTIONS,
    approval_view,
    calculate_risk,
    can_approve,
    execute_approved_action,
    parse_ticket_questions,
)
from utils.logger import log_exception, log_interaction, log_mod
from utils.permissions import can_setup, is_staff
from views.base import ReliableModal


def action_choices(include_all=False):
    choices = [
        app_commands.Choice(name=ACTION_LABELS[action], value=action)
        for action in APPROVAL_ACTIONS
    ]
    if include_all:
        choices.insert(0, app_commands.Choice(name="All Actions", value="ALL"))
    return choices


def risk_label(level):
    return {
        1: "Low",
        2: "Guarded",
        3: "Elevated",
        4: "High",
        5: "Critical",
    }[level]


def risk_color(level):
    return {
        1: discord.Color.green(),
        2: discord.Color.blue(),
        3: discord.Color.orange(),
        4: discord.Color.from_rgb(230, 126, 34),
        5: discord.Color.red(),
    }[level]


class ApprovalReasonModal(ReliableModal):
    def __init__(self, cog, request_uuid, decision):
        title = "Request More Details" if decision == "DETAILS" else "Deny Request"
        super().__init__(title=title)
        self.cog = cog
        self.request_uuid = request_uuid
        self.decision = decision
        self.reason = discord.ui.TextInput(
            label="Explanation",
            style=discord.TextStyle.paragraph,
            min_length=3,
            max_length=500,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        await self.cog.handle_approval_decision(
            interaction, self.request_uuid, self.decision, self.reason.value
        )


class AppealSubmissionModal(ReliableModal):
    def __init__(self, cog, guild_id, infraction_uuid):
        super().__init__(title="Submit Moderation Appeal")
        self.cog = cog
        self.guild_id = guild_id
        self.infraction_uuid = infraction_uuid
        self.reason = discord.ui.TextInput(
            label="Why should this action be reviewed?",
            style=discord.TextStyle.paragraph,
            min_length=20,
            max_length=1000,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.submit_appeal(
            interaction, self.guild_id, self.infraction_uuid, self.reason.value
        )


class Governance(commands.Cog):
    approvalz = app_commands.Group(
        name="approvalz", description="Configure and manage senior approvals"
    )
    ticketformz = app_commands.Group(
        name="ticketformz", description="Configure custom ticket intake forms"
    )
    riskz = app_commands.Group(
        name="riskz", description="Review server-scoped moderation risk"
    )
    appealz = app_commands.Group(
        name="appealz", description="Submit and review moderation appeals"
    )

    def __init__(self, bot):
        self.bot = bot
        self.maintain_workflows.start()

    def cog_unload(self):
        self.maintain_workflows.cancel()

    @tasks.loop(minutes=5)
    async def maintain_workflows(self):
        now = datetime.now(timezone.utc)
        await expire_approval_requests(now.isoformat())
        await fail_stale_approval_executions(
            (now - timedelta(minutes=15)).isoformat(), now.isoformat()
        )
        await fail_stale_appeal_reviews(
            (now - timedelta(minutes=15)).isoformat(), now.isoformat()
        )

    @maintain_workflows.before_loop
    async def before_maintain_workflows(self):
        await self.bot.wait_until_ready()

    @maintain_workflows.error
    async def maintain_workflows_error(self, error):
        log_exception(
            "WORKER", error, context="Governance lifecycle maintenance stopped"
        )

    def is_server_owner(self, interaction):
        return bool(
            interaction.guild and interaction.user.id == interaction.guild.owner_id
        )

    async def owner_only(self, interaction):
        if self.is_server_owner(interaction) and interaction.guild.id in config.GUILDS:
            return True
        if interaction.guild and interaction.guild.id not in config.GUILDS:
            await interaction.response.send_message(
                embed=error_embed(
                    "This server must complete `/setup start` before senior approvals can be configured."
                ),
                ephemeral=True,
            )
            return False
        await interaction.response.send_message(
            embed=error_embed(
                "Only the Discord server owner can configure senior approvals."
            ),
            ephemeral=True,
        )
        return False

    @approvalz.command(
        name="configure", description="Configure approval requirements for an action"
    )
    @app_commands.describe(
        action="Moderation action to configure",
        enabled="Whether this action requires approval",
        senior_role="Role authorized to review requests",
        review_channel="Private channel receiving approval requests",
        required_approvals="Independent approvals required",
        expiry_minutes="Minutes before requests expire",
        senior_bypass="Allow senior-role members to act immediately",
    )
    @app_commands.choices(action=action_choices(include_all=True))
    async def configure_approval(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        enabled: bool,
        senior_role: discord.Role | None = None,
        review_channel: discord.TextChannel | None = None,
        required_approvals: app_commands.Range[int, 1, 3] = 1,
        expiry_minutes: app_commands.Range[int, 5, 10080] = 1440,
        senior_bypass: bool = True,
    ):
        if not await self.owner_only(interaction):
            return
        guild = interaction.guild
        if enabled and (senior_role is None or review_channel is None):
            await interaction.response.send_message(
                embed=error_embed(
                    "An enabled rule requires both a senior role and a private review channel."
                ),
                ephemeral=True,
            )
            return
        if senior_role and senior_role.is_default():
            await interaction.response.send_message(
                embed=error_embed(
                    "The Everyone role cannot approve moderation actions."
                ),
                ephemeral=True,
            )
            return
        if review_channel:
            bot_member = guild.me
            if bot_member is None:
                await interaction.response.send_message(
                    embed=error_embed(
                        "The bot member could not be resolved in this server. Restart the bot and try again."
                    ),
                    ephemeral=True,
                )
                return
            bot_permissions = review_channel.permissions_for(bot_member)
            if not bot_permissions.view_channel or not bot_permissions.send_messages:
                await interaction.response.send_message(
                    embed=error_embed(
                        "The bot requires View Channel and Send Messages in the selected review channel."
                    ),
                    ephemeral=True,
                )
                return
        actions = APPROVAL_ACTIONS if action.value == "ALL" else (action.value,)
        now = datetime.now(timezone.utc).isoformat()
        for configured_action in actions:
            current = await get_approval_rule(guild.id, configured_action)
            await set_approval_rule(
                guild.id,
                configured_action,
                enabled,
                senior_role.id
                if senior_role
                else (current or {}).get("approver_role_id", 0),
                required_approvals,
                review_channel.id
                if review_channel
                else (current or {}).get("request_channel_id", 0),
                expiry_minutes,
                senior_bypass,
                interaction.user.id,
                now,
            )
            if not enabled:
                await cancel_pending_approval_requests(
                    guild.id,
                    configured_action,
                    "The server owner disabled this approval rule",
                    now,
                )
        log_interaction(
            interaction.user,
            "approvalz configure",
            interaction.channel,
            details=f"Action: {action.value}, Enabled: {enabled}",
        )
        embed = discord.Embed(
            title="Senior Approval Configuration Updated",
            description="The moderation governance policy is now active with the settings below.",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Actions",
            value=(
                "All configurable moderation actions"
                if action.value == "ALL"
                else ACTION_LABELS[action.value]
            ),
            inline=False,
        )
        embed.add_field(name="Status", value="Enabled" if enabled else "Disabled")
        embed.add_field(
            name="Senior Role",
            value=senior_role.mention if senior_role else "Preserved",
        )
        embed.add_field(
            name="Required Approvals", value=str(required_approvals), inline=True
        )
        embed.add_field(
            name="Review Channel",
            value=review_channel.mention if review_channel else "Preserved",
            inline=True,
        )
        embed.add_field(
            name="Senior Bypass",
            value="Allowed" if senior_bypass else "Independent review required",
            inline=True,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Server Owner Policy")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @approvalz.command(name="status", description="View all senior approval rules")
    async def approval_status(self, interaction: discord.Interaction):
        if not interaction.guild or not can_setup(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to view this policy."),
                ephemeral=True,
            )
            return
        rules = {
            rule["action_type"]: rule
            for rule in await get_approval_rules(interaction.guild.id)
        }
        lines = []
        for action in APPROVAL_ACTIONS:
            rule = rules.get(action)
            if not rule or not rule["enabled"]:
                lines.append(f"**{ACTION_LABELS[action]}**: Disabled")
                continue
            role = interaction.guild.get_role(rule["approver_role_id"])
            channel = interaction.guild.get_channel(rule["request_channel_id"])
            lines.append(
                f"**{ACTION_LABELS[action]}**: {rule['required_approvals']} approval(s) | "
                f"{role.mention if role else 'Missing role'} | "
                f"{channel.mention if channel else 'Missing channel'} | "
                f"Bypass: {'Yes' if rule['senior_bypass'] else 'No'}"
            )
        embed = discord.Embed(
            title="Senior Approval Policy",
            description="\n".join(lines),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Moderation Governance")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @approvalz.command(name="pending", description="List open senior approval requests")
    async def approval_pending(self, interaction: discord.Interaction):
        if not interaction.guild or not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed(
                    "You do not have permission to view approval requests."
                ),
                ephemeral=True,
            )
            return
        await expire_approval_requests(
            datetime.now(timezone.utc).isoformat(), interaction.guild.id
        )
        requests = await get_approval_requests(
            interaction.guild.id,
            {"PENDING", "NEEDS_DETAILS", "APPROVED", "EXECUTING"},
            20,
        )
        lines = [
            f"`{request['request_uuid']}` | "
            f"{ACTION_LABELS[request['action_type']]} | "
            f"<@{request['requester_id']}> to <@{request['target_id']}> | "
            f"{request['status'].replace('_', ' ').title()}"
            for request in requests
        ]
        embed = discord.Embed(
            title="Open Senior Approval Requests",
            description="\n".join(lines) or "No approval requests are currently open.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Moderation Governance")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @approvalz.command(
        name="details", description="Provide details requested by senior reviewers"
    )
    async def approval_details(
        self,
        interaction: discord.Interaction,
        request_uuid: str,
        details: app_commands.Range[str, 10, 1000],
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=error_embed("This command must be used in the original server."),
                ephemeral=True,
            )
            return
        updated = await add_approval_details(
            request_uuid,
            interaction.guild.id,
            interaction.user.id,
            details,
            datetime.now(timezone.utc).isoformat(),
        )
        if not updated:
            await interaction.response.send_message(
                embed=error_embed(
                    "No active request awaiting details was found for your account."
                ),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Approval Details Submitted",
            description="The request has returned to pending review.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Request UUID", value=f"`{request_uuid}`")
        embed.set_footer(text=f"{config.BOT_NAME} | Moderation Governance")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        data = interaction.data or {}
        custom_id = str(data.get("custom_id") or "")
        if not custom_id.startswith("approval:"):
            if custom_id.startswith("appeal:start:"):
                parts = custom_id.split(":", 3)
                if len(parts) != 4:
                    return
                try:
                    guild_id = int(parts[2])
                except ValueError:
                    return
                infraction = await get_infraction_by_uuid(parts[3], guild_id)
                if not infraction or infraction["user_id"] != interaction.user.id:
                    await interaction.response.send_message(
                        embed=error_embed(
                            "This appeal link is invalid or belongs to another account."
                        ),
                        ephemeral=True,
                    )
                    return
                await interaction.response.send_modal(
                    AppealSubmissionModal(self, guild_id, infraction["uuid"])
                )
            return
        parts = custom_id.split(":", 2)
        if len(parts) != 3:
            return
        decision, request_uuid = parts[1], parts[2]
        if decision == "approve":
            await self.handle_approval_decision(
                interaction, request_uuid, "APPROVE", "Approved"
            )
        elif decision in {"deny", "details"}:
            await interaction.response.send_modal(
                ApprovalReasonModal(self, request_uuid, decision.upper())
            )

    async def handle_approval_decision(
        self, interaction, request_uuid, decision, reason
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=error_embed("This approval is no longer attached to a server."),
                ephemeral=True,
            )
            return
        request = await get_approval_request(request_uuid, interaction.guild.id)
        if not request:
            await interaction.response.send_message(
                embed=error_embed("This approval request no longer exists."),
                ephemeral=True,
            )
            return
        rule = await get_approval_rule(interaction.guild.id, request["action_type"])
        if not rule or not rule["enabled"] or not can_approve(interaction.user, rule):
            await interaction.response.send_message(
                embed=error_embed("You are not authorized to review this request."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        result = await vote_approval_request(
            request_uuid,
            interaction.guild.id,
            interaction.user.id,
            decision,
            reason,
            datetime.now(timezone.utc).isoformat(),
        )
        status = result["status"]
        if status == "SELF_APPROVAL":
            await interaction.followup.send(
                embed=error_embed(
                    "Requesters cannot approve their own moderation actions."
                ),
                ephemeral=True,
            )
            return
        if status in {"NOT_FOUND", "EXPIRED", "EXECUTED", "FAILED", "DENIED"}:
            if status != "DENIED" or decision != "DENY":
                if status == "EXPIRED" and interaction.message:
                    expired_embed = discord.Embed(
                        title="Moderation Approval Expired",
                        description="The review window ended before this request received a final decision.",
                        color=discord.Color.dark_grey(),
                        timestamp=discord.utils.utcnow(),
                    )
                    expired_embed.add_field(
                        name="Request UUID", value=f"`{request_uuid}`", inline=False
                    )
                    expired_embed.add_field(name="Status", value="Expired")
                    expired_embed.set_footer(
                        text=f"{config.BOT_NAME} | Moderation Governance"
                    )
                    try:
                        await interaction.message.edit(embed=expired_embed, view=None)
                    except discord.HTTPException as error:
                        log_exception(
                            "VIEW",
                            error,
                            guild=interaction.guild,
                            channel=interaction.channel,
                            user=interaction.user,
                            context=f"Expired approval message update failed for {request_uuid}",
                        )
                await interaction.followup.send(
                    embed=error_embed(f"This request is already {status.lower()}."),
                    ephemeral=True,
                )
                return
        requester = interaction.guild.get_member(request["requester_id"])
        if requester is None:
            try:
                requester = await self.bot.fetch_user(request["requester_id"])
            except discord.HTTPException as error:
                log_exception(
                    "DM",
                    error,
                    guild=interaction.guild,
                    context=f"Approval requester lookup failed for {request_uuid}",
                )
        if status == "APPROVED" and "approval_count" in result:
            from utils.database import claim_approval_execution

            claimed = await claim_approval_execution(request_uuid, interaction.guild.id)
            if not claimed:
                await interaction.followup.send(
                    embed=error_embed(
                        "Another reviewer or worker already started this action."
                    ),
                    ephemeral=True,
                )
                return
            try:
                execution = await execute_approved_action(self.bot, request)
                status = "EXECUTED"
                result_uuid = execution["result_uuid"]
                result_text = (
                    f"Action completed. Result UUID: `{result_uuid}`"
                    if result_uuid
                    else "Action completed successfully."
                )
            except Exception as error:
                await self._mark_approval_failed(request, error, interaction)
                status = "FAILED"
                result_text = (
                    "The approved action failed safely. Inspect the error reference."
                )
        elif status == "PENDING":
            result_text = (
                f"Approval recorded: {result['approval_count']}/"
                f"{result['required_approvals']}."
            )
        elif status == "NEEDS_DETAILS":
            result_text = "The requester must provide additional details."
        else:
            result_text = "The request was denied."
        embed = discord.Embed(
            title="Moderation Approval Updated",
            description=result_text,
            color=(
                discord.Color.green()
                if status == "EXECUTED"
                else discord.Color.red()
                if status in {"FAILED", "DENIED"}
                else discord.Color.orange()
            ),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Request UUID", value=f"`{request_uuid}`", inline=False)
        embed.add_field(name="Status", value=status.replace("_", " ").title())
        embed.add_field(name="Reviewed By", value=interaction.user.mention)
        embed.set_footer(text=f"{config.BOT_NAME} | Moderation Governance")
        approval_message = interaction.message
        if approval_message is None and request.get("request_message_id"):
            request_channel = interaction.guild.get_channel(
                request["request_channel_id"]
            )
            if isinstance(request_channel, discord.TextChannel):
                try:
                    approval_message = await request_channel.fetch_message(
                        request["request_message_id"]
                    )
                except discord.HTTPException as error:
                    log_exception(
                        "VIEW",
                        error,
                        guild=interaction.guild,
                        channel=request_channel,
                        user=interaction.user,
                        context=f"Approval message lookup failed for {request_uuid}",
                    )
        if approval_message:
            try:
                await approval_message.edit(
                    embed=embed,
                    view=(
                        approval_view(request_uuid)
                        if status in {"PENDING", "NEEDS_DETAILS"}
                        else None
                    ),
                )
            except discord.HTTPException as error:
                log_exception(
                    "VIEW",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context=f"Approval message update failed for {request_uuid}",
                )
        await interaction.followup.send(embed=embed, ephemeral=True)
        if requester:
            try:
                await requester.send(embed=embed)
            except discord.HTTPException as error:
                log_exception(
                    "DM",
                    error,
                    guild=interaction.guild,
                    user=requester,
                    context=f"Approval status delivery failed for {request_uuid}",
                )

    async def _mark_approval_failed(self, request, error, interaction):
        from utils.database import complete_approval_request

        reference = log_exception(
            "MODERATION",
            error,
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            context=f"Approved action execution failed for {request['request_uuid']}",
        )
        await complete_approval_request(
            request["request_uuid"],
            request["guild_id"],
            "FAILED",
            result_message=f"Error reference: {reference}",
        )

    @ticketformz.command(
        name="configure", description="Create or replace a ticket intake form"
    )
    async def configure_ticket_form(
        self,
        interaction: discord.Interaction,
        ticket_type: str,
        question_1: app_commands.Range[str, 2, 45],
        question_2: app_commands.Range[str, 2, 45] | None = None,
        question_3: app_commands.Range[str, 2, 45] | None = None,
        question_4: app_commands.Range[str, 2, 45] | None = None,
        question_5: app_commands.Range[str, 2, 45] | None = None,
        required_questions: app_commands.Range[int, 0, 5] = 1,
    ):
        if not interaction.guild or not can_setup(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to configure forms."),
                ephemeral=True,
            )
            return
        if interaction.guild.id not in config.GUILDS:
            await interaction.response.send_message(
                embed=error_embed(
                    "This server must complete `/setup start` before ticket forms can be configured."
                ),
                ephemeral=True,
            )
            return
        settings = config.get_guild_config(interaction.guild.id)
        selected = next(
            (
                value
                for value in settings["TICKET_OPTIONS"]
                if value.casefold() == ticket_type.strip().casefold()
            ),
            None,
        )
        if not selected:
            await interaction.response.send_message(
                embed=error_embed(
                    "The ticket type must exactly match one of the configured panel options."
                ),
                ephemeral=True,
            )
            return
        try:
            questions = parse_ticket_questions(
                (question_1, question_2, question_3, question_4, question_5),
                required_questions,
            )
        except ValueError as error:
            await interaction.response.send_message(
                embed=error_embed(str(error)), ephemeral=True
            )
            return
        await set_ticket_form(
            interaction.guild.id,
            selected,
            questions,
            interaction.user.id,
            datetime.now(timezone.utc).isoformat(),
        )
        embed = discord.Embed(
            title="Custom Ticket Form Saved",
            description=(
                f"Members selecting **{selected}** will complete this private form "
                "before a channel is created."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Questions",
            value="\n".join(
                f"{index}. {item['label']} ({'Required' if item['required'] else 'Optional'})"
                for index, item in enumerate(questions, 1)
            ),
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Ticket Intake")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticketformz.command(name="status", description="View configured ticket forms")
    async def ticket_form_status(self, interaction: discord.Interaction):
        if not interaction.guild or not can_setup(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to view forms."),
                ephemeral=True,
            )
            return
        forms = await get_ticket_forms(interaction.guild.id)
        embed = discord.Embed(
            title="Custom Ticket Forms",
            description=(
                "\n".join(
                    f"**{form['ticket_type']}**: {len(form['questions'])} question(s)"
                    for form in forms
                )
                or "No custom ticket forms are configured."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Ticket Intake")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticketformz.command(name="remove", description="Remove a custom ticket form")
    async def remove_ticket_form_command(
        self, interaction: discord.Interaction, ticket_type: str
    ):
        if not interaction.guild or not can_setup(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to remove forms."),
                ephemeral=True,
            )
            return
        removed = await delete_ticket_form(interaction.guild.id, ticket_type)
        embed = discord.Embed(
            title="Ticket Form Removed" if removed else "Ticket Form Not Found",
            description=(
                "The ticket type will return to direct channel creation."
                if removed
                else "No custom form matched that ticket type."
            ),
            color=discord.Color.green() if removed else discord.Color.orange(),
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Ticket Intake")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @riskz.command(name="user", description="Calculate one member's risk level")
    async def risk_user(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.guild or not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to review member risk."),
                ephemeral=True,
            )
            return
        records = await get_risk_records(interaction.guild.id, user.id)
        risk = calculate_risk(records)
        embed = discord.Embed(
            title=f"Member Risk Level {risk['level']} of 5",
            description=(
                f"**{user.display_name}** is currently classified as "
                f"**{risk_label(risk['level'])}** based only on this server's stored "
                "moderation history."
            ),
            color=risk_color(risk["level"]),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user.mention}\n`{user.id}`")
        embed.add_field(name="Weighted Score", value=str(risk["score"]))
        embed.add_field(name="Records Considered", value=str(len(records)))
        breakdown = (
            "\n".join(
                f"{action.replace('_', ' ').title()}: {count}"
                for action, count in sorted(risk["counts"].items())
            )
            or "No weighted moderation records."
        )
        embed.add_field(name="Risk Factors", value=breakdown, inline=False)
        embed.add_field(
            name="Important",
            value=(
                "This score is a review aid. It never applies punishments "
                "automatically and must not replace staff judgment."
            ),
            inline=False,
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"{config.BOT_NAME} | Server-Scoped Risk Review")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @riskz.command(
        name="server", description="List the highest current member risk levels"
    )
    async def risk_server(self, interaction: discord.Interaction):
        if not interaction.guild or not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to review server risk."),
                ephemeral=True,
            )
            return
        if interaction.guild.id not in config.GUILDS:
            await interaction.response.send_message(
                embed=error_embed(
                    "This server has no stored configuration. Run `/setup start` first."
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        records = await get_risk_records(interaction.guild.id)
        grouped = {}
        for record in records:
            grouped.setdefault(record["user_id"], []).append(record)
        rankings = []
        for user_id, user_records in grouped.items():
            member = interaction.guild.get_member(user_id)
            if member is None or member.bot:
                continue
            rankings.append((calculate_risk(user_records), member, len(user_records)))
        rankings.sort(
            key=lambda item: (-item[0]["level"], -item[0]["score"], item[1].id)
        )
        lines = [
            f"{index}. {member.mention} | Level **{risk['level']}** "
            f"({risk_label(risk['level'])}) | Score `{risk['score']}` | "
            f"{count} record(s)"
            for index, (risk, member, count) in enumerate(rankings[:15], 1)
        ]
        embed = discord.Embed(
            title="Server Member Risk Overview",
            description=(
                "\n".join(lines)
                or "No current members have weighted moderation records."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Scope",
            value="Only current non-bot members and records belonging to this server are included.",
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Review Aid Only")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @appealz.command(
        name="submit", description="Privately appeal one warning, timeout or ban"
    )
    async def open_appeal(self, interaction: discord.Interaction, infraction_uuid: str):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=error_embed("Appeals must be opened in the affected server."),
                ephemeral=True,
            )
            return
        infraction = await get_infraction_by_uuid(infraction_uuid, interaction.guild.id)
        if not infraction or infraction["user_id"] != interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed(
                    "No appealable infraction belonging to your account was found."
                ),
                ephemeral=True,
            )
            return
        if infraction["action_type"] not in {"WARN", "TIMEOUT", "BAN"}:
            await interaction.response.send_message(
                embed=error_embed("This type of record cannot be appealed."),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            AppealSubmissionModal(self, interaction.guild.id, infraction["uuid"])
        )

    async def submit_appeal(self, interaction, guild_id, infraction_uuid, reason):
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            await interaction.followup.send(
                embed=error_embed("The bot is no longer connected to that server."),
                ephemeral=True,
            )
            return
        infraction = await get_infraction_by_uuid(infraction_uuid, guild.id)
        if not infraction or infraction["user_id"] != interaction.user.id:
            await interaction.followup.send(
                embed=error_embed("The infraction is no longer available."),
                ephemeral=True,
            )
            return
        appeal = await create_moderation_appeal(
            guild.id,
            interaction.user.id,
            infraction["uuid"],
            infraction["action_type"],
            reason,
            datetime.now(timezone.utc).isoformat(),
        )
        if not appeal:
            await interaction.followup.send(
                embed=error_embed("An appeal already exists for this infraction."),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Moderation Appeal Submitted",
            description="The appeal is private and awaiting staff review.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Appeal UUID", value=f"`{appeal['appeal_uuid']}`")
        embed.add_field(name="Infraction UUID", value=f"`{infraction['uuid']}`")
        embed.add_field(name="Action", value=infraction["action_type"])
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
        embed.set_footer(text=f"{config.BOT_NAME} | Private Appeal")
        await interaction.followup.send(embed=embed, ephemeral=True)
        settings = config.get_guild_config(guild.id)
        review_channel = guild.get_channel(settings["LOG_CHANNEL_ID"])
        if isinstance(review_channel, discord.TextChannel):
            review_embed = embed.copy()
            review_embed.title = "New Moderation Appeal"
            review_embed.add_field(
                name="Appellant",
                value=f"{interaction.user.mention}\n`{interaction.user.id}`",
                inline=False,
            )
            try:
                await review_channel.send(embed=review_embed)
            except discord.HTTPException as error:
                log_exception(
                    "APPEAL",
                    error,
                    guild=guild,
                    channel=review_channel,
                    user=interaction.user,
                    context="Appeal review notification failed",
                )

    @appealz.command(name="view", description="View one private moderation appeal")
    async def view_appeal(self, interaction: discord.Interaction, appeal_uuid: str):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=error_embed("This command must be used in a server."),
                ephemeral=True,
            )
            return
        appeal = await get_moderation_appeal(appeal_uuid, interaction.guild.id)
        if not appeal or (
            appeal["appellant_id"] != interaction.user.id
            and not is_staff(interaction.user)
        ):
            await interaction.response.send_message(
                embed=error_embed("No accessible appeal matched that UUID."),
                ephemeral=True,
            )
            return
        embed = self.appeal_embed(appeal)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @appealz.command(
        name="pending", description="List private appeals awaiting staff review"
    )
    async def pending_appeals(self, interaction: discord.Interaction):
        if not interaction.guild or not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to review appeals."),
                ephemeral=True,
            )
            return
        appeals = await get_moderation_appeals(
            interaction.guild.id, {"PENDING", "NEEDS_DETAILS"}, 20
        )
        lines = [
            f"`{appeal['appeal_uuid']}` | <@{appeal['appellant_id']}> | "
            f"{appeal['action_type']} | "
            f"{appeal['status'].replace('_', ' ').title()}"
            for appeal in appeals
        ]
        embed = discord.Embed(
            title="Pending Moderation Appeals",
            description="\n".join(lines) or "No appeals are awaiting review.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Private Appeal Queue")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @appealz.command(
        name="review", description="Accept, deny or request details for an appeal"
    )
    @app_commands.choices(
        decision=[
            app_commands.Choice(name="Accept", value="ACCEPTED"),
            app_commands.Choice(name="Deny", value="DENIED"),
            app_commands.Choice(name="Request Details", value="NEEDS_DETAILS"),
        ]
    )
    async def review_appeal(
        self,
        interaction: discord.Interaction,
        appeal_uuid: str,
        decision: app_commands.Choice[str],
        response: app_commands.Range[str, 3, 1000],
    ):
        if not interaction.guild or not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("You do not have permission to review appeals."),
                ephemeral=True,
            )
            return
        appeal = await get_moderation_appeal(appeal_uuid, interaction.guild.id)
        if not appeal or appeal["status"] not in {"PENDING", "NEEDS_DETAILS"}:
            await interaction.response.send_message(
                embed=error_embed("No pending appeal matched that UUID."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        reviewed_at = datetime.now(timezone.utc).isoformat()
        claimed = await claim_moderation_appeal(
            appeal_uuid,
            interaction.guild.id,
            interaction.user.id,
            reviewed_at,
        )
        if not claimed:
            await interaction.followup.send(
                embed=error_embed(
                    "Another staff member already started or completed this appeal review."
                ),
                ephemeral=True,
            )
            return
        final_status = decision.value
        if decision.value == "ACCEPTED":
            try:
                await self.accept_appeal(interaction.guild, appeal, interaction.user)
            except Exception as error:
                reference = log_exception(
                    "APPEAL",
                    error,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    context=f"Appeal reversal failed for {appeal_uuid}",
                )
                final_status = "FAILED"
                response = f"The reversal failed safely. Error reference: {reference}"
        updated = await complete_moderation_appeal(
            appeal_uuid,
            interaction.guild.id,
            final_status,
            response,
            interaction.user.id,
            datetime.now(timezone.utc).isoformat(),
        )
        if not updated:
            await interaction.followup.send(
                embed=error_embed("The appeal changed before this review completed."),
                ephemeral=True,
            )
            return
        updated_appeal = await get_moderation_appeal(appeal_uuid, interaction.guild.id)
        embed = self.appeal_embed(updated_appeal)
        await interaction.followup.send(embed=embed, ephemeral=True)
        appellant = interaction.guild.get_member(appeal["appellant_id"])
        if appellant is None:
            try:
                appellant = await self.bot.fetch_user(appeal["appellant_id"])
            except discord.HTTPException as error:
                log_exception(
                    "DM",
                    error,
                    guild=interaction.guild,
                    context=f"Appeal appellant lookup failed for {appeal_uuid}",
                )
        if appellant is not None:
            try:
                await appellant.send(embed=embed)
            except discord.HTTPException as error:
                log_exception(
                    "DM",
                    error,
                    guild=interaction.guild,
                    user=appellant,
                    context=f"Appeal decision delivery failed for {appeal_uuid}",
                )
        log_mod(
            f"APPEAL {final_status}",
            interaction.user,
            appeal["appellant_id"],
            reason=response,
            extra=f"appeal={appeal_uuid}, infraction={appeal['infraction_uuid']}",
        )

    @appealz.command(
        name="details", description="Add information requested by appeal reviewers"
    )
    async def appeal_details(
        self,
        interaction: discord.Interaction,
        appeal_uuid: str,
        details: app_commands.Range[str, 10, 1000],
    ):
        if not interaction.guild:
            await interaction.response.send_message(
                embed=error_embed("This command must be used in the affected server."),
                ephemeral=True,
            )
            return
        updated = await add_appeal_details(
            appeal_uuid,
            interaction.guild.id,
            interaction.user.id,
            details,
            datetime.now(timezone.utc).isoformat(),
        )
        embed = discord.Embed(
            title="Appeal Details Submitted" if updated else "Appeal Not Updated",
            description=(
                "The appeal has returned to pending staff review."
                if updated
                else "No appeal awaiting details was found for your account."
            ),
            color=discord.Color.green() if updated else discord.Color.orange(),
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Private Appeal")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def accept_appeal(self, guild, appeal, reviewer):
        action = appeal["action_type"]
        user_id = appeal["appellant_id"]
        if action == "WARN":
            removed = await remove_infraction_by_uuid(
                appeal["infraction_uuid"], guild.id
            )
            if not removed:
                raise RuntimeError("The warning record no longer exists")
        elif action == "TIMEOUT":
            member = guild.get_member(user_id)
            if member is None:
                raise RuntimeError("The timed-out member is no longer in the server")
            await member.timeout(
                None,
                reason=f"Appeal {appeal['appeal_uuid']} accepted by {reviewer}",
            )
        elif action == "BAN":
            await guild.unban(
                discord.Object(id=user_id),
                reason=f"Appeal {appeal['appeal_uuid']} accepted by {reviewer}",
            )
        await add_infraction(
            user_id,
            reviewer.id,
            "APPEAL_ACCEPTED",
            f"Appeal {appeal['appeal_uuid']} accepted for {appeal['infraction_uuid']}",
            guild.id,
        )

    def appeal_embed(self, appeal):
        color = {
            "ACCEPTED": discord.Color.green(),
            "DENIED": discord.Color.red(),
            "FAILED": discord.Color.red(),
            "NEEDS_DETAILS": discord.Color.orange(),
        }.get(appeal["status"], discord.Color.blurple())
        embed = discord.Embed(
            title="Moderation Appeal",
            description=f"Status: **{appeal['status'].replace('_', ' ').title()}**",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Appeal UUID", value=f"`{appeal['appeal_uuid']}`")
        embed.add_field(name="Infraction UUID", value=f"`{appeal['infraction_uuid']}`")
        embed.add_field(name="Action", value=appeal["action_type"])
        embed.add_field(
            name="Appeal Reason", value=appeal["reason"][:1024], inline=False
        )
        if appeal["staff_response"]:
            embed.add_field(
                name="Staff Response",
                value=appeal["staff_response"][:1024],
                inline=False,
            )
        embed.set_footer(text=f"{config.BOT_NAME} | Private Appeal")
        return embed

    @app_commands.command(
        name="doctorz", description="Scan or safely repair server configuration"
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Scan", value="SCAN"),
            app_commands.Choice(name="Safe Repair", value="REPAIR"),
        ]
    )
    async def doctorz(
        self, interaction: discord.Interaction, action: app_commands.Choice[str]
    ):
        if not interaction.guild or not can_setup(interaction.user):
            await interaction.response.send_message(
                embed=error_embed(
                    "You do not have permission to run Configuration Doctor."
                ),
                ephemeral=True,
            )
            return
        if interaction.guild.id not in config.GUILDS:
            await interaction.response.send_message(
                embed=error_embed(
                    "This server has no stored configuration. Run `/setup start` first."
                ),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        settings = config.get_guild_config(interaction.guild.id)
        if action.value == "REPAIR":
            staff_role = interaction.guild.get_role(settings["MOD_ROLE"])
            onboarding = self.bot.get_cog("Onboarding")
            if onboarding is None or staff_role is None:
                await interaction.followup.send(
                    embed=error_embed(
                        "Safe repair cannot continue because the configured staff "
                        "role or onboarding service is unavailable. Run `/setup start`."
                    ),
                    ephemeral=True,
                )
                return
            repaired, missing = await onboarding.configure_server(
                interaction.guild,
                staff_role,
                ", ".join(settings["TICKET_OPTIONS"]),
            )
            if missing or repaired is None:
                await interaction.followup.send(
                    embed=error_embed(
                        "Safe repair is blocked by missing bot permissions: "
                        + ", ".join(missing or ["Unknown setup failure"])
                    ),
                    ephemeral=True,
                )
                return
        diagnostics = self.bot.get_cog("Diagnostics")
        issues = resource_report(interaction.guild, settings)
        permissions = setup_permission_report(interaction.guild)
        server_check = (
            diagnostics.server_checks(interaction.guild.id)[0]
            if diagnostics
            else {"issues": [], "warnings": []}
        )
        governance_issues = []
        governance_warnings = []
        for rule in await get_approval_rules(interaction.guild.id):
            if not rule["enabled"]:
                continue
            role = interaction.guild.get_role(rule["approver_role_id"])
            channel = interaction.guild.get_channel(rule["request_channel_id"])
            if role is None:
                governance_issues.append(
                    f"{ACTION_LABELS[rule['action_type']]} approval role is missing"
                )
            if not isinstance(channel, discord.TextChannel):
                governance_issues.append(
                    f"{ACTION_LABELS[rule['action_type']]} review channel is missing"
                )
            elif interaction.guild.me:
                channel_permissions = channel.permissions_for(interaction.guild.me)
                if (
                    not channel_permissions.view_channel
                    or not channel_permissions.send_messages
                ):
                    governance_issues.append(
                        f"Bot cannot publish {ACTION_LABELS[rule['action_type']]} approval requests"
                    )
        valid_ticket_types = {value.casefold() for value in settings["TICKET_OPTIONS"]}
        for form in await get_ticket_forms(interaction.guild.id):
            if form["ticket_type"].casefold() not in valid_ticket_types:
                governance_warnings.append(
                    f"Custom form references removed ticket type: {form['ticket_type']}"
                )
        if interaction.guild.me:
            for role_id in set(settings["OWNER_ROLES"]):
                role = interaction.guild.get_role(role_id)
                if role and role >= interaction.guild.me.top_role:
                    governance_issues.append(
                        f"Bot role must be above staff role: {role.name}"
                    )
        all_issues = list(
            dict.fromkeys(
                issues + permissions + server_check["issues"] + governance_issues
            )
        )
        warnings = list(dict.fromkeys(server_check["warnings"] + governance_warnings))
        healthy = not all_issues
        embed = discord.Embed(
            title="Configuration Doctor",
            description=(
                "The server configuration is ready."
                if healthy
                else "Configuration Doctor found items requiring attention."
            ),
            color=discord.Color.green() if healthy else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Operation",
            value="Safe Repair and Verification"
            if action.value == "REPAIR"
            else "Read-Only Scan",
        )
        embed.add_field(
            name="Status", value="Ready" if healthy else "Attention Required"
        )
        embed.add_field(name="Issues", value=str(len(all_issues)))
        embed.add_field(
            name="Action Required",
            value="\n".join(
                f"{index}. {item}" for index, item in enumerate(all_issues, 1)
            )[:1024]
            or "No configuration issues detected.",
            inline=False,
        )
        embed.add_field(
            name="Warnings",
            value="\n".join(warnings)[:1024] or "No configuration warnings detected.",
            inline=False,
        )
        embed.add_field(
            name="Repair Scope",
            value=(
                "Only bot-managed categories, channels, permissions, panel "
                "registration and stored setup references are repaired. Member "
                "content and moderation records are never deleted."
            ),
            inline=False,
        )
        embed.set_footer(text=f"{config.BOT_NAME} | Configuration Doctor")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Governance(bot))
