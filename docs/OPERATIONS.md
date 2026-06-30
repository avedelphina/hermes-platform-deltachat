# Operations Guide

This guide covers deployment, monitoring, and troubleshooting of the Hermes
Delta Chat adapter.

---

## Deployment checklist

- [ ] Python 3.12+ is installed.
- [ ] `pip install -r requirements.txt` succeeded.
- [ ] `deltachat-rpc-server` is installed and on `PATH`.
- [ ] `DELTACHAT_DATA_DIR` points to a persistent directory.
- [ ] `DELTACHAT_EMAIL` is set (or `auto`).
- [ ] For manual accounts, `DELTACHAT_PASSWORD` is set.
- [ ] Required allowlists and policies are configured.
- [ ] The Hermes gateway is configured to load the plugin.

---

## Verifying the installation

```bash
# Check the RPC server binary
deltachat-rpc-server --version

# Check the adapter's own requirements check
python3 -c "import adapter; print(adapter.check_requirements())"
```

`check_requirements()` returns `True` only if both the Python client and the RPC
server binary are available.

---

## Lifecycle

1. Hermes calls `register(ctx)` and later instantiates `DeltaChatAdapter`.
2. `connect()` validates configuration, registers signal handlers, and starts a
   background thread.
3. The background thread starts `deltachat-rpc-server`, configures the account,
   and runs the Delta Chat event loop.
4. `connect()` waits up to 60 seconds for setup to complete.
5. On shutdown, `disconnect()` stops I/O, removes signal handlers, and joins the
   background thread.

If setup fails, the adapter logs the error and returns `False` from `connect()`.

---

## Crash recovery

The background thread is restarted automatically after a crash, with a 5-second
delay. If 3 crashes occur within 60 seconds, the adapter disables itself
(`_running = False`) and stops retrying. A process restart is then required to
recover.

You can inspect recent crash counts via `get_status()`:

```python
status = adapter.get_status()
print(status["crashes_last_60s"])
print(status["last_crash"])
```

---

## Monitoring

### Health status

Call `adapter.get_status()` to obtain a snapshot:

```python
{
    "connected": True,
    "running": True,
    "account_addr": "bot@example.com",
    "crashes_last_60s": 0,
    "last_crash": None,
    "invite_link": "https://i.delta.chat/...",
    "invite_svg": True,
    "stats": {
        "messages_sent": 42,
        "images_sent": 3,
        "documents_sent": 1,
        "messages_send_failed": 0,
        "messages_rate_limited": 2,
        "duplicate_messages_dropped": 5,
    },
}
```

### Useful log lines

- `DeltaChat: account ready: <addr>` — account configured successfully.
- `DeltaChat: invite link: <url>` — SecureJoin invite link available.
- `DeltaChat: crashed (<error>), restarting in 5s` — background thread crash.
- `DeltaChat: 3 crashes in 60s — disabling` — adapter gave up restarting.
- `DeltaChat: rate limit exceeded for <email>` — a sender hit the rate limit.

---

## Graceful shutdown

The adapter registers handlers for `SIGTERM` and `SIGINT`. When a signal is
received, it schedules `disconnect()` on the running event loop. Make sure your
container orchestrator or process manager allows enough time for shutdown
(recommended: at least 15 seconds).

---

## Troubleshooting

### `connect()` times out

- Verify `deltachat-rpc-server` is on `PATH`.
- Check that `DELTACHAT_DATA_DIR` is writable.
- Look for earlier crash logs; the adapter may have hit 3 crashes in 60 s.

### Messages are not delivered

- Confirm the adapter is connected (`get_status()["connected"]`).
- Check the account address in `get_status()["account_addr"]`.
- Review `messages_send_failed` in `get_status()["stats"]`.

### The bot responds to everyone

- Check `DELTACHAT_ALLOW_ALL_USERS`; if `true`, the global allowlist is bypassed.
- Verify `DELTACHAT_DM_POLICY` and `DELTACHAT_GROUP_POLICY` values.

### The bot ignores group messages

- If `DELTACHAT_REQUIRE_MENTION=true`, ensure the message mentions the bot's
  display name (`@DisplayName` or whole-word name).
- Check `DELTACHAT_GROUP_POLICY` and `DELTACHAT_GROUP_ALLOWED_USERS`.

### Large attachments fail

- Outbound images are limited to 25 MB.
- Outbound documents are limited to 100 MB.
- Inbound attachments are limited to 50 MB.

### Duplicate messages

The adapter deduplicates inbound messages by `message_id` using a bounded cache
of the last 1000 IDs. If you see duplicates, the sender may be generating new
message IDs or the cache may have overflowed.

---

## Updating the plugin

The adapter registers with `allow_update_command=True`, so Hermes can reload it
via the platform update mechanism. After an update:

1. Hermes calls `disconnect()` on the old instance.
2. Hermes creates a new `DeltaChatAdapter` and calls `connect()`.
3. The existing account in `DELTACHAT_DATA_DIR` is reused.

Ensure `DELTACHAT_DATA_DIR` is preserved across updates.
