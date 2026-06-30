# Agent Notes: hermes-platform-deltachat

This repository is a **Delta Chat platform adapter** for the Hermes Agent
ecosystem. It lets a Hermes agent send and receive messages over Delta Chat,
which is an email-based, end-to-end encrypted messenger.

> Language: all code comments and docs are in English. This file is in English.

---

## Project overview

- **Name:** `hermes-platform-deltachat`
- **Kind:** Hermes platform plugin (`kind: platform` in `plugin.yaml`)
- **Main module:** `adapter.py`
- **Plugin manifest:** `plugin.yaml`
- **Dependencies:** `requirements.txt`
- **Tests:** `tests/test_adapter.py`
- **Entry point for Hermes:** `register(ctx)` in `adapter.py`
- **Human docs:** `README.md`, `docs/CONFIGURATION.md`, `docs/OPERATIONS.md`, `docs/SECURITY.md`

The adapter implements `BasePlatformAdapter` from the Hermes gateway and
bridges Hermes events to the Delta Chat RPC client. It can run with a
user-supplied email account or create a free "chatmail" account automatically.

### File layout

```
.
├── adapter.py           # DeltaChatAdapter class and plugin registration hooks
├── plugin.yaml          # Hermes plugin manifest (env vars, labels, description)
├── requirements.txt     # Runtime dependencies
├── __init__.py          # Empty package marker
├── README.md            # Human-readable project overview
├── AGENTS.md            # Agent-focused notes for coding assistants
├── tests/
│   ├── __init__.py      # Empty package marker
│   └── test_adapter.py  # unittest tests for pure logic
└── docs/
    ├── CONFIGURATION.md # Full environment-variable reference
    ├── OPERATIONS.md    # Deployment, monitoring, troubleshooting
    └── SECURITY.md      # Security best practices
```

There is **no** `pyproject.toml`, `setup.py`, `setup.cfg`, `package.json`,
`Cargo.toml`, or similar build manifest. This is a flat Python module loaded by
Hermes at runtime.

---

## Technology stack

- **Language:** Python 3 (developed/tested on Python 3.14)
- **Framework:** Hermes Agent platform adapter interface
  - Imports from `gateway.platforms.base` and `gateway.config` (provided by the
    Hermes gateway at runtime)
- **Delta Chat integration:**
  - `deltachat-rpc-client` Python package
  - `deltachat-rpc-server` binary on `PATH`
- **HTTP client for media downloads:** `httpx`
- **Testing:** `unittest` from the standard library

---

## Build and test commands

### Install runtime dependencies

```bash
pip install -r requirements.txt
pip install deltachat-rpc-server
# Verify the RPC server is available:
deltachat-rpc-server --version
```

`deltachat-rpc-server` is a binary package that must be installed separately and
available on `PATH`. The adapter will fail to connect if it is missing.
`check_requirements()` now verifies both the Python client import and the RPC
server binary.

### Run tests

The tests use only the standard library and do **not** require a running Delta
Chat RPC server or a Hermes gateway:

```bash
python3 -m unittest discover -v tests
```

You can also run the test file directly:

```bash
python3 -m unittest tests.test_adapter -v
```

All tests stub out the Hermes `gateway` imports with fake modules, so the
adapter can be imported and exercised in isolation.

---

## Runtime architecture

Hermes loads this plugin via `register(ctx)` and creates an adapter instance with
`DeltaChatAdapter(config)`.

### Lifecycle

1. `connect()` validates configuration, registers `SIGTERM`/`SIGINT` handlers
   for graceful shutdown, and spawns a daemon thread (`deltachat-event`) that
   runs the Delta Chat event loop.
2. Inside the thread, `_run_dc_once()`:
   - Resolves and locks down `DELTACHAT_DATA_DIR` (mode `0o700`).
   - Validates and starts `deltachat-rpc-server` through `Rpc(...)`.
   - Adds or reuses a Delta Chat account.
   - Configures the account (manual email/password or auto chatmail).
   - Registers a `NewMessage` hook.
   - Signals `connect()` that setup succeeded.
   - Runs `Client(account, hooks).run_until(...)` until disconnect.
3. On inbound messages, the hook schedules `asyncio` coroutines on the main
   event loop via `asyncio.run_coroutine_threadsafe()`.
4. `disconnect()` removes signal handlers, stops I/O, and joins the event
   thread.

### Thread safety

Shared runtime state (`_account`, `_rpc_ref`, `_invite_link`, `_stats`,
`_crash_times`) is guarded by a `threading.RLock`. Async send methods take a
snapshot of `_account` under the lock before performing I/O in the executor, so
sends do not hold the lock during blocking RPC calls.

### Crash recovery

The background thread loops in `_run_dc()`. If it crashes, it restarts after 5
seconds unless there have been 3 crashes within 60 seconds, in which case it
disables itself. A `get_status()` method exposes connection state, recent crash
counts, and counters for diagnostics.

### Message sending

- Long text is split at paragraph, line, sentence, or word boundaries using
  `_split_message()` with a configurable limit defaulting to
  `DC_MESSAGE_MAX_LEN = 3600` characters.
- `send_image()` downloads the image with `httpx`, validates the URL scheme,
  bounds the download size (25 MB), checks the `Content-Type`, writes it to a
  temp file, sends it, and cleans up. Transient failures are retried with
  exponential backoff.
- `send_document()` validates that the local file exists, resolves symlinks,
  enforces a 100 MB size limit, and sends it.
- `send_typing()` is a no-op because Delta Chat has no typing indicator.
- All send operations retry transient failures a small number of times.

### Inbound message handling

- Duplicate `message_id` values are dropped using a bounded LRU cache.
- Per-sender rate limiting is applied before policy checks.
- `DELTACHAT_REQUIRE_MENTION` now uses a whole-word / `@Name` regex instead of
  a simple substring match.
- Attachments larger than 50 MB are rejected.
- Event timestamps are UTC-aware.

### Chat IDs

- Direct messages use the sender's lowercased email address as the Hermes
  `chat_id`.
- Groups use `group:<delta_chat_chat_id>`. The integer part is validated.

---

## Configuration

Configuration is read from environment variables first, then from the Hermes
`PlatformConfig.extra` dict. All env vars are prefixed with `DELTACHAT_`.

### Required environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DELTACHAT_EMAIL` | `auto` | Bot email. Use `auto` to create a free chatmail account. |

### Optional environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DELTACHAT_PASSWORD` | — | Password when using a manual email account. |
| `DELTACHAT_DATA_DIR` | `~/.hermes/deltachat-data` | Account database directory. |
| `DELTACHAT_RPC_SERVER` | `deltachat-rpc-server` | Path to the RPC server binary. |
| `DELTACHAT_CHATMAIL_SERVER` | `nine.testrun.org` | Chatmail server for auto accounts. |
| `DELTACHAT_CHATMAIL_SERVERS` | — | Comma-separated list of chatmail servers to try in order. Overrides the single-server setting. |
| `DELTACHAT_DISPLAY_NAME` | `Hermes` | Bot display name. |
| `DELTACHAT_AVATAR_PATH` | — | Path to bot avatar image (PNG/JPEG/GIF/WebP). |
| `DELTACHAT_ALLOWED_USERS` | — | Comma-separated allowlist for all interactions. |
| `DELTACHAT_ALLOW_ALL_USERS` | `false` | Set `true` to disable allowlist checks. |
| `DELTACHAT_DM_POLICY` | `pairing` | `open`, `allowlist`, `pairing`, or `disabled`. |
| `DELTACHAT_DM_ALLOWED_USERS` | — | Comma-separated allowlist for DMs. |
| `DELTACHAT_GROUP_POLICY` | `open` | `open`, `allowlist`, or `disabled`. |
| `DELTACHAT_GROUP_ALLOWED_USERS` | — | Comma-separated allowlist for groups. |
| `DELTACHAT_REQUIRE_MENTION` | `false` | Set `true` to require a mention in groups. |
| `DELTACHAT_SEND_REJECTION_REPLIES` | `true` | Set `false` to stop sending rejection explanations. |
| `DELTACHAT_HOME_CHANNEL` | — | Email address for cron/notification delivery. |
| `DELTACHAT_MAX_MESSAGE_LENGTH` | `3600` | Character limit for automatic message splitting. |
| `DELTACHAT_RATE_LIMIT_MAX` | `30` | Max inbound messages per sender per window. |
| `DELTACHAT_RATE_LIMIT_WINDOW` | `60` | Rate-limit window in seconds. |

### Validation

`validate_config()` enforces:

- `DELTACHAT_EMAIL` must be `auto` or a plain valid email address.
- A password is required when using a manual email account.
- `DELTACHAT_DM_POLICY` must be one of `open`, `allowlist`, `pairing`, `disabled`.
- `DELTACHAT_GROUP_POLICY` must be one of `open`, `allowlist`, `disabled`.
- `DELTACHAT_DATA_DIR` must not contain `..`.
- `DELTACHAT_AVATAR_PATH` must have a supported image extension.
- A non-default `DELTACHAT_RPC_SERVER` path must point to an executable file.

---

## Code organization

- `adapter.py` is the only substantial source file.
- Helper functions live at module level: `_split_message`, `_is_valid_email`,
  `_parse_email_list`, `_get_chat`, `_safe_data_dir`, `_validate_rpc_server_path`,
  `_validate_avatar_path`, `_async_retry`, `check_requirements`.
- Internal utilities: `_RateLimiter`, `_MessageCache`.
- `DeltaChatAdapter` subclasses `BasePlatformAdapter` and overrides the Hermes
  lifecycle methods (`connect`, `disconnect`, `send`, `send_image`, etc.).
- Inbound message handling is in `DeltaChatAdapter._on_message()`.
- Policy checks are in `_check_dm()` and `_check_group()`.
- Mention detection is in `_is_mentioned()`.
- Health/metrics snapshot is in `get_status()`.
- Plugin registration hooks are at the bottom: `_env_enablement`,
  `validate_config`, `is_connected`, and `register`.

---

## Code style guidelines

- Use type hints (`from __future__ import annotations`).
- Use `logging.getLogger(__name__)` for logging.
- Keep helper functions small and pure where possible.
- Prefer explicit environment variable reads with `os.getenv` over complex
  config parsing.
- Follow the existing naming:
  - Module-level helpers are `_snake_case`.
  - Adapter methods are `snake_case`.
  - Environment variables are `DELTACHAT_SCREAMING_SNAKE_CASE`.

---

## Testing instructions

- Tests live in `tests/test_adapter.py`.
- Tests must not require `deltachat-rpc-server`, `deltachat-rpc-client`, or a
  Hermes gateway.
- The test file manually stubs `gateway.platforms.base` and `gateway.config`
  before importing `adapter`.
- When adding tests, keep them pure logic tests (splitting, validation,
  policies) or use lightweight fakes for Delta Chat objects.

Run the full suite before committing:

```bash
python3 -m unittest discover -v tests
```

---

## Security considerations

- **Data directory:** The adapter creates `DELTACHAT_DATA_DIR` with mode `0o700`
  and rejects paths containing `..`.
- **RPC server path:** A custom `DELTACHAT_RPC_SERVER` value is validated to
  point to an executable file, preventing arbitrary binary execution.
- **Allowlists:** Empty allowlists mean "deny none", but `DELTACHAT_DM_POLICY`
  and `DELTACHAT_GROUP_POLICY` still apply unless
  `DELTACHAT_ALLOW_ALL_USERS=true`.
- **DM policy `pairing`:** The bot only accepts direct messages from verified
  contacts. This is the default and the recommended secure mode.
- **Rejection replies:** Set `DELTACHAT_SEND_REJECTION_REPLIES=false` to avoid
  confirming the bot's existence to unauthorized senders.
- **Image downloads:** `send_image()` validates URL schemes, bounds download
  size, rejects non-image `Content-Type`, and does not follow redirects.
- **Document sending:** `send_document()` resolves symlinks, verifies the file
  exists, and enforces a 100 MB size limit.
- **Inbound attachments:** Attachments larger than 50 MB are rejected before
  being read into memory.
- **PII:** `pii_safe=False` is declared in the plugin registration. Email
  addresses and message content are considered sensitive.
- **Credentials:** Never commit `DELTACHAT_PASSWORD` or chatmail credentials to
  version control. Use environment variables or a secrets manager.
- **Auto accounts:** Chatmail accounts are created on the configured server;
  keys are stored in `DELTACHAT_DATA_DIR`.

---

## Deployment notes

- This project is a plugin, not a standalone application. It is loaded by the
  Hermes Agent gateway.
- Ensure `deltachat-rpc-server` is installed and on `PATH` in the deployment
  environment.
- Make sure `DELTACHAT_DATA_DIR` is a persistent volume so the account keys and
  state survive restarts.
- The adapter registers graceful shutdown handlers for `SIGTERM`/`SIGINT`; make
  sure the deployment environment gives the process enough time to shut down.
- The adapter supports automatic updates via the `allow_update_command=True`
  flag in `register()`.
