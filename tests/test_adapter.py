"""
Unit tests for the Delta Chat adapter.

Tests the pure logic (message splitting, policy checks, config validation)
without requiring deltachat-rpc-server or a Hermes gateway.
"""

import os
import sys
import tempfile
import time
import types
import unittest
import unittest.mock
from pathlib import Path

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
_gateway_base.MessageType = type(
    "MessageType", (), {"TEXT": "text", "PHOTO": "photo", "AUDIO": "audio", "DOCUMENT": "document"}
)()
_gateway_base.SendResult = type(
    "SendResult", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)}
)()
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

    def test_empty_message(self):
        self.assertEqual(adapter._split_message(""), [])

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

    def test_respects_custom_max_len(self):
        text = "a" * 100
        parts = adapter._split_message(text, max_len=40)
        self.assertTrue(all(len(p) <= 40 for p in parts))
        self.assertEqual("".join(parts), text)


class TestEmailValidation(unittest.TestCase):
    def test_valid(self):
        for e in ["user@example.com", "a+b@x.org", "foo@bar.co.uk"]:
            self.assertTrue(adapter._is_valid_email(e), e)

    def test_invalid(self):
        for e in ["notanemail", "@domain.com", "user@", "user @domain.com"]:
            self.assertFalse(adapter._is_valid_email(e), e)

    def test_rejects_display_name_form(self):
        self.assertFalse(adapter._is_valid_email("User <user@example.com>"))

    def test_rejects_too_long(self):
        self.assertFalse(adapter._is_valid_email("a" * 250 + "@x.com"))


class TestParseEmailList(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            adapter._parse_email_list("a@x.com, B@X.COM"), {"a@x.com", "b@x.com"}
        )

    def test_empty(self):
        self.assertEqual(adapter._parse_email_list(""), set())

    def test_single(self):
        self.assertEqual(adapter._parse_email_list("alice@example.com"), {"alice@example.com"})


class TestParseChatmailServers(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(
            adapter._parse_chatmail_servers("a.com, b.com, a.com"),
            ["a.com", "b.com"],
        )

    def test_empty(self):
        self.assertEqual(adapter._parse_chatmail_servers(""), [])

    def test_whitespace_trimmed(self):
        self.assertEqual(
            adapter._parse_chatmail_servers(" a.com , b.com "),
            ["a.com", "b.com"],
        )

    def test_case_preserved(self):
        self.assertEqual(
            adapter._parse_chatmail_servers("A.com, a.com"),
            ["A.com"],
        )


class TestSafeDataDir(unittest.TestCase):
    def test_rejects_dotdot(self):
        with self.assertRaises(ValueError):
            adapter._safe_data_dir("/tmp/foo/../bar")

    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "dc-data")
            p = adapter._safe_data_dir(path, create=True)
            self.assertTrue(p.exists())
            self.assertEqual(p.stat().st_mode & 0o777, 0o700)


class TestValidateRpcServerPath(unittest.TestCase):
    def test_non_strict_returns_path_when_missing(self):
        self.assertEqual(
            adapter._validate_rpc_server_path("probably-not-on-path", strict=False),
            "probably-not-on-path",
        )

    def test_strict_missing_raises(self):
        with self.assertRaises(ValueError):
            adapter._validate_rpc_server_path("/nonexistent/binary-12345", strict=True)

    def test_strict_resolves_absolute_executable(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"#!/bin/sh\n")
            path = f.name
        os.chmod(path, 0o755)
        try:
            self.assertEqual(adapter._validate_rpc_server_path(path, strict=True), path)
        finally:
            os.unlink(path)


class TestValidateAvatarPath(unittest.TestCase):
    def test_non_strict_accepts_image_suffix(self):
        self.assertEqual(
            adapter._validate_avatar_path("/tmp/bot.png", strict=False), "/tmp/bot.png"
        )

    def test_invalid_suffix_raises(self):
        with self.assertRaises(ValueError):
            adapter._validate_avatar_path("/tmp/bot.txt", strict=False)

    def test_strict_missing_file_raises(self):
        with self.assertRaises(ValueError):
            adapter._validate_avatar_path("/tmp/nonexistent.png", strict=True)

    def test_strict_existing_file_ok(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            path = f.name
        try:
            self.assertEqual(
                adapter._validate_avatar_path(path, strict=True), str(Path(path).resolve())
            )
        finally:
            os.unlink(path)


class TestMessageCache(unittest.TestCase):
    def test_add_new(self):
        c = adapter._MessageCache()
        self.assertTrue(c.add("a"))
        self.assertTrue(c.add("b"))

    def test_duplicate(self):
        c = adapter._MessageCache()
        self.assertTrue(c.add("a"))
        self.assertFalse(c.add("a"))

    def test_bounded(self):
        c = adapter._MessageCache(max_size=2)
        c.add("a")
        c.add("b")
        c.add("c")
        self.assertTrue(c.add("a"))  # 'a' was evicted


class TestRateLimiter(unittest.TestCase):
    def test_allows_under_limit(self):
        rl = adapter._RateLimiter(max_calls=3, window_seconds=60)
        self.assertTrue(rl.is_allowed("u"))
        self.assertTrue(rl.is_allowed("u"))
        self.assertTrue(rl.is_allowed("u"))
        self.assertFalse(rl.is_allowed("u"))

    def test_window_slides(self):
        rl = adapter._RateLimiter(max_calls=1, window_seconds=0.01)
        self.assertTrue(rl.is_allowed("u"))
        time.sleep(0.02)
        self.assertTrue(rl.is_allowed("u"))

    def test_keys_are_isolated(self):
        rl = adapter._RateLimiter(max_calls=1, window_seconds=60)
        self.assertTrue(rl.is_allowed("a"))
        self.assertTrue(rl.is_allowed("b"))


class _FakeConfig:
    extra = {}


class TestDeltaChatAdapter(unittest.TestCase):
    def _make(self, env=None):
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
        self.assertTrue(a._send_rejection_replies)
        self.assertEqual(a._max_message_len, adapter.DC_MESSAGE_MAX_LEN)
        self.assertEqual(a._rate_limiter.max_calls, 30)
        self.assertEqual(a._chatmail_servers, ["nine.testrun.org"])

    def test_multiple_chatmail_servers(self):
        a = self._make(
            {"DELTACHAT_CHATMAIL_SERVERS": "chat.postblue.cz, chat.cqre.net"}
        )
        self.assertEqual(a._chatmail_servers, ["chat.postblue.cz", "chat.cqre.net"])

    def test_chatmail_servers_override_single(self):
        a = self._make(
            {
                "DELTACHAT_CHATMAIL_SERVER": "old.example.com",
                "DELTACHAT_CHATMAIL_SERVERS": "chat.postblue.cz",
            }
        )
        self.assertEqual(a._chatmail_servers, ["chat.postblue.cz"])

    def test_env_overrides(self):
        a = self._make(
            {
                "DELTACHAT_EMAIL": "bot@example.com",
                "DELTACHAT_PASSWORD": "secret",
                "DELTACHAT_DM_POLICY": "open",
                "DELTACHAT_REQUIRE_MENTION": "true",
                "DELTACHAT_SEND_REJECTION_REPLIES": "false",
                "DELTACHAT_MAX_MESSAGE_LENGTH": "1000",
                "DELTACHAT_RATE_LIMIT_MAX": "10",
                "DELTACHAT_RATE_LIMIT_WINDOW": "30",
            }
        )
        self.assertEqual(a._email, "bot@example.com")
        self.assertEqual(a._password, "secret")
        self.assertEqual(a._dm_policy, "open")
        self.assertTrue(a._require_mention)
        self.assertFalse(a._send_rejection_replies)
        self.assertEqual(a._max_message_len, 1000)
        self.assertEqual(a._rate_limiter.max_calls, 10)
        self.assertEqual(a._rate_limiter.window, 30.0)

    def test_allowed_users_parsed(self):
        a = self._make({"DELTACHAT_ALLOWED_USERS": "alice@x.com, BOB@x.com"})
        self.assertIn("alice@x.com", a._allowed_users)
        self.assertIn("bob@x.com", a._allowed_users)

    def test_allow_all_clears_set(self):
        a = self._make(
            {
                "DELTACHAT_ALLOWED_USERS": "alice@x.com",
                "DELTACHAT_ALLOW_ALL_USERS": "true",
            }
        )
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
        saved = os.environ.get("DELTACHAT_DM_POLICY")
        saved2 = os.environ.get("DELTACHAT_DM_ALLOWED_USERS")
        os.environ["DELTACHAT_DM_POLICY"] = "allowlist"
        os.environ["DELTACHAT_DM_ALLOWED_USERS"] = "alice@x.com"
        try:
            a = adapter.DeltaChatAdapter(_FakeConfig())
        finally:
            if saved is None:
                os.environ.pop("DELTACHAT_DM_POLICY", None)
            else:
                os.environ["DELTACHAT_DM_POLICY"] = saved
            if saved2 is None:
                os.environ.pop("DELTACHAT_DM_ALLOWED_USERS", None)
            else:
                os.environ["DELTACHAT_DM_ALLOWED_USERS"] = saved2
        self.assertIsNone(a._check_dm("alice@x.com", False))
        self.assertIsNotNone(a._check_dm("bob@x.com", False))

    def test_check_group_disabled(self):
        a = self._make({"DELTACHAT_GROUP_POLICY": "disabled"})
        self.assertIsNotNone(a._check_group("user@x.com"))

    def test_check_group_open(self):
        a = self._make({"DELTACHAT_GROUP_POLICY": "open"})
        self.assertIsNone(a._check_group("user@x.com"))

    def test_get_status_disconnected(self):
        a = self._make()
        status = a.get_status()
        self.assertFalse(status["connected"])
        self.assertFalse(status["running"])
        self.assertEqual(status["account_addr"], None)
        self.assertEqual(status["stats"], {})


class TestIsMentioned(unittest.TestCase):
    def setUp(self):
        self.a = adapter.DeltaChatAdapter(_FakeConfig())

    def test_at_mention(self):
        self.assertTrue(self.a._is_mentioned("Hello @Hermes"))

    def test_word_mention(self):
        self.assertTrue(self.a._is_mentioned("Hello Hermes"))

    def test_case_insensitive(self):
        self.assertTrue(self.a._is_mentioned("hello @hermes"))

    def test_no_substring_match(self):
        self.assertFalse(self.a._is_mentioned("Hermesss"))
        self.assertFalse(self.a._is_mentioned("someHermes"))

    def test_empty_text(self):
        self.assertFalse(self.a._is_mentioned(""))


class TestValidateConfig(unittest.TestCase):
    def test_valid_auto(self):
        adapter.validate_config(_FakeConfig())  # no exception

    def test_invalid_email_format(self):
        os.environ["DELTACHAT_EMAIL"] = "notanemail"
        os.environ["DELTACHAT_PASSWORD"] = "pass"
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_EMAIL", None)
            os.environ.pop("DELTACHAT_PASSWORD", None)

    def test_missing_password(self):
        os.environ["DELTACHAT_EMAIL"] = "bot@example.com"
        os.environ.pop("DELTACHAT_PASSWORD", None)
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_EMAIL", None)

    def test_invalid_dm_policy(self):
        os.environ["DELTACHAT_DM_POLICY"] = "bogus"
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_DM_POLICY", None)

    def test_rejects_bad_avatar_suffix(self):
        os.environ["DELTACHAT_AVATAR_PATH"] = "/tmp/bot.txt"
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_AVATAR_PATH", None)

    def test_rejects_data_dir_with_dotdot(self):
        os.environ["DELTACHAT_DATA_DIR"] = "/tmp/foo/../bar"
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_DATA_DIR", None)

    def test_rejects_custom_missing_rpc_server(self):
        os.environ["DELTACHAT_RPC_SERVER"] = "/nonexistent-binary-12345"
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_RPC_SERVER", None)

    def test_accepts_chatmail_servers(self):
        os.environ["DELTACHAT_CHATMAIL_SERVERS"] = "chat.postblue.cz,chat.cqre.net"
        try:
            adapter.validate_config(_FakeConfig())  # no exception
        finally:
            os.environ.pop("DELTACHAT_CHATMAIL_SERVERS", None)

    def test_rejects_empty_chatmail_servers(self):
        os.environ["DELTACHAT_CHATMAIL_SERVERS"] = " , , "
        try:
            with self.assertRaises(ValueError):
                adapter.validate_config(_FakeConfig())
        finally:
            os.environ.pop("DELTACHAT_CHATMAIL_SERVERS", None)


class TestCheckRequirements(unittest.TestCase):
    def test_false_when_binary_missing(self):
        # Ensure deltachat_rpc_client import succeeds by mocking it if absent.
        real_module = sys.modules.get("deltachat_rpc_client")
        fake_module = types.ModuleType("deltachat_rpc_client")
        sys.modules["deltachat_rpc_client"] = fake_module
        try:
            with unittest.mock.patch("adapter.shutil.which", return_value=None):
                self.assertFalse(adapter.check_requirements())
        finally:
            if real_module is not None:
                sys.modules["deltachat_rpc_client"] = real_module
            else:
                del sys.modules["deltachat_rpc_client"]

    def test_true_when_binary_present(self):
        real_module = sys.modules.get("deltachat_rpc_client")
        fake_module = types.ModuleType("deltachat_rpc_client")
        sys.modules["deltachat_rpc_client"] = fake_module
        try:
            with unittest.mock.patch("adapter.shutil.which", return_value="/usr/bin/deltachat-rpc-server"):
                self.assertTrue(adapter.check_requirements())
        finally:
            if real_module is not None:
                sys.modules["deltachat_rpc_client"] = real_module
            else:
                del sys.modules["deltachat_rpc_client"]


if __name__ == "__main__":
    unittest.main()
