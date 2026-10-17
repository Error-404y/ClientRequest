import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite

import config
from cogs.monitoring import format_duration
from utils.metrics import MetricsRegistry
from utils.monitoring import monitoring_history, persist_monitoring_sample


class MetricsRegistryTests(unittest.TestCase):
    def test_windowed_event_counts(self):
        registry = MetricsRegistry()
        registry.increment("command.total")
        registry.increment("command.total", 2)
        registry.increment("command.failed")
        self.assertEqual(registry.count("command.total", 3600), 3)
        self.assertEqual(registry.count("command.failed", 3600), 1)

    def test_guild_scoped_counts(self):
        registry = MetricsRegistry()
        registry.increment("interaction.total", guild_id=100)
        registry.increment("interaction.total", guild_id=200)
        registry.increment("interaction.total", guild_id=100)
        self.assertEqual(registry.count("interaction.total", 3600, 100), 2)
        self.assertEqual(registry.count("interaction.total", 3600, 200), 1)

    def test_process_stats_have_runtime_values(self):
        registry = MetricsRegistry()
        stats = registry.process_stats()
        self.assertGreaterEqual(stats["cpu_percent"], 0)
        self.assertGreater(stats["memory_mb"], 0)

    def test_monitoring_duration_format(self):
        self.assertEqual(format_duration(90061), "1d 1h 1m 1s")


class MonitoringPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "monitoring.db")
        self.database_patch = patch.object(config, "DATABASE", self.database)
        self.database_patch.start()
        async with aiosqlite.connect(self.database) as database:
            await database.execute(
                """
                CREATE TABLE monitoring_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    gateway_latency_ms REAL NOT NULL,
                    db_latency_ms REAL NOT NULL,
                    event_loop_lag_ms REAL NOT NULL,
                    process_cpu_percent REAL NOT NULL,
                    process_memory_mb REAL NOT NULL,
                    guild_count INTEGER NOT NULL,
                    member_count INTEGER NOT NULL,
                    open_tickets INTEGER NOT NULL,
                    errors_1h INTEGER NOT NULL,
                    errors_24h INTEGER NOT NULL,
                    slow_operations_24h INTEGER NOT NULL,
                    commands_1h INTEGER NOT NULL,
                    interactions_1h INTEGER NOT NULL,
                    command_failures_1h INTEGER NOT NULL,
                    dm_failures_24h INTEGER NOT NULL
                )
                """
            )
            await database.commit()

    async def asyncTearDown(self):
        self.database_patch.stop()
        self.tempdir.cleanup()

    async def test_samples_are_persisted_and_aggregated(self):
        sample = {
            "gateway_latency_ms": 100,
            "db_latency_ms": 5,
            "event_loop_lag_ms": 2,
            "process_cpu_percent": 10,
            "process_memory_mb": 100,
            "guild_count": 2,
            "member_count": 50,
            "open_tickets": 1,
            "errors_1h": 0,
            "errors_24h": 1,
            "slow_operations_24h": 2,
            "commands_1h": 3,
            "interactions_1h": 4,
            "command_failures_1h": 0,
            "dm_failures_24h": 0,
        }
        await persist_monitoring_sample(sample)
        history = await monitoring_history(24)
        self.assertEqual(history["samples"], 1)
        self.assertEqual(history["gateway_avg_ms"], 100)
        self.assertEqual(history["memory_max_mb"], 100)


if __name__ == "__main__":
    unittest.main()
