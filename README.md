# Hermes Delta Chat Platform Adapter

[![Python 3](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)

A [Hermes Agent](https://github.com/openclaw/hermes) platform adapter that lets
your agent send and receive messages over [Delta Chat](https://delta.chat/) —
an email-based, end-to-end encrypted messenger.

The adapter can run with a user-supplied email account or create a free
"chatmail" account automatically. You can configure a single chatmail server or
provide a fallback list of servers to try during onboarding.

---

## Features

- **Email-based messaging** over Delta Chat's E2E-encrypted transport.
- **Automatic chatmail account creation** — set `DELTACHAT_EMAIL=auto` and the
  adapter provisions a free account on the configured chatmail server.
- **Manual email account support** — bring your own email address and password.
- **Group chat support** via Delta Chat groups, with `group:<id>` chat IDs.
- **Media support** for images, audio, and documents.
- **Fine-grained access control** via allowlists and DM/group policies.
- **Rate limiting** and duplicate-message suppression** for robust inbound handling.
- **Automatic message splitting** for long replies.
- **Graceful shutdown** on `SIGTERM`/`SIGINT`.

---

## Requirements

- Python 3.12 or newer.
- `deltachat-rpc-client` Python package.
- `deltachat-rpc-server` binary on your `PATH`.
- A running Hermes Agent gateway (this repository is a plugin, not a standalone
  app).

---

## Installation

1. Install the Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Install the Delta Chat RPC server binary:

   ```bash
   pip install deltachat-rpc-server
   ```

3. Verify the server is available:

   ```bash
   deltachat-rpc-server --version
   ```

4. Make the plugin discoverable to Hermes (usually by placing the repository in
   the gateway's plugin directory or by configuring the plugin path).

---

## Quick start

### Option A: Automatic chatmail account

```bash
export DELTACHAT_EMAIL=auto
export DELTACHAT_DISPLAY_NAME="My Bot"
```

The adapter will create a free account on `nine.testrun.org` by default. You can
override the chatmail server with `DELTACHAT_CHATMAIL_SERVER`, or provide a list
of fallback servers with `DELTACHAT_CHATMAIL_SERVERS`:

```bash
export DELTACHAT_CHATMAIL_SERVERS="chat.postblue.cz,chat.cqre.net"
```

### Option B: Manual email account

```bash
export DELTACHAT_EMAIL=bot@example.com
export DELTACHAT_PASSWORD="your-app-password"
export DELTACHAT_DISPLAY_NAME="My Bot"
```

Use an app-specific password if your provider supports it.

### Option C: Run the test suite

The tests do not require a running Delta Chat server or Hermes gateway:

```bash
python3 -m unittest discover -v tests
```

---

## Configuration

All configuration values are read from environment variables first, then from
the Hermes `PlatformConfig.extra` dict. Environment variables take precedence.

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full reference,
including allowlist examples, policy modes, and rate-limiting options.

### Minimal configuration

```bash
export DELTACHAT_EMAIL=auto
export DELTACHAT_DISPLAY_NAME="Hermes Bot"
```

### Recommended secure configuration

```bash
export DELTACHAT_EMAIL=auto
export DELTACHAT_DISPLAY_NAME="Hermes Bot"
export DELTACHAT_DM_POLICY=pairing
export DELTACHAT_GROUP_POLICY=allowlist
export DELTACHAT_GROUP_ALLOWED_USERS="alice@example.com,bob@example.com"
export DELTACHAT_SEND_REJECTION_REPLIES=false
```

With `DM_POLICY=pairing`, the bot only accepts direct messages from verified
contacts. Use the invite link logged at startup (or the SecureJoin QR) to pair
new contacts.

---

## How it works

Hermes loads the plugin through the `register(ctx)` hook and creates a
`DeltaChatAdapter` instance. The adapter starts a background thread that:

1. Spawns `deltachat-rpc-server`.
2. Adds or reuses a Delta Chat account.
3. Configures the account (manual or chatmail).
4. Listens for `NewMessage` events.
5. Forwards allowed inbound messages to the Hermes pipeline.

Outgoing messages from the agent are sent back through the same account.

---

## Project layout

```
.
├── adapter.py           # Main adapter implementation
├── plugin.yaml          # Hermes plugin manifest
├── requirements.txt     # Python dependencies
├── tests/               # Unit tests
├── AGENTS.md            # Agent-focused notes for coding assistants
├── README.md            # This file
└── docs/                # Additional documentation
    ├── CONFIGURATION.md
    ├── OPERATIONS.md
    └── SECURITY.md
```

---

## Development

### Running tests

```bash
python3 -m unittest discover -v tests
```

The tests stub out the Hermes gateway imports, so the adapter can be tested in
isolation without `deltachat-rpc-server` running.

### Code style

- Use type hints (`from __future__ import annotations`).
- Keep helper functions small and testable.
- Prefer `os.getenv` for configuration reads.
- Use `logging.getLogger(__name__)` for logging.

---

## Security

See [docs/SECURITY.md](docs/SECURITY.md) for security considerations and best
practices.

Key points:

- The adapter stores account keys in `DELTACHAT_DATA_DIR`; keep that directory
  private (`0o700`) and persistent.
- Never commit email passwords or chatmail credentials to version control.
- Use `DELTACHAT_DM_POLICY=pairing` and verified contacts for sensitive bots.
- Set `DELTACHAT_SEND_REJECTION_REPLIES=false` to avoid confirming the bot's
  existence to unauthorized senders.

---

## Operations

See [docs/OPERATIONS.md](docs/OPERATIONS.md) for deployment, monitoring, and
troubleshooting guidance.

---

## License

This project is part of the Hermes Agent ecosystem. See the upstream Hermes
repository for license details.
