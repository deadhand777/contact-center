# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/en/1.0.0/)
and this project adheres to [Semantic Versioning](http://semver.org/spec/v2.0.0.html).

<!-- insertion marker -->
## Unreleased

### Fixed

- Guardrail refusals no longer become escalations. A Bedrock Guardrail replaces the blocked turn with its own plain-text message, which cannot satisfy the JSON response contract, so every compliance refusal was rejected and handed off to a human. The supervisor now detects the `guardrail_intervened` / `content_filtered` stop reason and delivers the guardrail's message as a non-escalating answer.

### Added

- Contract rejections are logged as a PII-safe `contract_rejected` record (cause and field names, no answer text). They were previously swallowed silently.

## [0.1.0](https://github.com/deadhand777/contact-center/releases/tag/0.1.0) - 2026-09-11

<small>[Compare with first commit](https://github.com/deadhand777/contact-center/compare/937f03bdec7ccb3004951c4dabe6a07ccf627cf6...0.1.0)</small>

### Changed

- Deepen the supervisor response contract (#6) ([0f5dc18](https://github.com/deadhand777/contact-center/commit/0f5dc183e9c87fd0cbcb45ebfdba3f4c620edec1) by Chris). Strict validation of `{answer, escalate, reason}`; malformed output now fails toward a human instead of reaching the customer.
- Refactor Connect conversation lifecycle (#5) ([8f37e67](https://github.com/deadhand777/contact-center/commit/8f37e673feec6c16a88fd17b089be5ab83f504d5) by Chris). `ConnectConversation` context manager and `TurnResult` replace the free-function session/polling API.
- Remove dead constants, duplicated data and docs (#3) ([a202fdd](https://github.com/deadhand777/contact-center/commit/a202fddf8b92233d3f1391445acba23a5656c986) by Chris).

### Build

- update project dependencies (#1) ([bbb059f](https://github.com/deadhand777/contact-center/commit/bbb059fd8e8f454e85993ea81dd5bea2bcbf8359) by Chris).

### Features

- agentic contact-center PoC (M1–M4) ([937f03b](https://github.com/deadhand777/contact-center/commit/937f03bdec7ccb3004951c4dabe6a07ccf627cf6) by deadhand777).
