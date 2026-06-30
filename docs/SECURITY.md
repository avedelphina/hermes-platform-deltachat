# Security Guide

This guide covers the most important security settings and behaviors of the
Hermes Delta Chat adapter.

---

## Credential handling

- **Never commit credentials.** Do not put `DELTACHAT_PASSWORD`, chatmail QR
  contents, or account backup files in version control.
- Use environment variables or a secrets manager (Kubernetes secrets, HashiCorp
  Vault, AWS Secrets Manager, etc.) to inject credentials.
- For manual email accounts, use an app-specific password rather than the
  primary account password whenever possible.

---

## Data directory

The adapter stores Delta Chat account keys, contact databases, and message state
in `DELTACHAT_DATA_DIR` (default `~/.hermes/deltachat-data`).

- The directory is created with mode `0o700` (owner read/write/execute only).
- The adapter warns if the directory has permissive permissions.
- Keep this directory on a persistent volume. Losing it means losing your
  account keys and verified contacts.
- Back up the directory if the bot manages important contacts or groups.

---

## Contact verification and DM policy

The recommended mode for bots that should not talk to strangers is:

```bash
export DELTACHAT_DM_POLICY=pairing
```

In `pairing` mode, the bot only accepts direct messages from verified contacts.
Verification happens through Delta Chat's SecureJoin protocol. The bot logs its
SecureJoin invite link at startup, and you can share that link or the matching
QR code with intended users.

For group chats, combine `pairing` DMs with a group allowlist:

```bash
export DELTACHAT_DM_POLICY=pairing
export DELTACHAT_GROUP_POLICY=allowlist
export DELTACHAT_GROUP_ALLOWED_USERS="alice@example.com,bob@example.com"
```

---

## Rejection replies

By default, the adapter sends a short explanation when a sender is rejected
(e.g. "Sorry, you are not authorized to use this bot."). This confirms the bot
exists and may leak information.

To silently drop rejected messages:

```bash
export DELTACHAT_SEND_REJECTION_REPLIES=false
```

This is recommended for public-facing or high-security bots.

---

## Image and document handling

### Outbound images (`send_image`)

- Only `http://` and `https://` URLs are accepted.
- Redirects are **not** followed.
- Downloads are bounded to 25 MB by streamed size and `Content-Length`.
- The response `Content-Type` must start with `image/`.
- File extensions are sanitized before writing to disk.

### Outbound documents (`send_document`)

- The path is resolved and must be a regular file.
- Files larger than 100 MB are rejected.
- Symlinks are resolved to their real path.

### Inbound attachments

- Attachments larger than 50 MB are rejected before being read into memory.
- Files are handed to the Hermes gateway cache helpers; ensure the gateway's
  cache directory is also properly restricted.

---

## RPC server path

The adapter invokes the binary specified by `DELTACHAT_RPC_SERVER`. A custom
path is validated to exist and be executable before it is launched. Never point
this setting at a writable directory or an untrusted file.

```bash
# Good: a known executable
export DELTACHAT_RPC_SERVER=/usr/local/bin/deltachat-rpc-server

# Bad: a directory or writable path
export DELTACHAT_RPC_SERVER=/tmp/my-rpc
```

---

## Rate limiting and abuse

The adapter applies a per-sender sliding-window rate limit on inbound messages
(default: 30 messages per 60 seconds). This helps mitigate accidental loops and
low-volume abuse, but it is not a substitute for network-level DDoS protection.

Tune the limits with:

```bash
export DELTACHAT_RATE_LIMIT_MAX=60
export DELTACHAT_RATE_LIMIT_WINDOW=60
```

---

## PII and logging

The plugin is registered with `pii_safe=False`, meaning email addresses and
message content are considered sensitive. The adapter logs sender addresses and
message previews at `INFO` level but truncates them. If you need stricter
handling, configure your logging pipeline to filter or redact these lines.

---

## Allowlist behavior summary

| Setting | Empty value | Non-empty value |
|---------|-------------|-----------------|
| `DELTACHAT_ALLOWED_USERS` | Allow anyone (subject to policies) | Allow only listed addresses |
| `DELTACHAT_DM_ALLOWED_USERS` | Allow anyone (subject to `DM_POLICY`) | Allow only listed addresses for DMs |
| `DELTACHAT_GROUP_ALLOWED_USERS` | Allow anyone (subject to `GROUP_POLICY`) | Allow only listed addresses in groups |

`DELTACHAT_ALLOW_ALL_USERS=true` bypasses `DELTACHAT_ALLOWED_USERS` but does
not bypass `DM_POLICY` or `GROUP_POLICY`.
