# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.x (latest minor) | Yes |
| Older minors | No |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository ("Security" tab, "Report a vulnerability").

Please include the affected component (`apps/api`, `packages/core`, `infra/terraform/...`) and version or commit,
a description of the impact, steps to reproduce, and any suggested mitigation.

| Stage | Target |
| ----- | ------ |
| Acknowledgement | within 3 business days |
| Initial assessment and severity | within 7 business days |
| Fix or mitigation for critical / high issues | within 30 days |
| Coordinated public disclosure | after a fix is released, credited to you unless you prefer otherwise |

## Scope

In scope: the code in this repository and the infrastructure it provisions with its default settings. Of particular
interest: the ingestion and proxy endpoints (injection, resource exhaustion), API-key handling, SSRF through the proxy's
upstream setting, and any way events from one application could be read or altered with another application's key.

Out of scope:

- **Vulnerabilities in LLM providers or SDKs** you proxy to.
- **Prompt content.** TokenProfiler stores token counts and metadata by default, not prompts. If you turn on prompt
  capture, that data is yours to protect.
- Findings that require a compromised host, a compromised AWS account, or leaked deployment secrets.
- Exposing the API to the internet without TLS and a key. It is a self-hosted service: you operate it.

## Hardening checklist for operators

- Put the API behind TLS and give every application its own scoped key (`TP_API_KEY_PEPPER` set to a long random value).
- Keep prompt capture off unless you need it, and restrict who holds the `read` scope.
- Back up the SQLite database, or archive reports to your own S3 bucket.
- Restrict `TP_CORS_ORIGINS` to your console origin.
