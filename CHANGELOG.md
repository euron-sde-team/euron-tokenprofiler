# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

### Added

- **Core**: integer micro-dollar pricing with a 42-model price book and overrides; a calibrated token estimator; idempotent
  SQLite event store; whitelisted query layer; anomaly detection, month-end forecast, budgets; prompt analysis; the optimizer
  (8 rules, overlap-capped potential).
- **API**: `/v1/events` ingest, an OpenAI-compatible profiling proxy (chat, embeddings, streaming), analytics, insights, admin,
  scoped API keys with per-app allow-lists, webhook alerts with an SSRF guard, CSV/JSONL export, S3 report archive, Prometheus metrics.
- **SDK**: bounded-queue client with retries and idempotent ids; OpenAI and Anthropic wrappers.
- **Console**: overview, explorer, models, prompts, events, optimizer, prompt lab, budgets and alerts, settings; light and dark themes.
- **Infra**: Terraform modules and `local-floci`, `aws-dev`, `aws-prod` environments; Docker Compose stack on Floci.
- **Tests**: unit and integration, 49-check end-to-end, chaos, and Chromium browser suites.
- Initial repository: license, code of conduct, security policy and contribution guide.
