import unittest
from types import SimpleNamespace

import config
from utils.permissions import (
    can_manage_setup_admins,
    can_setup,
    is_bot_owner,
    is_owner,
    is_staff,
)


class BotOwnerPermissionTests(unittest.TestCase):
    def member(self, user_id):
        guild = SimpleNamespace(id=999, owner_id=111)
        return SimpleNamespace(
            id=user_id,
            guild=guild,
            roles=[],
            guild_permissions=SimpleNamespace(administrator=False),
        )

    def test_global_bot_owners_have_owner_access_without_server_setup(self):
        for owner_id in config.BOT_OWNER_IDS:
            member = self.member(owner_id)
            self.assertTrue(is_bot_owner(member))
            self.assertTrue(is_owner(member))
            self.assertTrue(can_setup(member))
            self.assertTrue(can_manage_setup_admins(member))
            self.assertTrue(is_staff(member))

    def test_regular_member_does_not_receive_global_owner_access(self):
        member = self.member(222)
        self.assertFalse(is_bot_owner(member))
        self.assertFalse(can_setup(member))
        self.assertFalse(can_manage_setup_admins(member))


if __name__ == "__main__":
    unittest.main()
