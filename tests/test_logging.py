import re
import unittest

from cogs.diagnostics import format_uptime
from utils.logger import create_error_fingerprint, create_error_reference, redact


def generated_failure():
    raise RuntimeError("Repeated failure")


class LoggingTests(unittest.TestCase):
    def test_sensitive_values_are_redacted(self):
        result = redact("token=example-secret password: another-secret https://discord.com/api/webhooks/123/secret")
        self.assertNotIn("example-secret", result)
        self.assertNotIn("another-secret", result)
        self.assertNotIn("webhooks/123/secret", result)
        self.assertIn("[REDACTED]", result)

    def test_error_references_are_short_and_unique(self):
        first = create_error_reference()
        second = create_error_reference()
        self.assertRegex(first, re.compile(r"^ERR-[A-F0-9]{8}$"))
        self.assertNotEqual(first, second)

    def test_uptime_format(self):
        self.assertEqual(format_uptime(90061), "1d 1h 1m 1s")

    def test_error_fingerprints_group_same_failure(self):
        fingerprints = []
        for _ in range(2):
            try:
                generated_failure()
            except RuntimeError as error:
                fingerprints.append(create_error_fingerprint("TEST", error, "same context"))
        self.assertEqual(fingerprints[0], fingerprints[1])


if __name__ == "__main__":
    unittest.main()
