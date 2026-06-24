"""
Unit tests for the Delta Chat adapter.

Tests the pure logic (message splitting, policy checks, config validation)
without requiring deltachat-rpc-server or a Hermes gateway.
"""

import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Stub out gateway imports so we can test adapter.py without Hermes installed
# ---------------------------------------------------------------------------
_gateway_base = types.ModuleType("gateway.platforms.base")
_gateway_config = types.ModuleType("gateway.config")

class _FakePlatform:
    def __init__(self, name):
        self.value = name

class _FakeBase:
    def __init__(self, config, platform):
        self.config = config
        self.platform = platform

_gateway_base.BasePlatformAdapter = _FakeBase
_gateway_base.MessageEvent = object
_gateway_base.MessageType = type("MessageType", (), {"TEXT": "text", "PHOTO": "photo", "AUDIO": "audio", "DOCUMENT": "document"})()
_gateway_base.SendResult = type("SendResult", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)})()
_gateway_base.cache_image_from_bytes = lambda data, suffix="": "/tmp/img" + suffix
_gateway_base.cache_audio_from_bytes = lambda data, suffix="": "/tmp/aud" + suffix
_gateway_base.cache_document_from_bytes = lambda data, suffix="": "/tmp/doc" + suffix
_gateway_config.Platform = _FakePlatform

sys.modules.setdefault("gateway", types.ModuleType("gateway"))
sys.modules.setdefault("gateway.platforms", types.ModuleType("gateway.platforms"))
sys.modules["gateway.platforms.base"] = _gateway_base
sys.modules["gateway.config"] = _gateway_config

# Now import the module under test
import importlib
adapter = importlib.import_module("adapter")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSplitMessage(unittest.TestCase):
    def test_short_message_unchanged(self):
        self.assertEqual(adapter._split_message("hello"), ["hello"])

    def test_split_on_paragraph(self):
        text = ("a" * 1800) + "\n\n" + ("b" * 1800)
        parts = adapter._split_message(text)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(len(p) <= adapter.DC_MESSAGE_MAX_LEN for p in parts))

    def test_split_on_line(self):
        text = ("a" * 1800) + "\n" + ("b" * 1800)
        parts = adapter._split_message(text)
        self.assertEqual(len(parts), 2)

    def test_hard_split_fallback(self):
        text = "x" * 8000
        parts = adapter._split_message(text)
        self.assertTrue(len(parts) >= 2)
        self.assertTrue(all(len(p) <= adapter.DC_MESSAGE_MAX_LEN for p in parts))
        self.assertEqual("".join(parts), text)

    def test_exact_boundary_no_split(self):
        text = "a" * adapter.DC_MESSAGE_MAX_LEN
        self.assertEqual(adapter._split_message(text), [text])


class TestEmailValidation(unittest.TestCase):
    def test_valid(self):
        for e in ["user@example.com", "a+b@x.org", "foo@bar.co.uk"]:
            self.assertTrue(adapter._is_valid_email(e), e)

    def test_invalid(self):
        for e in ["notanemail", "@domain.com", "user@", "user @domain.com"]:
            self.assertFalse(adapter._is_valid_email(e), e)


class TestParseEmailList(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(adapter._parse_email_list("a@x.com, B@X.COM"), {"a@x.com", "b@x.com"})

    def test_empty(self):
        self.assertEqual(adapter._parse_email_list(""), set())

    def test_single(self):
        self.assertEqual(adapter._parse_email_list("alice@example.com"), {"alice@example.com"})


class _FakeConfig:
    extra = {}


class TestDeltaChatAdapter(unittest.TestCase):
    def _make(self, env=None):
        import os
        saved = {k: os.environ.get(k) for k in (env or {})}
        for k, v in (env or {}).items():
            os.environ[k] = v
        try:
            a = adapter.DeltaChatAdapter(_FakeConfig())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return a

    def test_defaults(self):
        a = self._make()
        self.assertEqual(a._email, "auto")
        self.assertEqual(a._dm_policy, "pairing")
        self.assertEqual(a._group_policy, "open")
        self.assertFalse(a._require_mention)

    def test_env_overrides(self):
        a = self._make({
            "DELTACHAT_EMAIL": "bot@example.com",
            "DELTACHAT_PASSWORD": "secret",
            "DELTACHAT_DM_POLICY": "open",
            "DELTACHAT_REQUIRE_MENTION": "true",
        })
        self.assertEqual(a._email, "bot@example.com")
        self.assertEqual(a._password, "secret")
        self.assertEqual(a._dm_policy, "open")
        self.assertTrue(a._require_mention)

    def test_allowed_users_parsed(self):
        a = self._make({"DELTACHAT_ALLOWED_USERS": "alice@x.com, BOB@x.com"})
        self.assertIn("alice@x.com", a._allowed_users)
        self.assertIn("bob@x.com", a._allowed_users)

    def test_allow_all_clears_set(self):
        a = self._make({
            "DELTACHAT_ALLOWED_USERS": "alice@x.com",
            "DELTACHAT_ALLOW_ALL_USERS": "true",
        })
        self.assertEqual(a._allowed_users, set())

    def test_check_dm_open(self):
        a = self._make({"DELTACHAT_DM_POLICY": "open"})
        self.assertIsNone(a._check_dm("anyone@x.com", False))

    def test_check_dm_pairing_unverified(self):
        a = self._make({"DELTACHAT_DM_POLICY": "pairing"})
        self.assertIsNotNone(a._check_dm("user@x.com", False))

    def test_check_dm_pairing_verified(self):
        a = self._make({"DELTACHAT_DM_POLICY": "pairing"})
        self.assertIsNone(a._check_dm("user@x.com", True))

    def test_check_dm_disabled(self):
        a = self._make({"DELTACHAT_DM_POLICY": "disabled"})
        self.assertIsNotNone(a._check_dm("user@x.com", True))

    def test_check_dm_allowlist_blocked(self):
        import os
        saved = os.environ.get("DELTACHAT_DM_POLICY")
        saved2 = os.environ.get("DELTACHAT_DM_ALLOWED_USERS")
        os.environ["DELTACHAT_DM_POLICY"] = "allowlist"
        os.environ["DELTACHAT_DM_ALLOWED_USERS"] = "alice@x.com"
        try:
            a = adapter.DeltaChatAdapter(_FakeConfig())
        finally:
            if saved is None: os.environ.pop("DELTACHAT_DM_POLICY", None)
            else: os.environ["DELTACHAT_DM_POLICY"] = saved
            if saved2 is None: os.environ.pop("DELTACHAT_DM_ALLOWED_USERS", None)
            else: os.environ["DELTACHAT_DM_ALLOWED_USERS"] = saved2
        self.assertIsNone(a._check_dm("alice@x.com", False))
        self.assertIsNotNone(a._check_dm("bob@x.com", False))

    def test_check_group_disabled(self):
        a = self._make({"DELTACHAT_GROUP_POLICY": "disabled"})
        self.assertIsNotNone(a._check_group("user@x.com"))

    def test_check_group_open(self):
        a = self._make({"DELTACHAT_GROUP_POLICY": "open"})
        self.assertIsNone(a._check_group("user@x.com"))


class TestValidateConfig(unittest.TestCase):
    def test_valid_auto(self):
        adapter.validate_config(_FakeConfig())  # no exception

    def test_invalid_email_format(self):
        import os
        os.environ["DELTACHAT_EMAIL"] = "notanemail"
        os.environ["DELTACHAT_PASSWORD"] = "pass"
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_EMAIL", None)
            os.environ.pop("DELTACHAT_PASSWORD", None)

    def test_missing_password(self):
        import os
        os.environ["DELTACHAT_EMAIL"] = "bot@example.com"
        os.environ.pop("DELTACHAT_PASSWORD", None)
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_EMAIL", None)

    def test_invalid_dm_policy(self):
        import os
        os.environ["DELTACHAT_DM_POLICY"] = "bogus"
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_DM_POLICY", None)


if __name__ == "__main__":
    unittest.main()
