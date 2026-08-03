# Security policy

## Supported versions

Security fixes are applied to the latest tagged release. Before `1.0`, pin an
exact tag or reviewed commit.

## Reporting a vulnerability

Do not open a public issue with tokens, OAuth codes, account identifiers,
private analytics, database files or exploit details.

Use GitHub's **Security → Report a vulnerability** flow when available. If
private vulnerability reporting is not enabled, contact the maintainer through
the GitHub profile and share only enough information to establish a private
reporting channel.

## Sensitive areas

- OAuth authorization and refresh flows;
- tokens persisted in SQLite metadata;
- Telegram bot tokens and chat allowlists;
- provider responses containing account identifiers;
- logs and exceptions around token refresh;
- report rendering that could produce unsafe links or markup;
- retry behavior that could consume platform quotas.

Use synthetic data for every reproduction.
