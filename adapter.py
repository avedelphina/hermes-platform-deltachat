"""
Delta Chat platform adapter for Hermes Agent.

Bridges Delta Chat (email-based, E2E encrypted) to the Hermes agent pipeline.
Requires deltachat-rpc-server on PATH: pip install deltachat-rpc-server
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DC_MESSAGE_MAX_LEN = 3600  # DC truncates at ~3800; split conservatively
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_message(text: str, max_len: int = DC_MESSAGE_MAX_LEN) -> list[str]:
    """Split long text at paragraph/line/sentence/word boundaries."""
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > max_len:
        split_at = -1
        # Try in order: paragraph break, line break, sentence end, word boundary
        for rfind_str, extra in [("\n\n", 0), ("\n", 0), (". ", 1), (" ", 0)]:
            idx = remaining.rfind(rfind_str, 0, max_len)
            if idx > max_len * 0.5:
                split_at = idx + extra
                break
        if split_at == -1:
            split_at = max_len
        parts.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def _is_valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s))


def _parse_email_list(raw: str) -> set[str]:
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def check_requirements() -> bool:
    try:
        import deltachat_rpc_client  # noqa: F401
        return True
    except ImportError:
        return False


def _get_chat(account, chat_id: str):
    """Return a DC Chat for a Hermes chat_id (email for DM, 'group:N' for groups)."""
    from deltachat_rpc_client.chat import Chat
    if chat_id.startswith("group:"):
        return Chat(account, int(chat_id[6:]))
    contact = account.get_contact_by_addr(chat_id) or account.create_contact(chat_id)
    return contact.create_chat()


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_image_from_bytes,
    cache_audio_from_bytes,
    cache_document_from_bytes,
)
from gateway.config import Platform


class DeltaChatAdapter(BasePlatformAdapter):
    """Delta Chat platform adapter — email-based E2E encrypted messaging."""

    MAX_MESSAGE_LENGTH = DC_MESSAGE_MAX_LEN
    splits_long_messages = True

    def __init__(self, config):
        super().__init__(config=config, platform=Platform("deltachat"))

        extra = getattr(config, "extra", {}) or {}
        g = lambda env, key, default="": os.getenv(env) or extra.get(key, default)

        self._email = g("DELTACHAT_EMAIL", "email", "auto")
        self._password = g("DELTACHAT_PASSWORD", "password")
        self._data_dir = g("DELTACHAT_DATA_DIR", "data_dir", "~/.hermes/deltachat-data")
        self._rpc_server = g("DELTACHAT_RPC_SERVER", "rpc_server", "deltachat-rpc-server")
        self._chatmail_server = g("DELTACHAT_CHATMAIL_SERVER", "chatmail_server", "nine.testrun.org")
        self._display_name = g("DELTACHAT_DISPLAY_NAME", "display_name", "Hermes")
        self._avatar_path = g("DELTACHAT_AVATAR_PATH", "avatar_path") or None

        allow_all = g("DELTACHAT_ALLOW_ALL_USERS", "allow_all_users", "false").lower() in ("1", "true", "yes")
        raw_allowed = g("DELTACHAT_ALLOWED_USERS", "allowed_users")
        self._allowed_users: set[str] = set() if allow_all else (_parse_email_list(raw_allowed) if raw_allowed else set())

        self._dm_policy = g("DELTACHAT_DM_POLICY", "dm_policy", "pairing")
        raw_dm_allow = g("DELTACHAT_DM_ALLOWED_USERS", "dm_allowed_users")
        self._dm_allow_from: set[str] = _parse_email_list(raw_dm_allow) if raw_dm_allow else set()

        self._group_policy = g("DELTACHAT_GROUP_POLICY", "group_policy", "open")
        raw_group_allow = g("DELTACHAT_GROUP_ALLOWED_USERS", "group_allowed_users")
        self._group_allow_from: set[str] = _parse_email_list(raw_group_allow) if raw_group_allow else set()

        self._require_mention = g("DELTACHAT_REQUIRE_MENTION", "require_mention", "false").lower() in ("1", "true", "yes")

        # Runtime state (guarded by _running flag)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._account = None
        self._rpc_ref = None
        self._event_thread: Optional[threading.Thread] = None
        self._running = False
        self._setup_event = threading.Event()
        self._setup_error: Optional[Exception] = None
        self._invite_link: Optional[str] = None
        self._invite_svg: Optional[str] = None
        self._crash_times: list[float] = []

    # --- Hermes lifecycle ---

    async def connect(self) -> bool:
        self._loop = asyncio.get_event_loop()
        self._running = True
        self._setup_event.clear()
        self._setup_error = None

        self._event_thread = threading.Thread(
            target=self._run_dc, daemon=True, name="deltachat-event"
        )
        self._event_thread.start()

        ok = await self._loop.run_in_executor(None, lambda: self._setup_event.wait(60))
        if not ok:
            logger.error("DeltaChat: connect timed out (60s)")
            self._running = False
            return False
        if self._setup_error:
            logger.error("DeltaChat: connect failed: %s", self._setup_error)
            return False
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._account:
            loop = asyncio.get_event_loop()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, self._account.stop_io),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, Exception):
                pass
        if self._event_thread and self._event_thread.is_alive():
            self._event_thread.join(timeout=10.0)
        self._account = None
        self._rpc_ref = None

    # --- Sending ---

    async def send(self, chat_id: str, text: str, **kwargs) -> SendResult:
        if not self._account:
            return SendResult(success=False, error="not connected")
        loop = asyncio.get_event_loop()
        try:
            chat = await loop.run_in_executor(None, lambda: _get_chat(self._account, chat_id))
            for chunk in _split_message(text):
                await loop.run_in_executor(None, lambda c=chunk: chat.send_text(c))
            return SendResult(success=True)
        except Exception as e:
            logger.error("DeltaChat: send to %s failed: %s", chat_id, e)
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id: str) -> None:
        pass  # Delta Chat has no typing indicator concept

    async def send_image(self, chat_id: str, image_url: str, caption: str = None) -> SendResult:
        if not self._account:
            return SendResult(success=False, error="not connected")
        import httpx
        loop = asyncio.get_event_loop()
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.get(image_url)
                resp.raise_for_status()
            suffix = Path(image_url.split("?")[0]).suffix or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(resp.content)
                tmp = Path(f.name)
            try:
                chat = await loop.run_in_executor(None, lambda: _get_chat(self._account, chat_id))
                await loop.run_in_executor(None, lambda: chat.send_message(text=caption, file=str(tmp)))
            finally:
                tmp.unlink(missing_ok=True)
            return SendResult(success=True)
        except Exception as e:
            logger.error("DeltaChat: send_image to %s failed: %s", chat_id, e)
            return SendResult(success=False, error=str(e))

    async def send_document(self, chat_id: str, path: str, caption: str = None) -> SendResult:
        if not self._account:
            return SendResult(success=False, error="not connected")
        loop = asyncio.get_event_loop()
        try:
            chat = await loop.run_in_executor(None, lambda: _get_chat(self._account, chat_id))
            await loop.run_in_executor(None, lambda: chat.send_message(text=caption, file=path))
            return SendResult(success=True)
        except Exception as e:
            logger.error("DeltaChat: send_document to %s failed: %s", chat_id, e)
            return SendResult(success=False, error=str(e))

    async def get_chat_info(self, chat_id: str) -> dict:
        if not self._account:
            return {"name": chat_id, "type": "unknown", "chat_id": chat_id}
        loop = asyncio.get_event_loop()
        try:
            chat = _get_chat(self._account, chat_id)
            snap = await loop.run_in_executor(None, chat.get_basic_snapshot)
            return {
                "name": snap.name,
                "type": "group" if chat_id.startswith("group:") else "dm",
                "chat_id": chat_id,
            }
        except Exception:
            return {"name": chat_id, "type": "unknown", "chat_id": chat_id}

    # --- Background thread: DC event loop with crash recovery ---

    def _run_dc(self) -> None:
        while self._running:
            try:
                self._run_dc_once()
                break  # Clean exit
            except Exception as e:
                if not self._running:
                    break
                now = time.monotonic()
                self._crash_times = [t for t in self._crash_times if now - t < 60]
                self._crash_times.append(now)
                if len(self._crash_times) >= 3:
                    logger.error("DeltaChat: 3 crashes in 60s — disabling")
                    self._running = False
                    break
                logger.error("DeltaChat: crashed (%s), restarting in 5s", e)
                time.sleep(5)

        if not self._setup_event.is_set():
            if not self._setup_error:
                self._setup_error = Exception("DeltaChat thread exited unexpectedly")
            self._setup_event.set()

    def _run_dc_once(self) -> None:
        from deltachat_rpc_client import DeltaChat, Rpc, events
        from deltachat_rpc_client.client import Client

        data_dir = Path(self._data_dir).expanduser().resolve()
        if ".." in data_dir.parts:
            raise ValueError(f"data_dir may not contain '..': {self._data_dir}")
        data_dir.mkdir(parents=True, exist_ok=True)
        data_dir.chmod(0o700)

        hooks = events.HookCollection()

        @hooks.on(events.NewMessage)
        def on_message(event):
            snap = event.message_snapshot
            if self._loop and not self._loop.is_closed():
                asyncio.run_coroutine_threadsafe(self._on_message(snap), self._loop)

        with Rpc(accounts_dir=str(data_dir), rpc_server_path=self._rpc_server) as rpc:
            self._rpc_ref = rpc
            dc = DeltaChat(rpc)

            accounts = dc.get_all_accounts()
            account = accounts[0] if accounts else dc.add_account()
            self._account = account

            if not account.is_configured():
                self._configure_account(rpc, account)
            else:
                account.update_config(bot="1", show_emails="2", displayname=self._display_name)
                if self._avatar_path:
                    account.set_avatar(self._avatar_path)
                logger.info("DeltaChat: existing account: %s", account.get_config("addr"))

            # Fetch SecureJoin invite QR
            try:
                link, svg = rpc.get_chat_securejoin_qr_code_svg(account.id, None)
                self._invite_link = link
                self._invite_svg = svg
                logger.info("DeltaChat: invite link: %s", link)
            except Exception as e:
                logger.warning("DeltaChat: SecureJoin invite unavailable: %s", e)

            self._setup_event.set()  # Signal connect() that setup succeeded
            Client(account, hooks).run_until(lambda _: not self._running)

        self._account = None
        self._rpc_ref = None

    def _configure_account(self, rpc, account) -> None:
        if self._email and self._email != "auto" and self._password:
            logger.info("DeltaChat: configuring with %s", self._email)
            account.update_config(bot="1", show_emails="2", displayname=self._display_name)
            if self._avatar_path:
                account.set_avatar(self._avatar_path)
            account.add_or_update_transport({"addr": self._email, "password": self._password})
            rpc.configure(account.id)
        else:
            logger.info("DeltaChat: creating chatmail account on %s", self._chatmail_server)
            rpc.set_config_from_qr(account.id, f"DCACCOUNT:https://{self._chatmail_server}/new")
            account.update_config(bot="1", show_emails="2", displayname=self._display_name)
            if self._avatar_path:
                account.set_avatar(self._avatar_path)
            rpc.configure(account.id)
        logger.info("DeltaChat: account ready: %s", account.get_config("addr"))

    # --- Inbound message handler (runs in asyncio loop) ---

    async def _on_message(self, snap) -> None:
        loop = asyncio.get_event_loop()

        # System messages: only handle group join requests
        if snap.is_info:
            chat_snap = await loop.run_in_executor(None, snap.chat.get_basic_snapshot)
            if getattr(chat_snap, "chat_type", None) == "Group" and getattr(chat_snap, "is_contact_request", False):
                fn = snap.chat.leave if self._group_policy == "disabled" else snap.chat.accept
                await loop.run_in_executor(None, fn)
            return

        if getattr(snap, "is_bot", False):
            return

        chat_snap = await loop.run_in_executor(None, snap.chat.get_basic_snapshot)
        chat_type = getattr(chat_snap, "chat_type", None)
        if chat_type not in ("Single", "Group"):
            return

        contact_snap = await loop.run_in_executor(None, snap.sender.get_snapshot)
        sender_email = (contact_snap.address or "").lower()
        sender_name = contact_snap.display_name or sender_email
        is_verified = getattr(contact_snap, "is_verified", False)
        is_request = getattr(chat_snap, "is_contact_request", False)

        logger.info("DeltaChat: %s from %s: %r", chat_type, sender_email[:30], (snap.text or "")[:60])

        # Global allowlist (empty set = allow all)
        if self._allowed_users and sender_email not in self._allowed_users:
            logger.warning("DeltaChat: rejected %s (not in allowed_users)", sender_email)
            await loop.run_in_executor(None, lambda: snap.chat.send_text(
                "Sorry, you are not authorized to use this bot."
            ))
            return

        if chat_type == "Single":
            reason = self._check_dm(sender_email, is_verified)
            if reason:
                logger.warning("DeltaChat: dm_policy rejected %s", sender_email)
                await loop.run_in_executor(None, lambda: snap.chat.send_text(reason))
                return
            if is_request:
                await loop.run_in_executor(None, snap.chat.accept)

        if chat_type == "Group":
            reason = self._check_group(sender_email)
            if reason:
                logger.warning("DeltaChat: group_policy rejected %s", sender_email)
                if is_request:
                    await loop.run_in_executor(None, snap.chat.leave)
                else:
                    await loop.run_in_executor(None, lambda: snap.chat.send_text(reason))
                return
            if is_request:
                await loop.run_in_executor(None, snap.chat.accept)

        # Require mention in groups
        if chat_type == "Group" and self._require_mention:
            if self._display_name.lower() not in (snap.text or "").lower():
                return

        hermes_chat_id = sender_email if chat_type == "Single" else f"group:{snap.chat_id}"

        # Handle file attachments
        media_urls: list[str] = []
        media_types: list[str] = []
        msg_type = MessageType.TEXT
        if getattr(snap, "file", None):
            try:
                file_path = Path(snap.file)
                data = file_path.read_bytes()
                mime = getattr(snap, "file_mime", "") or "application/octet-stream"
                if mime.startswith("image/"):
                    cached = cache_image_from_bytes(data, suffix=file_path.suffix)
                    msg_type = MessageType.PHOTO
                elif mime.startswith("audio/"):
                    cached = cache_audio_from_bytes(data, suffix=file_path.suffix)
                    msg_type = MessageType.AUDIO
                else:
                    cached = cache_document_from_bytes(data, suffix=file_path.suffix)
                    msg_type = MessageType.DOCUMENT
                media_urls.append(cached)
                media_types.append(mime)
            except Exception as e:
                logger.warning("DeltaChat: could not cache attachment: %s", e)

        source = self.build_source(
            chat_id=hermes_chat_id,
            chat_name=getattr(chat_snap, "name", hermes_chat_id),
            chat_type="dm" if chat_type == "Single" else "group",
            user_id=sender_email,
            user_name=sender_name,
        )

        event = MessageEvent(
            text=snap.text or "",
            message_type=msg_type,
            source=source,
            message_id=str(snap.message.id),
            timestamp=datetime.datetime.now(),
            media_urls=media_urls,
            media_types=media_types,
        )

        await self.handle_message(event)

    def _check_dm(self, sender_email: str, is_verified: bool) -> Optional[str]:
        if self._dm_policy == "disabled":
            return "Sorry, this bot does not accept direct messages."
        if self._dm_policy == "pairing" and not is_verified:
            return "I only chat with verified contacts. Scan my QR code to connect securely."
        if self._dm_policy == "allowlist" and self._dm_allow_from and sender_email not in self._dm_allow_from:
            return "Sorry, you are not on the allowed list for direct messages."
        return None

    def _check_group(self, sender_email: str) -> Optional[str]:
        if self._group_policy == "disabled":
            return "Sorry, this bot does not respond in group chats."
        if self._group_policy == "allowlist" and self._group_allow_from and sender_email not in self._group_allow_from:
            return "Sorry, you are not authorized for group interactions."
        return None


# ---------------------------------------------------------------------------
# Plugin registration hooks
# ---------------------------------------------------------------------------

def _env_enablement():
    """Seed PlatformConfig.extra from env vars (for gateway status without instantiating)."""
    email = os.getenv("DELTACHAT_EMAIL")
    if not email:
        return None
    return {
        "email": email,
        "data_dir": os.getenv("DELTACHAT_DATA_DIR", "~/.hermes/deltachat-data"),
        "chatmail_server": os.getenv("DELTACHAT_CHATMAIL_SERVER", "nine.testrun.org"),
        "display_name": os.getenv("DELTACHAT_DISPLAY_NAME", "Hermes"),
    }


def validate_config(config) -> None:
    extra = getattr(config, "extra", {}) or {}
    email = os.getenv("DELTACHAT_EMAIL") or extra.get("email", "auto")
    password = os.getenv("DELTACHAT_PASSWORD") or extra.get("password")

    if email and email != "auto" and not _is_valid_email(email):
        raise ValueError(f"DELTACHAT_EMAIL is not a valid email address: {email!r}")
    if email and email != "auto" and not password:
        raise ValueError("DELTACHAT_PASSWORD required when DELTACHAT_EMAIL is set (not 'auto')")

    dm_policy = os.getenv("DELTACHAT_DM_POLICY") or extra.get("dm_policy", "pairing")
    if dm_policy not in ("open", "allowlist", "pairing", "disabled"):
        raise ValueError(f"Invalid DELTACHAT_DM_POLICY: {dm_policy!r}")

    group_policy = os.getenv("DELTACHAT_GROUP_POLICY") or extra.get("group_policy", "open")
    if group_policy not in ("open", "allowlist", "disabled"):
        raise ValueError(f"Invalid DELTACHAT_GROUP_POLICY: {group_policy!r}")


def is_connected(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    return bool(os.getenv("DELTACHAT_EMAIL") or extra.get("email"))


def register(ctx) -> None:
    ctx.register_platform(
        name="deltachat",
        label="Delta Chat",
        adapter_factory=lambda cfg: DeltaChatAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["DELTACHAT_EMAIL"],
        install_hint=(
            "pip install deltachat-rpc-server deltachat-rpc-client\n"
            "Verify: deltachat-rpc-server --version"
        ),
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="DELTACHAT_HOME_CHANNEL",
        allowed_users_env="DELTACHAT_ALLOWED_USERS",
        allow_all_env="DELTACHAT_ALLOW_ALL_USERS",
        max_message_length=DC_MESSAGE_MAX_LEN,
        emoji="📧",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via Delta Chat, an email-based end-to-end encrypted messenger. "
            "Messages support markdown formatting. "
            "Long messages are split automatically at ~3600 characters. "
            "Image, audio, and document attachments are supported."
        ),
    )
