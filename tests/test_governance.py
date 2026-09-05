import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from utils.database import (
    add_appeal_details,
    add_approval_details,
    add_infraction,
    cancel_pending_approval_requests,
    claim_approval_execution,
    claim_moderation_appeal,
    complete_approval_request,
    complete_moderation_appeal,
    create_approval_request,
    create_moderation_appeal,
    create_ticket_record,
    delete_ticket_form,
    expire_approval_requests,
    fail_stale_appeal_reviews,
    fail_stale_approval_executions,
    get_approval_request,
    get_approval_requests,
    get_approval_rule,
    get_moderation_appeal,
    get_risk_records,
    get_ticket_by_uuid,
    get_ticket_form,
    get_ticket_forms,
    set_approval_rule,
    set_ticket_form,
    setup_database,
    update_moderation_appeal,
    vote_approval_request,
)
from utils.governance import APPROVAL_ACTIONS, calculate_risk, parse_ticket_questions


class GovernanceLogicTests(unittest.TestCase):
    def test_ticket_question_validation(self):
        questions = parse_ticket_questions(
            ("Account ID", "Evidence", "Additional context", None, None), 2
        )
        self.assertEqual(
            questions,
            [
                {"label": "Account ID", "required": True},
                {"label": "Evidence", "required": True},
                {"label": "Additional context", "required": False},
            ],
        )
        with self.assertRaises(ValueError):
            parse_ticket_questions((None, None), 1)
        with self.assertRaises(ValueError):
            parse_ticket_questions(("Evidence", " evidence "), 1)

    def test_risk_levels_are_deterministic(self):
        now = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

        def record(action, days=0):
            timestamp = (now - timedelta(days=days)).strftime("%d/%m/%Y - %H:%M")
            return {"action_type": action, "timestamp": timestamp}

        self.assertEqual(calculate_risk([], now)["level"], 1)
        self.assertEqual(calculate_risk([record("WARN")], now)["level"], 2)
        self.assertEqual(calculate_risk([record("TIMEOUT")], now)["level"], 2)
        self.assertEqual(calculate_risk([record("KICK")], now)["level"], 3)
        self.assertEqual(calculate_risk([record("BAN")], now)["level"], 4)
        self.assertEqual(
            calculate_risk([record("BAN"), record("BAN")], now)["level"], 5
        )
        recent = calculate_risk([record("WARN", 1)], now)["score"]
        old = calculate_risk([record("WARN", 180)], now)["score"]
        self.assertGreater(recent, old)

    def test_all_moderation_execution_paths_reference_approval_queue(self):
        root = Path(__file__).resolve().parents[1]
        expected = {
            "cogs/ban.py": ("WARN", "WARNING_REMOVE", "INFRACTION_REMOVE"),
            "cogs/moderation.py": ("TIMEOUT",),
            "views/ban_buttons.py": ("BAN",),
            "views/kick_buttons.py": ("KICK",),
            "views/unban_buttons.py": ("UNBAN",),
        }
        for filename, actions in expected.items():
            source = root.joinpath(filename).read_text(encoding="utf-8")
            self.assertIn("queue_moderation_approval", source)
            for action in actions:
                self.assertIn(f'"{action}"', source)
        self.assertEqual(
            set(APPROVAL_ACTIONS),
            {item for values in expected.values() for item in values},
        )

    def test_custom_form_precedes_ticket_defer(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("views", "dropdown.py")
            .read_text(encoding="utf-8")
        )
        callback = source.split("async def callback", 1)[1].split(
            "async def create_with_lock", 1
        )[0]
        self.assertLess(callback.index("get_ticket_form"), callback.index("defer"))
        self.assertIn("send_modal", callback)

    def test_ticket_creation_locks_are_released_after_use(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("views", "dropdown.py")
            .read_text(encoding="utf-8")
        )
        lock_flow = source.split("async def create_with_lock", 1)[1].split(
            "async def create_ticket", 1
        )[0]
        self.assertIn('entry["users"] += 1', lock_flow)
        self.assertIn('entry["users"] -= 1', lock_flow)
        self.assertIn("locks.pop(key, None)", lock_flow)

    def test_governance_module_is_loaded(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("main.py")
            .read_text(encoding="utf-8")
        )
        self.assertIn('"cogs.governance"', source)

    def test_configuration_doctor_passes_server_settings(self):
        source = (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("cogs", "governance.py")
            .read_text(encoding="utf-8")
        )
        doctor = source.split("async def doctorz", 1)[1]
        self.assertIn("resource_report(interaction.guild, settings)", doctor)


class GovernanceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_database = config.DATABASE
        self.original_guilds = dict(config.GUILDS)
        self.temp_directory = tempfile.TemporaryDirectory()
        config.DATABASE = str(Path(self.temp_directory.name) / "governance.db")
        await setup_database()
        self.guild_id = 700000000000000001
        self.other_guild_id = 700000000000000002

    async def asyncTearDown(self):
        config.DATABASE = self.original_database
        config.replace_guild_configs(self.original_guilds)
        self.temp_directory.cleanup()

    async def test_ticket_forms_are_server_scoped_and_replaceable(self):
        now = datetime.now(timezone.utc).isoformat()
        first = [{"label": "Account ID", "required": True}]
        second = [{"label": "Evidence", "required": False}]
        await set_ticket_form(self.guild_id, "Issues", first, 10, now)
        await set_ticket_form(self.other_guild_id, "Issues", second, 20, now)
        self.assertEqual(
            (await get_ticket_form(self.guild_id, "issues"))["questions"], first
        )
        self.assertEqual(
            (await get_ticket_form(self.other_guild_id, "Issues"))["questions"],
            second,
        )
        await set_ticket_form(self.guild_id, "Issues", second, 10, now)
        self.assertEqual(len(await get_ticket_forms(self.guild_id)), 1)
        self.assertEqual(
            (await get_ticket_form(self.guild_id, "Issues"))["questions"], second
        )
        self.assertTrue(await delete_ticket_form(self.guild_id, "ISSUES"))
        self.assertIsNone(await get_ticket_form(self.guild_id, "Issues"))
        self.assertIsNotNone(await get_ticket_form(self.other_guild_id, "Issues"))

    async def test_ticket_form_answers_are_persistent(self):
        answers = [
            {"question": "Account ID", "answer": "ABC-123"},
            {"question": "Evidence", "answer": "https://example.invalid/evidence"},
        ]
        ticket_uuid = await create_ticket_record(
            100,
            self.guild_id,
            200,
            "Issues",
            datetime.now(timezone.utc).isoformat(),
            answers,
        )
        ticket = await get_ticket_by_uuid(ticket_uuid, self.guild_id)
        self.assertEqual(ticket["form_response"], answers)
        self.assertIsNone(await get_ticket_by_uuid(ticket_uuid, self.other_guild_id))

    async def test_approval_rules_are_server_and_action_scoped(self):
        now = datetime.now(timezone.utc).isoformat()
        await set_approval_rule(
            self.guild_id, "BAN", True, 50, 2, 60, 120, False, 10, now
        )
        await set_approval_rule(
            self.other_guild_id, "BAN", True, 51, 1, 61, 60, True, 11, now
        )
        first = await get_approval_rule(self.guild_id, "BAN")
        second = await get_approval_rule(self.other_guild_id, "BAN")
        self.assertEqual(first["required_approvals"], 2)
        self.assertFalse(first["senior_bypass"])
        self.assertEqual(second["required_approvals"], 1)
        self.assertTrue(second["senior_bypass"])
        self.assertIsNone(await get_approval_rule(self.guild_id, "WARN"))

    async def test_approval_requires_independent_votes_and_executes_once(self):
        created_at = datetime.now(timezone.utc).isoformat()
        created = await create_approval_request(
            self.guild_id,
            "BAN",
            100,
            200,
            "Target",
            "Repeated abuse",
            {},
            2,
            300,
            60,
            created_at,
        )
        self_vote = await vote_approval_request(
            created["request_uuid"],
            self.guild_id,
            100,
            "APPROVE",
            "",
            datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(self_vote["status"], "SELF_APPROVAL")
        first = await vote_approval_request(
            created["request_uuid"],
            self.guild_id,
            101,
            "APPROVE",
            "",
            datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(first["status"], "PENDING")
        duplicate = await vote_approval_request(
            created["request_uuid"],
            self.guild_id,
            101,
            "APPROVE",
            "",
            datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(duplicate["approval_count"], 1)
        second = await vote_approval_request(
            created["request_uuid"],
            self.guild_id,
            102,
            "APPROVE",
            "",
            datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(second["status"], "APPROVED")
        claims = await asyncio.gather(
            claim_approval_execution(created["request_uuid"], self.guild_id),
            claim_approval_execution(created["request_uuid"], self.guild_id),
        )
        self.assertEqual(sum(claims), 1)
        self.assertTrue(
            await complete_approval_request(
                created["request_uuid"],
                self.guild_id,
                "EXECUTED",
                "G-result",
                "Completed",
            )
        )
        stored = await get_approval_request(created["request_uuid"], self.guild_id)
        self.assertEqual(stored["status"], "EXECUTED")
        self.assertEqual(stored["result_uuid"], "G-result")
        self.assertIsNone(
            await get_approval_request(created["request_uuid"], self.other_guild_id)
        )

    async def test_approval_expiry_and_details_flow(self):
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        expired = await create_approval_request(
            self.guild_id,
            "WARN",
            100,
            200,
            "Target",
            "Reason",
            {},
            1,
            300,
            5,
            created_at,
        )
        result = await vote_approval_request(
            expired["request_uuid"],
            self.guild_id,
            101,
            "APPROVE",
            "",
            datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(result["status"], "EXPIRED")

        active = await create_approval_request(
            self.guild_id,
            "WARN",
            100,
            200,
            "Target",
            "Reason",
            {},
            1,
            300,
            60,
            datetime.now(timezone.utc).isoformat(),
        )
        details = await vote_approval_request(
            active["request_uuid"],
            self.guild_id,
            101,
            "DETAILS",
            "Provide a message link",
            datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(details["status"], "NEEDS_DETAILS")
        self.assertFalse(
            await add_approval_details(
                active["request_uuid"],
                self.guild_id,
                999,
                "Wrong requester",
                datetime.now(timezone.utc).isoformat(),
            )
        )
        self.assertTrue(
            await add_approval_details(
                active["request_uuid"],
                self.guild_id,
                100,
                "https://example.invalid/message",
                datetime.now(timezone.utc).isoformat(),
            )
        )
        stored = await get_approval_request(active["request_uuid"], self.guild_id)
        self.assertEqual(stored["status"], "PENDING")
        self.assertIn("Additional details", stored["reason"])

    async def test_interrupted_approval_execution_is_failed_safely(self):
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        request = await create_approval_request(
            self.guild_id,
            "BAN",
            100,
            200,
            "Target",
            "Reason",
            {},
            1,
            300,
            60,
            created_at,
        )
        approved = await vote_approval_request(
            request["request_uuid"],
            self.guild_id,
            101,
            "APPROVE",
            "Approved",
            (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
        )
        self.assertEqual(approved["status"], "APPROVED")
        self.assertTrue(
            await claim_approval_execution(request["request_uuid"], self.guild_id)
        )
        now = datetime.now(timezone.utc)
        self.assertEqual(
            await fail_stale_approval_executions(
                (now - timedelta(minutes=15)).isoformat(), now.isoformat()
            ),
            1,
        )
        stored = await get_approval_request(request["request_uuid"], self.guild_id)
        self.assertEqual(stored["status"], "FAILED")
        self.assertIn("interrupted", stored["result_message"].lower())

    async def test_open_approval_queue_is_server_scoped(self):
        now = datetime.now(timezone.utc).isoformat()
        first = await create_approval_request(
            self.guild_id,
            "WARN",
            100,
            200,
            "Target",
            "Reason",
            {},
            1,
            300,
            60,
            now,
        )
        await create_approval_request(
            self.other_guild_id,
            "BAN",
            101,
            201,
            "Other Target",
            "Other Reason",
            {},
            1,
            301,
            60,
            now,
        )
        requests = await get_approval_requests(
            self.guild_id, {"PENDING", "NEEDS_DETAILS"}
        )
        self.assertEqual(
            [item["request_uuid"] for item in requests], [first["request_uuid"]]
        )

    async def test_expired_approval_cleanup_is_server_scoped(self):
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        first = await create_approval_request(
            self.guild_id,
            "WARN",
            100,
            200,
            "Target",
            "Reason",
            {},
            1,
            300,
            5,
            created_at,
        )
        second = await create_approval_request(
            self.other_guild_id,
            "WARN",
            101,
            201,
            "Other Target",
            "Reason",
            {},
            1,
            301,
            5,
            created_at,
        )
        expired = await expire_approval_requests(
            datetime.now(timezone.utc).isoformat(), self.guild_id
        )
        self.assertEqual(expired, 1)
        self.assertEqual(
            (await get_approval_request(first["request_uuid"], self.guild_id))[
                "status"
            ],
            "EXPIRED",
        )
        self.assertEqual(
            (await get_approval_request(second["request_uuid"], self.other_guild_id))[
                "status"
            ],
            "PENDING",
        )

    async def test_disabling_rule_cancels_open_requests(self):
        now = datetime.now(timezone.utc).isoformat()
        request = await create_approval_request(
            self.guild_id,
            "BAN",
            100,
            200,
            "Target",
            "Reason",
            {},
            1,
            300,
            60,
            now,
        )
        cancelled = await cancel_pending_approval_requests(
            self.guild_id,
            "BAN",
            "Rule disabled",
            now,
        )
        self.assertEqual(cancelled, 1)
        stored = await get_approval_request(request["request_uuid"], self.guild_id)
        self.assertEqual(stored["status"], "FAILED")
        self.assertEqual(stored["result_message"], "Rule disabled")

    async def test_appeals_validate_ownership_uniqueness_and_details(self):
        infraction_uuid = await add_infraction(
            200, 100, "WARN", "Test warning", self.guild_id
        )
        now = datetime.now(timezone.utc).isoformat()
        appeal = await create_moderation_appeal(
            self.guild_id,
            200,
            infraction_uuid,
            "WARN",
            "This warning should be reviewed because evidence was missing.",
            now,
        )
        self.assertIsNotNone(appeal)
        duplicate = await create_moderation_appeal(
            self.guild_id,
            200,
            infraction_uuid,
            "WARN",
            "Duplicate",
            now,
        )
        self.assertIsNone(duplicate)
        self.assertIsNone(
            await get_moderation_appeal(appeal["appeal_uuid"], self.other_guild_id)
        )
        self.assertTrue(
            await update_moderation_appeal(
                appeal["appeal_uuid"],
                self.guild_id,
                "NEEDS_DETAILS",
                "Add context",
                300,
                now,
            )
        )
        self.assertFalse(
            await add_appeal_details(
                appeal["appeal_uuid"],
                self.guild_id,
                201,
                "Not the appellant",
                now,
            )
        )
        self.assertTrue(
            await add_appeal_details(
                appeal["appeal_uuid"],
                self.guild_id,
                200,
                "Requested context",
                now,
            )
        )
        stored = await get_moderation_appeal(appeal["appeal_uuid"], self.guild_id)
        self.assertEqual(stored["status"], "PENDING")
        self.assertIn("Requested context", stored["reason"])

    async def test_appeal_review_can_only_be_claimed_once(self):
        infraction_uuid = await add_infraction(
            200, 100, "WARN", "Concurrent review", self.guild_id
        )
        now = datetime.now(timezone.utc).isoformat()
        appeal = await create_moderation_appeal(
            self.guild_id,
            200,
            infraction_uuid,
            "WARN",
            "This warning requires a concurrent review safety test.",
            now,
        )
        claims = await asyncio.gather(
            claim_moderation_appeal(appeal["appeal_uuid"], self.guild_id, 300, now),
            claim_moderation_appeal(appeal["appeal_uuid"], self.guild_id, 301, now),
        )
        self.assertEqual(sum(claims), 1)
        reviewer_id = 300 if claims[0] else 301
        other_reviewer = 301 if reviewer_id == 300 else 300
        self.assertFalse(
            await complete_moderation_appeal(
                appeal["appeal_uuid"],
                self.guild_id,
                "DENIED",
                "Wrong reviewer",
                other_reviewer,
                now,
            )
        )
        self.assertTrue(
            await complete_moderation_appeal(
                appeal["appeal_uuid"],
                self.guild_id,
                "DENIED",
                "Reviewed safely",
                reviewer_id,
                now,
            )
        )

    async def test_interrupted_appeal_review_is_failed_safely(self):
        infraction_uuid = await add_infraction(
            200, 100, "TIMEOUT", "Interrupted review", self.guild_id
        )
        started = datetime.now(timezone.utc) - timedelta(minutes=20)
        appeal = await create_moderation_appeal(
            self.guild_id,
            200,
            infraction_uuid,
            "TIMEOUT",
            "This timeout review should recover after interruption.",
            started.isoformat(),
        )
        self.assertTrue(
            await claim_moderation_appeal(
                appeal["appeal_uuid"], self.guild_id, 300, started.isoformat()
            )
        )
        now = datetime.now(timezone.utc)
        self.assertEqual(
            await fail_stale_appeal_reviews(
                (now - timedelta(minutes=15)).isoformat(), now.isoformat()
            ),
            1,
        )
        stored = await get_moderation_appeal(appeal["appeal_uuid"], self.guild_id)
        self.assertEqual(stored["status"], "FAILED")
        self.assertIn("interrupted", stored["staff_response"].lower())

    async def test_accepted_appeals_are_excluded_from_risk(self):
        infraction_uuid = await add_infraction(
            200, 100, "BAN", "Appealed ban", self.guild_id
        )
        now = datetime.now(timezone.utc).isoformat()
        appeal = await create_moderation_appeal(
            self.guild_id,
            200,
            infraction_uuid,
            "BAN",
            "The ban should be reviewed because it was issued in error.",
            now,
        )
        self.assertEqual(len(await get_risk_records(self.guild_id, 200)), 1)
        self.assertTrue(
            await update_moderation_appeal(
                appeal["appeal_uuid"],
                self.guild_id,
                "ACCEPTED",
                "Accepted",
                300,
                now,
            )
        )
        self.assertEqual(await get_risk_records(self.guild_id, 200), [])
        self.assertIsNone(
            await create_moderation_appeal(
                self.guild_id,
                200,
                infraction_uuid,
                "BAN",
                "Second appeal",
                now,
            )
        )

    async def test_risk_records_never_cross_servers(self):
        await add_infraction(200, 100, "WARN", "First", self.guild_id)
        await add_infraction(200, 100, "BAN", "Second", self.other_guild_id)
        first = await get_risk_records(self.guild_id, 200)
        second = await get_risk_records(self.other_guild_id, 200)
        self.assertEqual([record["action_type"] for record in first], ["WARN"])
        self.assertEqual([record["action_type"] for record in second], ["BAN"])
