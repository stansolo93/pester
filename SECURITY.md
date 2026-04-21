# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.0.x   | Yes                |
| 0.5.x   | Security fixes only|
| < 0.5   | No                 |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public issue.**
2. Use [GitHub Security Advisories](https://github.com/stansolo93/pester/security/advisories/new) to report the vulnerability privately.

You should receive a response within 48 hours. We will work with you to understand the scope and develop a fix before any public disclosure.

## Scope

pester processes local files and optionally connects to external APIs (OpenAI, Anthropic, Groq, Google Drive, Telegram). Security concerns include:

- **Local file access:** pester reads and writes files within the vault directory and `~/.pester/` state directory.
- **API credentials:** API keys are read from environment variables, never stored in vault files.
- **Daemon:** The background daemon runs with the user's permissions and watches the vault directory.

## Best Practices

- Never commit API keys or credentials to your vault repository.
- Use environment variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`) for all secrets.
- Review `pester.yaml` before sharing your vault, as it may contain chat IDs or folder IDs.
