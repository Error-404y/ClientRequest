import ast
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import discord
import pytz

import config
from cogs.afk import english_elapsed
from cogs.automod import AutoModeration, build_actions
from cogs.diagnostics import Diagnostics
from cogs.escalations import minutes_since
from cogs.inactivity import hours_since
from cogs.onboarding import (
    parse_ticket_options,
    public_install_permissions,
    resource_report,
    setup_permission_report,
)
from utils.database import (
    add_setup_admin,
    auto_assign_ticket,
    claim_ticket,
    clear_afk_status,
    close_ticket,
    create_ticket_record,
    escalation_event_exists,
    get_afk_statuses,
    get_available_staff_count,
    get_guild_settings,
    get_infraction_by_uuid,
    get_next_ticket_number,
    get_open_ticket_for_user,
    get_staff_availability,
    get_ticket_by_uuid,
    get_ticket_panels,
    process_afk_message,
    register_escalation_event,
    register_ticket_panel,
    remove_infraction_by_uuid,
    remove_setup_admin,
    remove_user_warning,
    reset_guild_settings,
    save_guild_settings,
    set_afk_status,
    set_staff_availability,
    set_ticket_label,
    set_ticket_priority,
    setup_database,
    toggle_ticket_claim,
)
from utils.embeds import (
    apply_ticket_label,
    estimate_response_time,
    ticket_claimed_dm,
    ticket_closed_dm,
)
from utils.logger import log_exception
from utils.permissions import can_manage_setup_admins, can_setup, is_owner


class ConfigurationTests(unittest.TestCase):
    def test_public_config_contains_no_embedded_discord_ids(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("config.py")
            .read_text(encoding="utf-8")
        )
        tree = ast.parse(source)
        embedded_ids = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and node.value >= 10**16
        ]
        self.assertEqual(embedded_ids, [])

    def test_obsolete_duplicate_modules_are_removed(self):
        root = Path(__file__).resolve().parents[1]
        obsolete = [
            root.joinpath("monitor_logging.py"),
            root.joinpath("cogs", "buttons.py"),
            root.joinpath("cogs", "logging.py"),
            root.joinpath("cogs", "owner.py"),
            root.joinpath("cogs", "panel.py"),
            root.joinpath("cogs", "setup.py"),
        ]
        self.assertTrue(all(not path.exists() for path in obsolete))

    def test_unknown_guild_is_rejected(self):
        with self.assertRaises(ValueError):
            config.get_guild_config(1)

    def test_inactivity_age_uses_warning_timestamp(self):
        zone = pytz.timezone(config.TIMEZONE)
        now = datetime.now(zone)
        value = (now - timedelta(hours=25)).isoformat()
        self.assertGreaterEqual(hours_since(value, now), 25)

    def test_response_time_estimates(self):
        self.assertEqual(estimate_response_time(0), "Currently unavailable")
        self.assertEqual(estimate_response_time(3), "10–20 minutes")
        self.assertEqual(estimate_response_time(5), "5–15 minutes")

    def test_ticket_lifecycle_direct_messages_use_structured_embeds(self):
        guild = SimpleNamespace(name="Moonlit Tokyo", icon=None)
        channel = SimpleNamespace(name="high-partnership-002")
        staff = SimpleNamespace(display_name="Error - 404 -")
        claimed = ticket_claimed_dm(guild, channel, staff)
        closed = ticket_closed_dm(guild, channel, staff, "Testing", True)
        self.assertEqual(claimed.title, "Your Ticket Is Now Under Review")
        self.assertEqual(closed.title, "Your Ticket Has Been Closed")
        self.assertIn("Testing", [field.value for field in closed.fields])
        self.assertIn(
            "#high-partnership-002", [field.value for field in claimed.fields]
        )
        self.assertTrue(
            any("Attached to this message" in field.value for field in closed.fields)
        )

    def test_escalation_age(self):
        zone = pytz.timezone(config.TIMEZONE)
        now = datetime.now(zone)
        value = (now - timedelta(minutes=31)).isoformat()
        self.assertGreaterEqual(minutes_since(value, now), 31)

    def test_ticket_coverage_timing(self):
        self.assertEqual(config.TICKET_REVIEW_ESCALATION_HOURS, 6)
        self.assertEqual(config.NO_RESPONSE_ESCALATION_HOURS, 24)
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "escalations.py")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("Unclaimed Ticket Escalation", source)
        self.assertNotIn("Customer Response Overdue", source)
        self.assertIn('"six_hour_ticket_review"', source)

    def test_update_embed_uses_server_label(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "updates.py")
            .read_text(encoding="utf-8")
        )
        self.assertIn('name="Server", value=guild.name', source)
        self.assertNotIn('name="Environment", value=guild.name', source)

    def test_public_setup_ticket_option_parser(self):
        self.assertEqual(
            parse_ticket_options("Support, Reports, support"), ["Support", "Reports"]
        )
        self.assertEqual(parse_ticket_options(None), config.DEFAULT_TICKET_OPTIONS)

    def test_setup_permission_report(self):
        permissions = SimpleNamespace(
            manage_channels=True,
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=False,
            read_message_history=True,
            manage_roles=False,
            moderate_members=True,
            manage_guild=True,
        )
        guild = SimpleNamespace(me=SimpleNamespace(guild_permissions=permissions))
        self.assertEqual(setup_permission_report(guild), ["Attach Files"])
        self.assertEqual(
            setup_permission_report(guild, needs_role_creation=True),
            ["Attach Files", "Manage Roles"],
        )

    def test_setup_access_owner_discord_admin_and_delegate(self):
        guild_id = 888888888888888888
        config.register_guild_config(guild_id, {"SETUP_ADMIN_USERS": [30]})
        guild = SimpleNamespace(id=guild_id, owner_id=10)
        owner = SimpleNamespace(
            id=10,
            guild=guild,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
        )
        administrator = SimpleNamespace(
            id=20,
            guild=guild,
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=False),
        )
        delegate = SimpleNamespace(
            id=30,
            guild=guild,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
        )
        manager = SimpleNamespace(
            id=35,
            guild=guild,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=True),
        )
        denied = SimpleNamespace(
            id=40,
            guild=guild,
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
        )
        self.assertTrue(can_setup(owner))
        self.assertTrue(can_setup(administrator))
        self.assertTrue(can_setup(delegate))
        self.assertFalse(can_setup(manager))
        self.assertFalse(can_setup(denied))
        self.assertTrue(can_manage_setup_admins(owner))
        self.assertFalse(can_manage_setup_admins(administrator))
        self.assertFalse(can_manage_setup_admins(delegate))
        from utils.permissions import is_staff

        self.assertTrue(is_staff(owner))
        self.assertTrue(is_staff(administrator))
        self.assertTrue(is_staff(delegate))
        config.remove_guild_config(guild_id)

    def test_discord_server_owner_has_owner_access_without_role(self):
        guild_id = 700000000000000001
        config.register_guild_config(guild_id, {"OWNER_ROLES": [999]})
        guild = SimpleNamespace(id=guild_id, owner_id=123)
        member = SimpleNamespace(id=123, guild=guild, roles=[])
        self.assertTrue(is_owner(member))
        config.remove_guild_config(guild_id)

    def test_runtime_prevents_duplicate_bot_instances(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("main.py")
            .read_text(encoding="utf-8")
        )
        self.assertIn("acquire_instance_lock()", source)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", source)

    def test_public_install_never_requests_administrator(self):
        permissions = public_install_permissions()
        self.assertFalse(permissions.administrator)
        self.assertTrue(permissions.manage_channels)
        self.assertTrue(permissions.embed_links)
        self.assertTrue(permissions.moderate_members)
        self.assertTrue(permissions.manage_guild)

    def test_afk_elapsed_time_is_english(self):
        now = datetime.now(pytz.utc)
        value = (now - timedelta(hours=10)).isoformat()
        self.assertEqual(english_elapsed(value, now), "10 hours ago")

    def test_automod_timeout_is_only_added_when_requested(self):
        without_timeout = build_actions(123, 0)
        with_timeout = build_actions(123, 10)
        self.assertNotIn(
            discord.AutoModRuleActionType.timeout,
            [action.type for action in without_timeout],
        )
        self.assertIn(
            discord.AutoModRuleActionType.timeout,
            [action.type for action in with_timeout],
        )

    def test_setup_resource_report_detects_missing_resources(self):
        guild = SimpleNamespace(
            get_channel=lambda resource_id: None, get_role=lambda role_id: None
        )
        issues = resource_report(
            guild,
            {
                "SETUP_COMPLETE": True,
                "TICKET_CATEGORY_ID": 1,
                "TICKET_ARCHIVE_CATEGORY_ID": 2,
                "TICKET_PANEL_CHANNEL_ID": 3,
                "LOG_CHANNEL_ID": 4,
                "MOD_ROLE": 5,
            },
        )
        self.assertEqual(len(issues), 5)

    def test_setup_creates_dedicated_ticket_system_location(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "onboarding.py")
            .read_text(encoding="utf-8")
        )
        self.assertIn('"ticket-system"', source)
        self.assertIn('name="ticket"', source)
        self.assertIn("category=panel_category", source)

    def test_help_is_a_public_embed(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "onboarding.py")
            .read_text(encoding="utf-8")
        )
        help_block = source.split('name="help"', 1)[1].split('name="invite"', 1)[0]
        self.assertIn("send_message(embed=embed)", help_block)
        self.assertNotIn("ephemeral=True", help_block)

    def test_public_server_isolation_guards(self):
        updates_source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "updates.py")
            .read_text(encoding="utf-8")
        )
        find_source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "findz.py")
            .read_text(encoding="utf-8")
        )
        dropdown_source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("views", "dropdown.py")
            .read_text(encoding="utf-8")
        )
        transcript_source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "transcript.py")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("for guild in self.bot.guilds", updates_source)
        self.assertIn("get_infraction_by_uuid(uuid_value, guild_id)", find_source)
        self.assertIn("get_ticket_by_uuid(uuid_value, guild_id)", find_source)
        self.assertNotIn("config .SETUP_USER_ID", dropdown_source)
        self.assertNotIn("config.SETUP_USER_ID", transcript_source)

    def test_public_release_documents_exist(self):
        root = Path(__file__).resolve().parents[1]
        privacy = root.joinpath("PRIVACY_POLICY.md").read_text(encoding="utf-8")
        terms = root.joinpath("TERMS_OF_SERVICE.md").read_text(encoding="utf-8")
        self.assertIn("# Privacy Policy", privacy)
        self.assertIn("Storage and Retention", privacy)
        self.assertIn("# Terms of Service", terms)
        self.assertIn("Acceptable Use", terms)

    def test_slash_commands_do_not_use_undefined_prefix_context(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "ban.py")
            .read_text(encoding="utf-8")
        )
        tree = ast.parse(source)
        invalid = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [ast.unparse(item) for item in node.decorator_list]
            if not any("app_commands.command" in item for item in decorators):
                continue
            parameters = {argument.arg for argument in node.args.args}
            names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
            if "ctx" in names and "ctx" not in parameters:
                invalid.append(node.name)
        self.assertEqual(invalid, [])

    def test_exception_handlers_never_hide_failures(self):
        root = Path(__file__).resolve().parents[1]
        invalid = []
        paths = [
            root.joinpath("main.py"),
            *root.joinpath("cogs").glob("*.py"),
            *root.joinpath("views").glob("*.py"),
            *root.joinpath("utils").glob("*.py"),
        ]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    invalid.append(f"{path.name}:{node.lineno}:pass")
                has_print = any(
                    isinstance(item, ast.Call)
                    and isinstance(item.func, ast.Name)
                    and item.func.id == "print"
                    for item in ast.walk(node)
                )
                if has_print:
                    invalid.append(f"{path.name}:{node.lineno}:print")
        self.assertEqual(invalid, [])

    def test_embed_helpers_are_not_shadowed_by_exception_variables(self):
        root = Path(__file__).resolve().parents[1]
        invalid = []
        paths = [
            *root.joinpath("cogs").glob("*.py"),
            *root.joinpath("views").glob("*.py"),
            *root.joinpath("utils").glob("*.py"),
        ]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            embed_helpers = {
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "utils.embeds"
                for alias in node.names
            }
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                exception_names = {
                    handler.name
                    for handler in ast.walk(node)
                    if isinstance(handler, ast.ExceptHandler) and handler.name
                }
                called_names = {
                    call.func.id
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                }
                for name in embed_helpers & exception_names & called_names:
                    invalid.append(f"{path.name}:{node.name}:{name}")
        self.assertEqual(invalid, [])

    def test_interactive_views_use_reliability_handler(self):
        root = Path(__file__).resolve().parents[1]
        invalid = []
        for path in root.joinpath("views").glob("*.py"):
            if path.name in {"__init__.py", "base.py"}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {ast.unparse(base).replace(" ", "") for base in node.bases}
                if bases.intersection({"View", "discord.ui.View"}):
                    invalid.append(f"{path.name}:{node.name}")
        self.assertEqual(invalid, [])

    def test_runtime_dependencies_are_pinned(self):
        requirements = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("requirements.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        active = [line.strip() for line in requirements if line.strip()]
        self.assertTrue(active)
        self.assertTrue(all("==" in line for line in active))

    def test_slash_commands_use_only_global_registration(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("main.py")
            .read_text(encoding="utf-8")
        )
        self.assertNotIn("copy_global_to", source)
        self.assertNotIn("clear_commands", source)
        self.assertIn("global_synced = await bot.tree.sync()", source)

    def test_ticket_workflows_avoid_channel_name_and_topic_rate_limits(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("views", "ticket_buttons.py")
            .read_text(encoding="utf-8")
        )
        tree = ast.parse(source)
        protected = {"claim", "update_priority"}
        invalid = []
        for node in ast.walk(tree):
            if (
                not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                or node.name not in protected
            ):
                continue
            for call in ast.walk(node):
                if (
                    not isinstance(call, ast.Call)
                    or not isinstance(call.func, ast.Attribute)
                    or call.func.attr != "edit"
                ):
                    continue
                keyword_names = {keyword.arg for keyword in call.keywords}
                if keyword_names.intersection({"name", "topic"}):
                    invalid.append(f"{node.name}:{call.lineno}")
        self.assertEqual(invalid, [])

    def test_component_interactions_are_not_logged_twice(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("main.py")
            .read_text(encoding="utf-8")
        )
        self.assertIn("discord.InteractionType.component", source)
        self.assertIn("discord.InteractionType.modal_submit", source)

    def test_operations_console_uses_active_server_count(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("main.py")
            .read_text(encoding="utf-8")
        )
        self.assertIn('console_row("ACTIVE SERVERS", str(len(bot.guilds)))', source)
        self.assertNotIn("PRIMARY SERVER", source)

    def test_runtime_uses_one_configured_timezone(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(config.TIMEZONE, "Europe/Athens")
        invalid = []
        legacy_timezone = "/".join(("Europe", "Berlin"))
        for path in root.rglob("*.py"):
            if any(part in {".venv", "venv", "__pycache__"} for part in path.parts):
                continue
            if legacy_timezone in path.read_text(encoding="utf-8"):
                invalid.append(str(path.relative_to(root)))
        self.assertEqual(invalid, [])

    def test_stale_roles_are_reported_as_configuration_warnings(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "diagnostics.py")
            .read_text(encoding="utf-8")
        )
        self.assertIn("Stale staff role IDs", source)
        self.assertIn("No configured staff role could be resolved", source)

    def test_unban_infractions_include_guild_scope(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("views", "unban_buttons.py")
            .read_text(encoding="utf-8")
        )
        tree = ast.parse(source)
        unban_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "add_infraction"
        ]
        self.assertTrue(unban_calls)
        self.assertTrue(
            all(
                any(keyword.arg == "guild_id" for keyword in call.keywords)
                for call in unban_calls
            )
        )

    def test_new_operational_modules_are_loaded(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("main.py")
            .read_text(encoding="utf-8")
        )
        self.assertIn('"cogs.moderation"', source)
        self.assertIn('"cogs.afk"', source)
        self.assertIn('"cogs.automod"', source)
        self.assertIn("auto_moderation_configuration = True", source)
        self.assertIn("auto_moderation_execution = True", source)

    def test_new_interaction_responses_use_embeds(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("moderation.py", "afk.py", "automod.py"):
            source = root.joinpath("cogs", filename).read_text(encoding="utf-8")
            self.assertNotIn('send_message(\n                "', source)
            self.assertNotIn('followup.send(\n                "', source)

    def test_all_interaction_text_responses_use_embeds(self):
        root = Path(__file__).resolve().parents[1]
        invalid = []
        for directory in ("cogs", "views"):
            for path in root.joinpath(directory).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(
                        node.func, ast.Attribute
                    ):
                        continue
                    if node.func.attr not in {"send", "send_message", "edit_message"}:
                        continue
                    owner = ast.unparse(node.func.value)
                    if (
                        "interaction.response" not in owner
                        and "interaction.followup" not in owner
                    ):
                        continue
                    has_text = bool(node.args) or any(
                        keyword.arg == "content"
                        and not (
                            isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is None
                        )
                        for keyword in node.keywords
                    )
                    has_embed = any(
                        keyword.arg == "embed"
                        and not (
                            isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is None
                        )
                        for keyword in node.keywords
                    )
                    if has_text and not has_embed:
                        invalid.append(f"{path.name}:{node.lineno}")
        self.assertEqual(invalid, [])

    def test_afk_command_does_not_require_ticket_setup(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "afk.py")
            .read_text(encoding="utf-8")
        )
        command_block = source.split("async def setafkz", 1)[1].split(
            "@commands.Cog.listener", 1
        )[0]
        self.assertNotIn("is_guild_configured", command_block)


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.original_database = config.DATABASE
        self.original_guilds = {
            guild_id: dict(settings) for guild_id, settings in config.GUILDS.items()
        }
        config.DATABASE = str(Path(self.temp_directory.name) / "test.db")
        await setup_database()
        self.guild_id = 600000000000000001
        await save_guild_settings(self.guild_id, {"NAME": "Database Test Server"})

    async def asyncTearDown(self):
        config.DATABASE = self.original_database
        config.replace_guild_configs(self.original_guilds)
        self.temp_directory.cleanup()

    async def test_ticket_numbers_are_sequential_per_guild(self):
        guild_id = self.guild_id
        first = await get_next_ticket_number(guild_id)
        second = await get_next_ticket_number(guild_id)
        self.assertEqual(second, first + 1)

    async def test_claim_and_close_are_single_transition_operations(self):
        guild_id = self.guild_id
        await create_ticket_record(
            100, guild_id, 200, "Test", datetime.now().isoformat()
        )
        self.assertTrue(await claim_ticket(100, 300, datetime.now().isoformat()))
        self.assertFalse(await claim_ticket(100, 301, datetime.now().isoformat()))
        self.assertTrue(
            await close_ticket(100, datetime.now().isoformat(), 300, "Done")
        )
        self.assertFalse(
            await close_ticket(100, datetime.now().isoformat(), 300, "Done")
        )
        self.assertFalse(await set_ticket_priority(100, "High"))

    async def test_open_ticket_lookup_is_scoped_to_server_and_user(self):
        await create_ticket_record(
            109, self.guild_id, 209, "Support", datetime.now().isoformat()
        )
        record = await get_open_ticket_for_user(self.guild_id, 209)
        self.assertEqual(record["channel_id"], 109)
        self.assertIsNone(await get_open_ticket_for_user(self.guild_id, 210))
        self.assertIsNone(await get_open_ticket_for_user(self.guild_id + 1, 209))
        self.assertTrue(await set_ticket_priority(109, "High"))

    async def test_ticket_claim_toggle_has_persistent_cooldown(self):
        await create_ticket_record(
            102, self.guild_id, 202, "Test", datetime.now().isoformat()
        )
        started = datetime.now(pytz.utc)
        claimed = await toggle_ticket_claim(102, 300, started.isoformat())
        blocked = await toggle_ticket_claim(
            102, 301, (started + timedelta(seconds=1)).isoformat()
        )
        released = await toggle_ticket_claim(
            102, 301, (started + timedelta(seconds=3)).isoformat()
        )
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(blocked["status"], "cooldown")
        self.assertEqual(released["status"], "unclaimed")
        self.assertEqual(released["previous_claimed_by"], 300)

    async def test_afk_status_is_persistent_and_clearable(self):
        set_at = datetime.now(pytz.utc).isoformat()
        await set_afk_status(self.guild_id, 200, "Working", set_at)
        records = await get_afk_statuses(self.guild_id, [200, 201])
        self.assertEqual(
            records, [{"user_id": 200, "reason": "Working", "set_at": set_at}]
        )
        self.assertTrue(await clear_afk_status(self.guild_id, 200))
        self.assertFalse(await clear_afk_status(self.guild_id, 200))

    async def test_afk_message_processing_clears_author_and_finds_mentions(self):
        set_at = datetime.now(pytz.utc).isoformat()
        await set_afk_status(self.guild_id, 210, "Away", set_at)
        await set_afk_status(self.guild_id, 211, "Working", set_at)
        removed, records = await process_afk_message(self.guild_id, 210, [211])
        self.assertTrue(removed)
        self.assertEqual(records[0]["user_id"], 211)
        self.assertEqual(await get_afk_statuses(self.guild_id, [210]), [])

    async def test_auto_assignment_uses_lowest_active_workload(self):
        now = datetime.now(pytz.utc).isoformat()
        await create_ticket_record(103, self.guild_id, 203, "Test", now)
        await create_ticket_record(104, self.guild_id, 204, "Test", now)
        await create_ticket_record(105, self.guild_id, 205, "Test", now)
        self.assertEqual(
            await auto_assign_ticket(103, self.guild_id, [300, 301], now), 300
        )
        self.assertEqual(
            await auto_assign_ticket(104, self.guild_id, [300, 301], now), 301
        )
        self.assertEqual(
            await auto_assign_ticket(105, self.guild_id, [300, 301], now), 300
        )

    async def test_automod_reuses_existing_rule_without_editing_it(self):
        class ExistingRule:
            def __init__(self):
                self.received = None

            async def edit(self, **kwargs):
                self.received = kwargs
                return self

        existing_rule = ExistingRule()
        cog = AutoModeration(None)
        actions = build_actions(123, 10)
        result, adopted = await cog.upsert_rule(
            SimpleNamespace(),
            [],
            "Managed Rule",
            discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.mention_spam,
                mention_limit=5,
            ),
            actions,
            adopt_rule=existing_rule,
        )
        self.assertIs(result, existing_rule)
        self.assertTrue(adopted)
        self.assertIsNone(existing_rule.received)

    async def test_automod_single_rule_trigger_is_reused_before_update(self):
        trigger = discord.AutoModTrigger(
            type=discord.AutoModRuleTriggerType.mention_spam,
            mention_limit=5,
        )
        existing_rule = SimpleNamespace(
            name="Server Mention Protection",
            trigger=trigger,
        )

        class Guild:
            async def fetch_automod_rules(self):
                return [existing_rule]

        cog = AutoModeration(None)
        result, reused = await cog.configure_rule(
            Guild(), "Managed Mentions", trigger, build_actions()
        )
        self.assertIs(result, existing_rule)
        self.assertTrue(reused)

    async def test_reopen_restores_database_ticket_state(self):
        guild_id = self.guild_id
        await create_ticket_record(
            101, guild_id, 201, "Test", datetime.now().isoformat()
        )
        self.assertTrue(
            await close_ticket(101, datetime.now().isoformat(), 300, "Done")
        )
        from utils.database import get_ticket_record, reopen_ticket

        self.assertTrue(await reopen_ticket(101))
        self.assertFalse(await reopen_ticket(101))
        record = await get_ticket_record(101)
        self.assertEqual(record["status"], "open")
        self.assertIsNone(record["closed_at"])
        self.assertIsNone(record["closed_by"])
        self.assertIsNone(record["close_reason"])

    async def test_warning_timestamp_column_exists(self):
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute("PRAGMA table_info(tickets)")
            names = {row[1] for row in await cursor.fetchall()}
        self.assertIn("warned_at", names)

    async def test_ticket_label_is_persistent_and_open_ticket_scoped(self):
        await create_ticket_record(
            109, self.guild_id, 209, "Technical", datetime.now().isoformat()
        )
        self.assertTrue(await set_ticket_label(109, "Urgent"))
        from utils.database import get_ticket_record

        record = await get_ticket_record(109)
        self.assertEqual(record["label"], "Urgent")
        self.assertTrue(
            await close_ticket(109, datetime.now().isoformat(), 309, "Resolved")
        )
        self.assertFalse(await set_ticket_label(109, "Billing"))
        self.assertEqual((await get_ticket_record(109))["label"], "Urgent")

    def test_ticket_label_updates_main_embed_field(self):
        embed = discord.Embed(title="Support Ticket")
        apply_ticket_label(embed, "Technical")
        apply_ticket_label(embed, "Escalated")
        fields = [field for field in embed.fields if field.name == "Ticket Label"]
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].value, "Escalated")

    async def test_legacy_infraction_uuid_is_repaired(self):
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "INSERT INTO infractions(uuid, guild_id, user_id, moderator_id, action_type, reason, timestamp) VALUES(NULL, ?, ?, ?, ?, ?, ?)",
                (
                    self.guild_id,
                    200,
                    300,
                    "WARN",
                    "Legacy record",
                    datetime.now().isoformat(),
                ),
            )
            row_id = cursor.lastrowid
            await database.commit()
        await setup_database()
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT uuid FROM infractions WHERE id=?", (row_id,)
            )
            repaired_uuid = (await cursor.fetchone())[0]
        self.assertTrue(repaired_uuid.startswith(f"G{self.guild_id}-"))

    async def test_staff_availability_and_panel_registration(self):
        guild_id = self.guild_id
        await set_staff_availability(
            guild_id, 200, "Available", datetime.now().isoformat()
        )
        await set_staff_availability(guild_id, 201, "Busy", datetime.now().isoformat())
        self.assertEqual(await get_available_staff_count(guild_id), 1)
        records = await get_staff_availability(guild_id)
        self.assertEqual(len(records), 2)
        await register_ticket_panel(guild_id, 300, 400, datetime.now().isoformat())
        panels = await get_ticket_panels(guild_id)
        self.assertEqual(panels, [{"channel_id": 300, "message_id": 400}])

    async def test_escalations_are_deduplicated(self):
        guild_id = self.guild_id
        created_at = datetime.now().isoformat()
        self.assertTrue(
            await register_escalation_event(guild_id, 100, "unclaimed", created_at)
        )
        self.assertFalse(
            await register_escalation_event(guild_id, 100, "unclaimed", created_at)
        )
        self.assertTrue(await escalation_event_exists(guild_id, 100, "unclaimed"))

    async def test_public_guild_settings_are_persistent(self):
        guild_id = 999999999999999999
        saved = await save_guild_settings(
            guild_id,
            {
                "NAME": "Public Test Server",
                "TICKET_CATEGORY_ID": 11,
                "TICKET_PANEL_CHANNEL_ID": 12,
                "TICKET_ARCHIVE_CATEGORY_ID": 13,
                "LOG_CHANNEL_ID": 14,
                "OWNER_ROLES": [15],
                "MOD_ROLE": 15,
                "TRIAL_MOD_ROLE": 15,
                "WARN_HISTORY_ROLE_ID": 15,
                "TICKET_OPTIONS": ["Support", "Reports"],
                "SETUP_COMPLETE": True,
                "WELCOME_SENT": True,
                "AUTO_ASSIGN_TICKETS": True,
            },
        )
        self.assertTrue(saved["SETUP_COMPLETE"])
        self.assertTrue(saved["AUTO_ASSIGN_TICKETS"])
        self.assertEqual(
            (await get_guild_settings(guild_id))["TICKET_OPTIONS"],
            ["Support", "Reports"],
        )
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT name, setup_complete FROM guild_settings WHERE guild_id=?",
                (guild_id,),
            )
            self.assertEqual(await cursor.fetchone(), ("Public Test Server", 1))
        reset = await reset_guild_settings(guild_id)
        self.assertFalse(reset["SETUP_COMPLETE"])
        self.assertEqual(reset["TICKET_PANEL_CHANNEL_ID"], 0)

    async def test_delegated_setup_admins_are_persistent(self):
        guild_id = 777777777777777777
        added, settings = await add_setup_admin(guild_id, 123456789012345678)
        self.assertTrue(added)
        self.assertEqual(settings["SETUP_ADMIN_USERS"], [123456789012345678])
        duplicate, settings = await add_setup_admin(guild_id, 123456789012345678)
        self.assertFalse(duplicate)
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT user_id FROM guild_setup_admins WHERE guild_id=?", (guild_id,)
            )
            self.assertEqual(await cursor.fetchall(), [(123456789012345678,)])
        removed, settings = await remove_setup_admin(guild_id, 123456789012345678)
        self.assertTrue(removed)
        self.assertEqual(settings["SETUP_ADMIN_USERS"], [])

    async def test_uuid_records_are_isolated_by_guild(self):
        ticket_uuid = await create_ticket_record(
            901, 1001, 2001, "Support", datetime.now().isoformat()
        )
        self.assertIsNotNone(await get_ticket_by_uuid(ticket_uuid, 1001))
        self.assertIsNone(await get_ticket_by_uuid(ticket_uuid, 1002))

        infraction_uuid = "G1001-isolation-test"
        async with aiosqlite.connect(config.DATABASE) as database:
            await database.execute(
                "INSERT INTO infractions(uuid, guild_id, user_id, moderator_id, action_type, reason, timestamp) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    infraction_uuid,
                    1001,
                    2001,
                    3001,
                    "Warning",
                    "Isolation test",
                    datetime.now().isoformat(),
                ),
            )
            await database.commit()
        self.assertIsNotNone(await get_infraction_by_uuid(infraction_uuid, 1001))
        self.assertIsNone(await get_infraction_by_uuid(infraction_uuid, 1002))
        self.assertIsNone(await remove_infraction_by_uuid(infraction_uuid, 1002))
        self.assertIsNotNone(await get_infraction_by_uuid(infraction_uuid, 1001))

    async def test_ambiguous_partial_infraction_uuid_is_rejected(self):
        values = (
            "G600000000000000001-shared-prefix-one",
            "G600000000000000001-shared-prefix-two",
        )
        async with aiosqlite.connect(config.DATABASE) as database:
            for value in values:
                await database.execute(
                    "INSERT INTO infractions(uuid, guild_id, user_id, moderator_id, action_type, reason, timestamp) VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        value,
                        self.guild_id,
                        200,
                        300,
                        "WARN",
                        "Ambiguity test",
                        datetime.now().isoformat(),
                    ),
                )
            await database.commit()
        partial = f"G{self.guild_id}-shared-prefix"
        self.assertIsNone(await get_infraction_by_uuid(partial, self.guild_id))
        self.assertIsNone(await remove_infraction_by_uuid(partial, self.guild_id))
        self.assertEqual(
            (await get_infraction_by_uuid(values[0], self.guild_id))["uuid"],
            values[0],
        )

    async def test_warning_removal_never_falls_back_to_another_guild(self):
        async with aiosqlite.connect(config.DATABASE) as database:
            await database.execute(
                "INSERT INTO infractions(uuid, guild_id, user_id, moderator_id, action_type, reason, timestamp) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    "G2002-warning",
                    2002,
                    501,
                    601,
                    "WARN",
                    "Isolation test",
                    datetime.now().isoformat(),
                ),
            )
            await database.commit()
        count, records = await remove_user_warning(
            501, guild_id=2001, warn_id="G2002-warning"
        )
        self.assertEqual((count, records), (0, []))
        self.assertIsNotNone(await get_infraction_by_uuid("G2002-warning", 2002))
        count, records = await remove_user_warning(
            999, guild_id=2002, warn_id="G2002-warning"
        )
        self.assertEqual((count, records), (0, []))
        self.assertIsNotNone(await get_infraction_by_uuid("G2002-warning", 2002))

    async def test_repeated_errors_share_reference(self):
        references = []
        for _ in range(2):
            try:
                raise RuntimeError("Database test failure")
            except RuntimeError as error:
                references.append(
                    log_exception("TEST", error, context="Grouped failure")
                )
        self.assertEqual(references[0], references[1])
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT occurrence_count FROM error_events WHERE reference=?",
                (references[0],),
            )
            row = await cursor.fetchone()
        self.assertEqual(row[0], 2)

    async def test_diagnostics_are_isolated_by_guild(self):
        references = []
        for guild_id in (self.guild_id, self.guild_id + 1):
            try:
                raise RuntimeError("Scoped diagnostic failure")
            except RuntimeError as error:
                references.append(
                    log_exception(
                        "TEST", error, guild=guild_id, context="Scoped diagnostic"
                    )
                )
        self.assertNotEqual(references[0], references[1])
        diagnostics = Diagnostics.__new__(Diagnostics)
        records = await diagnostics.recent_errors(self.guild_id, limit=10)
        self.assertEqual([record["reference"] for record in records], [references[0]])
        self.assertIsNone(await diagnostics.find_error(references[1], self.guild_id))

    async def test_numeric_user_id_is_not_recorded_as_guild_id(self):
        try:
            raise RuntimeError("User-only diagnostic failure")
        except RuntimeError as error:
            reference = log_exception(
                "TEST", error, user=123456789, context="Numeric user context"
            )
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT guild_id, user_id FROM error_events WHERE reference=?",
                (reference,),
            )
            location = await cursor.fetchone()
        self.assertEqual(location, (None, 123456789))


if __name__ == "__main__":
    unittest.main()
