# Configuration Reference

All settings are read from environment variables first, then from the Hermes
`PlatformConfig.extra` dict. Environment variables always win.

Environment variables use the prefix `DELTACHAT_`. The same keys without the
prefix can be used in `PlatformConfig.extra` (e.g. `email`, `password`,
`dm_policy`). Boolean values in `PlatformConfig.extra` (e.g.
`require_mention: true`) are also accepted and converted to string form
internally.

---

## Required variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DELTACHAT_EMAIL` | `auto` | Bot email address. Use `auto` to create a free chatmail account automatically. |

---

## Optional variables

### Account

| Variable | Default | Description |
|----------|---------|-------------|
| `DELTACHAT_PASSWORD` | — | Email password. Required when using a manual email account. |
| `DELTACHAT_DATA_DIR` | `~/.hermes/deltachat-data` | Directory for Delta Chat account keys and state. Created with mode `0o700`. |
| `DELTACHAT_RPC_SERVER` | `deltachat-rpc-server` | Path or name of the `deltachat-rpc-server` binary. Custom paths are validated to exist and be executable. |
| `DELTACHAT_CHATMAIL_SERVER` | `nine.testrun.org` | Chatmail server used when `DELTACHAT_EMAIL=auto`. |
| `DELTACHAT_CHATMAIL_SERVERS` | — | Comma-separated list of chatmail servers to try in order when creating an auto account. Overrides `DELTACHAT_CHATMAIL_SERVER` if set. |
| `DELTACHAT_DISPLAY_NAME` | `Hermes` | Display name shown to contacts. |
| `DELTACHAT_AVATAR_PATH` | — | Path to a bot avatar image (`.png`, `.jpg`, `.jpeg`, `.gif`, or `.webp`). |

### Access control

| Variable | Default | Description |
|----------|---------|-------------|
| `DELTACHAT_ALLOW_ALL_USERS` | `false` | If `true`, disables the global allowlist check. DM and group policies still apply. |
| `DELTACHAT_ALLOWED_USERS` | — | Comma-separated list of email addresses allowed to interact with the bot. Empty means "allow all" (subject to policies). |
| `DELTACHAT_DM_POLICY` | `pairing` | Direct-message policy: `open`, `allowlist`, `pairing`, or `disabled`. |
| `DELTACHAT_DM_ALLOWED_USERS` | — | Comma-separated allowlist used when `DM_POLICY=allowlist`. Overrides `DELTACHAT_ALLOWED_USERS` for DMs. |
| `DELTACHAT_GROUP_POLICY` | `open` | Group-chat policy: `open`, `allowlist`, or `disabled`. |
| `DELTACHAT_GROUP_ALLOWED_USERS` | — | Comma-separated allowlist used when `GROUP_POLICY=allowlist`. |
| `DELTACHAT_REQUIRE_MENTION` | `false` | If `true`, the bot only responds in groups when mentioned (`@DisplayName` or whole-word display name). |
| `DELTACHAT_SEND_REJECTION_REPLIES` | `true` | If `false`, the bot silently ignores rejected senders instead of sending an explanation. |

### Limits and tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `DELTACHAT_MAX_MESSAGE_LENGTH` | `3600` | Character limit for automatic message splitting. |
| `DELTACHAT_RATE_LIMIT_MAX` | `30` | Maximum inbound messages allowed per sender per window. |
| `DELTACHAT_RATE_LIMIT_WINDOW` | `60` | Rate-limiting window in seconds. |

### Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `DELTACHAT_HOME_CHANNEL` | — | Email address used by Hermes for cron/notification delivery. |

---

## Policy modes

### DM policies

- `open` — accept DMs from anyone.
- `allowlist` — accept DMs only from `DELTACHAT_DM_ALLOWED_USERS`.
- `pairing` — accept DMs only from verified contacts. This is the default and
  recommended secure mode. New contacts must scan the bot's SecureJoin QR code
  or use its invite link.
- `disabled` — reject all DMs.

### Group policies

- `open` — respond in any group the bot has joined.
- `allowlist` — respond only when the sender is in `DELTACHAT_GROUP_ALLOWED_USERS`.
- `disabled` — leave or ignore all group chats.

---

## Configuration examples

### Public chatmail bot

```bash
export DELTACHAT_EMAIL=auto
export DELTACHAT_CHATMAIL_SERVERS="chat.postblue.cz,chat.cqre.net"
export DELTACHAT_DISPLAY_NAME="Help Desk Bot"
export DELTACHAT_DM_POLICY=open
export DELTACHAT_GROUP_POLICY=open
export DELTACHAT_REQUIRE_MENTION=true
```

### Private internal bot

```bash
export DELTACHAT_EMAIL=bot@company.example
export DELTACHAT_PASSWORD="$BOT_EMAIL_PASSWORD"
export DELTACHAT_DISPLAY_NAME="Company Bot"
export DELTACHAT_ALLOWED_USERS="alice@company.example,bob@company.example"
export DELTACHAT_DM_POLICY=pairing
export DELTACHAT_GROUP_POLICY=allowlist
export DELTACHAT_GROUP_ALLOWED_USERS="alice@company.example,bob@company.example"
export DELTACHAT_SEND_REJECTION_REPLIES=false
```

### Development / testing bot

```bash
export DELTACHAT_EMAIL=auto
export DELTACHAT_DISPLAY_NAME="Dev Bot"
export DELTACHAT_ALLOW_ALL_USERS=true
export DELTACHAT_DM_POLICY=open
export DELTACHAT_GROUP_POLICY=open
export DELTACHAT_RATE_LIMIT_MAX=1000
```

---

## Validation

`validate_config()` is called by Hermes before the adapter starts. It enforces:

- `DELTACHAT_EMAIL` is either `auto` or a plain valid email address.
- A password is present for manual email accounts.
- `DELTACHAT_DM_POLICY` and `DELTACHAT_GROUP_POLICY` are valid values.
- `DELTACHAT_DATA_DIR` does not contain `..`.
- `DELTACHAT_AVATAR_PATH` has a supported image extension.
- A non-default `DELTACHAT_RPC_SERVER` path points to an executable file.
- `DELTACHAT_CHATMAIL_SERVERS` (if set) contains at least one valid hostname.

Full file-system and binary validation happens at runtime in `_run_dc_once()`.
