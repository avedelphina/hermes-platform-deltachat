# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] - 2026-06-30

### Fixed
- Synchronized `plugin.yaml` version with the released git tags.
- Added this changelog.

## [1.1.0] - 2026-06-30

### Added
- Support for multiple fallback chatmail servers via `DELTACHAT_CHATMAIL_SERVERS`.
  The adapter tries each configured server in order when creating an auto account.

## [1.0.0] - 2026-06-30

### Added
- Initial hardened Delta Chat platform adapter release for Hermes Agent.
- Security: RPC server path validation, URL/size/content checks for images,
  document path validation, stricter email validation, data-directory lockdown.
- Operations: thread-safe shared state, graceful shutdown, retry with backoff,
  per-sender rate limiting, duplicate-message suppression, UTC timestamps,
  `get_status()` health/metrics snapshot.
- Configuration: `DELTACHAT_SEND_REJECTION_REPLIES`,
  `DELTACHAT_MAX_MESSAGE_LENGTH`, `DELTACHAT_RATE_LIMIT_MAX`,
  `DELTACHAT_RATE_LIMIT_WINDOW`, and whole-word `@Name` mention detection.
- Documentation: `README.md`, `docs/CONFIGURATION.md`, `docs/SECURITY.md`,
  `docs/OPERATIONS.md`, and `AGENTS.md`.
