import ast
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytz

import config
from cogs.inactivity import hours_since
from cogs.escalations import minutes_since
from utils.database import (
    claim_ticket,
    close_ticket,
    create_ticket_record,
    escalation_event_exists,
    get_available_staff_count,
    get_next_ticket_number,
    get_staff_availability,
    get_ticket_panels,
    register_ticket_panel,
    register_escalation_event,
    set_staff_availability,
    setup_database,
)
from utils.embeds import estimate_response_time, ticket_claimed_dm, ticket_closed_dm
from utils.logger import log_exception


class ConfigurationTests(unittest.TestCase):
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
        self.assertIn("#high-partnership-002", [field.value for field in claimed.fields])
        self.assertTrue(any("Attached to this message" in field.value for field in closed.fields))

    def test_escalation_age(self):
        zone = pytz.timezone(config.TIMEZONE)
        now = datetime.now(zone)
        value = (now - timedelta(minutes=31)).isoformat()
        self.assertGreaterEqual(minutes_since(value, now), 31)

    def test_ticket_coverage_timing(self):
        self.assertEqual(config.TICKET_REVIEW_ESCALATION_HOURS, 6)
        self.assertEqual(config.NO_RESPONSE_ESCALATION_HOURS, 24)
        source = Path(__file__).resolve().parents[1].joinpath("cogs", "escalations.py").read_text(encoding="utf-8")
        self.assertNotIn("Unclaimed Ticket Escalation", source)
        self.assertNotIn("Customer Response Overdue", source)
        self.assertIn('"six_hour_ticket_review"', source)

    def test_update_embed_uses_server_label(self):
        source = Path(__file__).resolve().parents[1].joinpath("cogs", "updates.py").read_text(encoding="utf-8")
        self.assertIn('name="Server", value=guild.name', source)
        self.assertNotIn('name="Environment", value=guild.name', source)

    def test_slash_commands_do_not_use_undefined_prefix_context(self):
        source = Path(__file__).resolve().parents[1].joinpath("cogs", "ban.py").read_text(encoding="utf-8")
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
        requirements = Path(__file__).resolve().parents[1].joinpath("requirements.txt").read_text(encoding="utf-8").splitlines()
        active = [line.strip() for line in requirements if line.strip()]
        self.assertTrue(active)
        self.assertTrue(all("==" in line for line in active))

    def test_slash_commands_use_only_global_registration(self):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
        self.assertNotIn("copy_global_to", source)
        self.assertIn("clear_commands", source)
        self.assertIn("global_synced =await bot.tree.sync()", source)

    def test_ticket_workflows_avoid_channel_name_and_topic_rate_limits(self):
        source = Path(__file__).resolve().parents[1].joinpath("views", "ticket_buttons.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        protected = {"claim", "update_priority"}
        invalid = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in protected:
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute) or call.func.attr != "edit":
                    continue
                keyword_names = {keyword.arg for keyword in call.keywords}
                if keyword_names.intersection({"name", "topic"}):
                    invalid.append(f"{node.name}:{call.lineno}")
        self.assertEqual(invalid, [])

    def test_component_interactions_are_not_logged_twice(self):
        source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
        self.assertIn("discord.InteractionType.component", source)
        self.assertIn("discord.InteractionType.modal_submit", source)

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
        source = Path(__file__).resolve().parents[1].joinpath("cogs", "diagnostics.py").read_text(encoding="utf-8")
        self.assertIn("Stale staff role IDs", source)
        self.assertIn("No configured staff role could be resolved", source)

    def test_deleted_moonlit_roles_are_not_configured(self):
        deleted = {
            1536279648470704149,
            1536279648470704150,
            1536279648470704152,
            1536279648470704153,
        }
        guild = config.GUILDS[1536279648428884058]
        configured = set(guild["OWNER_ROLES"]) | {
            guild["MOD_ROLE"],
            guild["TRIAL_MOD_ROLE"],
            guild["WARN_HISTORY_ROLE_ID"],
        }
        self.assertTrue(deleted.isdisjoint(configured))

    def test_unban_infractions_include_guild_scope(self):
        source = Path(__file__).resolve().parents[1].joinpath("views", "unban_buttons.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        unban_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "add_infraction"
        ]
        self.assertTrue(unban_calls)
        self.assertTrue(all(any(keyword.arg == "guild_id" for keyword in call.keywords) for call in unban_calls))


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.original_database = config.DATABASE
        config.DATABASE = str(Path(self.temp_directory.name) / "test.db")
        await setup_database()

    async def asyncTearDown(self):
        config.DATABASE = self.original_database
        self.temp_directory.cleanup()

    async def test_ticket_numbers_are_sequential_per_guild(self):
        guild_id = next(iter(config.GUILDS))
        first = await get_next_ticket_number(guild_id)
        second = await get_next_ticket_number(guild_id)
        self.assertEqual(second, first + 1)

    async def test_claim_and_close_are_single_transition_operations(self):
        guild_id = next(iter(config.GUILDS))
        await create_ticket_record(100, guild_id, 200, "Test", datetime.now().isoformat())
        self.assertTrue(await claim_ticket(100, 300, datetime.now().isoformat()))
        self.assertFalse(await claim_ticket(100, 301, datetime.now().isoformat()))
        self.assertTrue(await close_ticket(100, datetime.now().isoformat(), 300, "Done"))
        self.assertFalse(await close_ticket(100, datetime.now().isoformat(), 300, "Done"))

    async def test_reopen_restores_database_ticket_state(self):
        guild_id = next(iter(config.GUILDS))
        await create_ticket_record(101, guild_id, 201, "Test", datetime.now().isoformat())
        self.assertTrue(await close_ticket(101, datetime.now().isoformat(), 300, "Done"))
        from utils.database import get_ticket_record, reopen_ticket
        await reopen_ticket(101)
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

    async def test_staff_availability_and_panel_registration(self):
        guild_id = next(iter(config.GUILDS))
        await set_staff_availability(guild_id, 200, "Available", datetime.now().isoformat())
        await set_staff_availability(guild_id, 201, "Busy", datetime.now().isoformat())
        self.assertEqual(await get_available_staff_count(guild_id), 1)
        records = await get_staff_availability(guild_id)
        self.assertEqual(len(records), 2)
        await register_ticket_panel(guild_id, 300, 400, datetime.now().isoformat())
        panels = await get_ticket_panels(guild_id)
        self.assertEqual(panels, [{"channel_id": 300, "message_id": 400}])

    async def test_escalations_are_deduplicated(self):
        guild_id = next(iter(config.GUILDS))
        created_at = datetime.now().isoformat()
        self.assertTrue(await register_escalation_event(guild_id, 100, "unclaimed", created_at))
        self.assertFalse(await register_escalation_event(guild_id, 100, "unclaimed", created_at))
        self.assertTrue(await escalation_event_exists(guild_id, 100, "unclaimed"))

    async def test_repeated_errors_share_reference(self):
        references = []
        for _ in range(2):
            try:
                raise RuntimeError("Database test failure")
            except RuntimeError as error:
                references.append(log_exception("TEST", error, context="Grouped failure"))
        self.assertEqual(references[0], references[1])
        async with aiosqlite.connect(config.DATABASE) as database:
            cursor = await database.execute(
                "SELECT occurrence_count FROM error_events WHERE reference=?",
                (references[0],),
            )
            row = await cursor.fetchone()
        self.assertEqual(row[0], 2)


if __name__ == "__main__":
    unittest.main()
