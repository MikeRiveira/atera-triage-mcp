# Security Policy

## Reporting a vulnerability

Please don't open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/MikeRiveira/atera-triage-mcp/security/advisories/new)
instead, and include steps to reproduce if you can. I'll reply within a few days.

## Supported versions

Security fixes go into the latest release.

## How this project stays safe

- Read-only by default. The one write tool (`set_ticket_status`) only exists when `ATERA_ALLOW_WRITES=true`.
- Your API key is read from the environment, sent only to Atera, and never included in errors or tool output.
- Contact details are removed and ticket text is redacted (phone, email, SSN, card numbers) before the AI sees it.
- Text written by end users is fenced as untrusted data, so a ticket can't instruct the AI.
- Dependabot keeps dependencies and GitHub Actions up to date, and CodeQL scans every change.
